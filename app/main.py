import logging
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import ROOT, settings
from app.gemini_client import GeminiClient, ModelError
from app.practice import PracticeStore
from app.providers import CHOICES, providers
from app.qwen_client import QwenClient
from app.questions import (
    QuestionError,
    backfill_timestamps,
    create_question,
    delete_question,
    get_question,
    next_question,
    update_question,
)
from app.segments import split_transcript
from app.storage import NotFound, SessionStore, StorageError, normalize_mime

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("quickback")

MAX_AUDIO_BYTES = 12 * 1024 * 1024
MIN_AUDIO_BYTES = 400
_speech_locks: dict[tuple[str, int], object] = {}
_speech_lock_guard = threading.Lock()


def _turn_speech_lock(session_id: str, index: int):
    import asyncio

    key = (session_id, index)
    with _speech_lock_guard:
        lock = _speech_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _speech_locks[key] = lock
        return lock

app = FastAPI(title="Quickback")
store = SessionStore(settings.data_dir)
practice = PracticeStore()
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.on_event("startup")
def prepare_questions() -> None:
    try:
        backfill_timestamps()
    except QuestionError as exc:
        log.warning("%s", exc)


def _has_key(name: str) -> bool:
    if name == "qwen":
        return bool(settings.qwen_api_key)
    return bool(settings.gemini_api_key)


def _provider_view() -> dict:
    current = providers.read()
    return {
        "provider": current,
        "has_key": _has_key(current),
        "providers": [
            {"id": "gemini", "label": "Gemini", "has_key": bool(settings.gemini_api_key)},
            {"id": "qwen", "label": "千问", "has_key": bool(settings.qwen_api_key)},
        ],
    }


def _client() -> GeminiClient | QwenClient:
    name = providers.read()
    if name == "qwen":
        if not settings.qwen_api_key:
            raise ModelError("还没有填写千问 Key。写进 .env 的 DASHSCOPE_API_KEY，保存后重启。")
        return QwenClient(
            api_key=settings.qwen_api_key,
            feedback_model=settings.qwen_feedback_model,
            asr_model=settings.qwen_asr_model,
            tts_model=settings.qwen_tts_model,
            tts_voice=settings.qwen_tts_voice,
            base_url=settings.qwen_base_url,
        )
    if not settings.gemini_api_key:
        raise ModelError("还没有填写 Gemini Key。写进 .env 后重启，再点重试。")
    return GeminiClient(
        api_key=settings.gemini_api_key,
        transcribe_model=settings.transcribe_model,
        feedback_model=settings.feedback_model,
        speech_model=settings.speech_model,
        speech_voice=settings.speech_voice,
    )


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
def health():
    current = _provider_view()
    return {
        "ok": True,
        "provider": current["provider"],
        "has_key": current["has_key"],
        "providers": current["providers"],
    }


@app.get("/api/provider")
def provider_get():
    return _provider_view()


@app.put("/api/provider")
def provider_put(payload: dict):
    name = str(payload.get("provider") or "").strip().lower()
    if name not in CHOICES:
        raise HTTPException(status_code=400, detail="只能选 Gemini 或千问。")
    providers.write(name)
    return _provider_view()


@app.get("/api/questions")
def questions_list():
    try:
        return backfill_timestamps()
    except QuestionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/questions")
def questions_create(payload: dict):
    try:
        return create_question(payload.get("en"), payload.get("zh"))
    except QuestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/questions/{question_id}")
def questions_update(question_id: str, payload: dict):
    try:
        return update_question(question_id, payload.get("en"), payload.get("zh"))
    except QuestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/questions/{question_id}")
def questions_remove(question_id: str):
    try:
        delete_question(question_id)
    except QuestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    practice.forget_question(question_id)
    return {"ok": True}


def _practice_view() -> dict:
    current = practice.read()
    if current["session_id"]:
        try:
            return {"question": None, "session": store.get(current["session_id"])}
        except NotFound:
            practice.write(question_id=current["question_id"], session_id="")
            current["session_id"] = ""
    if current["question_id"]:
        try:
            return {"question": get_question(current["question_id"]), "session": None}
        except QuestionError:
            practice.write()
    latest = store.list_summaries()
    if latest:
        session_id = latest[0]["id"]
        session = store.get(session_id)
        practice.write(question_id=session["question"]["id"], session_id=session_id)
        return {"question": None, "session": session}
    try:
        question = next_question()
    except QuestionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    practice.write(question_id=question["id"])
    return {"question": question, "session": None}


@app.get("/api/practice")
def practice_get():
    return _practice_view()


@app.put("/api/practice")
def practice_put(payload: dict):
    session_id = _blank(str(payload.get("session_id") or ""))
    question_id = _blank(str(payload.get("question_id") or ""))
    if session_id:
        try:
            session = store.get(session_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="找不到这条记录。") from exc
        practice.write(question_id=session["question"]["id"], session_id=session_id)
        return {"question": None, "session": session}
    if question_id:
        try:
            question = get_question(question_id)
        except QuestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        practice.write(question_id=question["id"], session_id="")
        return {"question": question, "session": None}
    raise HTTPException(status_code=400, detail="要指定题目或记录。")


@app.get("/api/questions/next")
def question_next(exclude: str | None = None):
    try:
        return next_question(_blank(exclude))
    except QuestionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/sessions")
def list_sessions():
    return store.list_summaries()


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    try:
        return store.get(session_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这条记录。") from exc


@app.put("/api/sessions/{session_id}/question")
def session_question(session_id: str, payload: dict):
    try:
        session = store.update_question_text(session_id, payload.get("en"), payload.get("zh"))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这条记录。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    question = session["question"]
    try:
        update_question(question["id"], question["en"], question["zh"])
    except QuestionError:
        pass
    return session


@app.put("/api/sessions/{session_id}/follow-up")
def session_follow_up(session_id: str, payload: dict):
    try:
        return store.update_follow_up(session_id, payload.get("en"), payload.get("zh"))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这条记录。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/sessions/{session_id}/turns/{index}")
def session_turn_update(session_id: str, index: int, payload: dict):
    try:
        natural = payload.get("natural") if "natural" in payload else None
        return store.update_answered_turn(
            session_id,
            index,
            payload.get("en"),
            payload.get("zh"),
            natural,
            payload.get("natural_zh"),
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/sessions/{session_id}/turns/{index}")
def session_turn_delete(session_id: str, index: int):
    try:
        return store.delete_turn(session_id, index)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/sessions/{session_id}/follow-up")
def session_clear_follow_up(session_id: str):
    try:
        return store.clear_follow_up(session_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这条记录。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/{session_id}/turns/{index}/speech")
async def create_speech(session_id: str, index: int):
    import asyncio

    async with _turn_speech_lock(session_id, index):
        try:
            saved = store.keep_existing_speech(session_id, index)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
        if saved is not None:
            return saved
        try:
            session = store.get(session_id)
            turn = next(item for item in session["turns"] if item["index"] == index)
        except (NotFound, StopIteration) as exc:
            raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
        natural = str((turn.get("feedback") or {}).get("natural") or "").strip()
        if not natural:
            raise HTTPException(status_code=400, detail="还没有地道版，不能生成发音。")
        try:
            client = _client()
        except ModelError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            audio = await asyncio.to_thread(client.speak, natural)
        except ModelError as exc:
            log.info("speech %s/%s failed: %s", session_id, index, exc)
            detail = str(exc).replace("录音已留下，可以点重试。", "稍后再打开这条记录。")
            detail = detail.replace("录音已留下，过一会儿点重试。", "稍后再打开这条记录。")
            raise HTTPException(status_code=502, detail=detail) from exc
        log.info("speech %s/%s saved %s bytes", session_id, index, len(audio))
        return store.save_speech(session_id, index, audio, natural)


@app.get("/api/sessions/{session_id}/speech/{index}")
def get_speech(session_id: str, index: int):
    try:
        path, mime = store.speech_path(session_id, index)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="还没有这段发音。") from exc
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-cache"})


@app.get("/api/sessions/{session_id}/audio/{index}")
def get_audio(session_id: str, index: int):
    try:
        path, mime = store.audio_path(session_id, index)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-cache"})


@app.post("/api/turns")
async def create_turn(
    audio: UploadFile = File(...),
    session_id: str | None = Form(None),
    question_id: str | None = Form(None),
    mime_type: str | None = Form(None),
    duration_ms: int | None = Form(None),
):
    data = await audio.read()
    if len(data) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="录音太短了，再说长一点。")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="这段录音太长了，控制在 3 分钟内。")

    session_id = _blank(session_id)
    question_id = _blank(question_id)
    try:
        if session_id:
            session = store.get(session_id)
        else:
            if not question_id:
                raise HTTPException(status_code=400, detail="还没有题目。")
            session = store.create(get_question(question_id))
            session_id = session["id"]
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这条记录。") from exc
    except QuestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    mime = normalize_mime(mime_type or audio.content_type)
    saved, index = store.append_audio(session_id, data, mime, duration_ms)
    practice.write(question_id=saved["question"]["id"], session_id=session_id)
    return await _process(session_id, index)


@app.post("/api/sessions/{session_id}/turns/{index}/retake")
async def retake_turn(
    session_id: str,
    index: int,
    audio: UploadFile = File(...),
    mime_type: str | None = Form(None),
    duration_ms: int | None = Form(None),
):
    data = await audio.read()
    if len(data) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="录音太短了，再说长一点。")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="这段录音太长了，控制在 3 分钟内。")
    mime = normalize_mime(mime_type or audio.content_type)
    try:
        store.replace_audio(session_id, index, data, mime, duration_ms)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _process(session_id, index)


@app.post("/api/sessions/{session_id}/turns/{index}/retry")
async def retry_turn(session_id: str, index: int):
    try:
        store.get(session_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这条记录。") from exc
    return await _process(session_id, index)


async def _process(session_id: str, index: int) -> dict:
    import asyncio

    try:
        session = store.get(session_id)
        turn = next(item for item in session["turns"] if item["index"] == index)
    except (NotFound, StopIteration) as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc

    try:
        client = _client()
    except ModelError as exc:
        return store.update_turn(session_id, index, status="error", error=str(exc))

    audio_path = Path(store.audio_path(session_id, index)[0])
    mime = turn["mime_type"]
    transcript = ""
    try:
        transcript = await asyncio.to_thread(client.transcribe, audio_path.read_bytes(), mime)
        previous_natural = ""
        previous_natural_zh = ""
        for earlier in session["turns"]:
            if earlier["index"] >= index:
                continue
            earlier_feedback = earlier.get("feedback") or {}
            earlier_natural = str(earlier_feedback.get("natural") or "").strip()
            if earlier_natural:
                previous_natural = earlier_natural
                previous_natural_zh = str(earlier_feedback.get("natural_zh") or "").strip()
        feedback = await asyncio.to_thread(
            client.feedback,
            topic_en=session["question"]["en"],
            topic_zh=session["question"]["zh"],
            prompt_en=turn["prompt"]["en"],
            prompt_zh=turn["prompt"]["zh"],
            transcript=transcript,
            previous_natural=previous_natural,
            previous_natural_zh=previous_natural_zh,
        )
    except ModelError as exc:
        log.info("turn %s/%s failed: %s", session_id, index, exc)
        fields = {"status": "error", "error": str(exc)}
        if transcript:
            fields["transcript"] = transcript
            fields["segments"] = split_transcript(transcript)
            fields["error"] = "转写已经出来了，反馈这会儿没生成。可以点重试。"
        return store.update_turn(session_id, index, **fields)
    except Exception:
        log.exception("turn %s/%s failed", session_id, index)
        fields = {"status": "error", "error": "处理中断了。录音已留下，可以点重试。"}
        if transcript:
            fields["transcript"] = transcript
            fields["segments"] = split_transcript(transcript)
        return store.update_turn(session_id, index, **fields)

    return store.update_turn(
        session_id,
        index,
        status="ready",
        error=None,
        transcript=transcript,
        segments=split_transcript(transcript),
        feedback=feedback,
    )
