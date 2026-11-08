import logging
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import ROOT, settings
from app.gemini_client import GeminiClient, ModelError, core_was_used
from app.practice import PracticeStore
from app.progress import ProgressStore
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


def _turn_speech_lock(session_id: str, index: int, level: str = ""):
    import asyncio

    key = (session_id, index, level)
    with _speech_lock_guard:
        lock = _speech_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _speech_locks[key] = lock
        return lock

app = FastAPI(title="Quickback")
store = SessionStore(settings.data_dir)
practice = PracticeStore()
progress = ProgressStore(settings.data_dir.parent / "progress")
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


@app.get("/api/cert")
def download_ca_cert():
    path = ROOT / "certs" / "quickback-ca.cer"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="还没有证书。在电脑上运行 issue-cert.ps1。")
    return FileResponse(
        path,
        media_type="application/x-x509-ca-cert",
        filename="quickback-ca.cer",
        headers={"Cache-Control": "no-cache"},
    )


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
        questions = backfill_timestamps()
    except QuestionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    states = store.latest_states()
    charts = progress.charts()
    for item in questions:
        state = states.get(item["id"]) or {}
        item["closed"] = bool(state.get("closed"))
        item["session_id"] = state.get("session_id") or ""
        item["progress"] = charts.get(item["id"]) or []
    return questions


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
    store.delete_for_question(question_id)
    practice.forget_question(question_id)
    progress.delete_question(question_id)
    return {"ok": True}


def _progress_page(question_id: str) -> dict:
    try:
        question = get_question(question_id)
    except QuestionError as exc:
        raise HTTPException(status_code=404, detail="没有这道题。") from exc
    return progress.page(question)


@app.get("/api/questions/{question_id}/progress")
def progress_page(question_id: str):
    return _progress_page(question_id)


@app.post("/api/questions/{question_id}/progress")
def progress_start(question_id: str):
    try:
        question = get_question(question_id)
        progress.open_run(question)
    except QuestionError as exc:
        raise HTTPException(status_code=404, detail="没有这道题。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return progress.page(question)


def _progress_question_page(question_id: str) -> dict:
    return _progress_page(question_id)


@app.post("/api/progress/{run_id}/drafts")
def progress_draft_add(run_id: str, payload: dict):
    try:
        question_id = progress.add_draft(run_id, (payload or {}).get("text"))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这次进展。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _progress_question_page(question_id)


@app.delete("/api/progress/{run_id}/drafts/{index}")
def progress_draft_remove(run_id: str, index: int):
    try:
        question_id = progress.remove_draft(run_id, index)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这次进展。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _progress_question_page(question_id)


@app.post("/api/progress/{run_id}/answers")
async def progress_answer(
    run_id: str,
    audio: UploadFile = File(...),
    mime_type: str | None = Form(None),
    duration_ms: int | None = Form(None),
):
    import asyncio

    data = await audio.read()
    if len(data) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="录音太短了，再说长一点。")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="这段录音太长了，控制在 3 分钟内。")
    mime = normalize_mime(mime_type or audio.content_type)
    try:
        question_id = progress.save_pending(run_id, data, mime, duration_ms)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这次进展。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _score_progress(run_id, question_id)


@app.post("/api/progress/{run_id}/retry")
async def progress_retry(run_id: str):
    try:
        question_id = progress.scoring_context(run_id)["question_id"]
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这次进展。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _score_progress(run_id, question_id)


@app.post("/api/progress/{run_id}/redo")
def progress_redo(run_id: str):
    try:
        question_id = progress.clear_pending(run_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这次进展。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _progress_question_page(question_id)


@app.post("/api/progress/{run_id}/finish")
def progress_finish(run_id: str):
    try:
        question_id = progress.finish(run_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这次进展。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _progress_question_page(question_id)


@app.delete("/api/progress/{run_id}")
def progress_discard(run_id: str):
    try:
        question_id = progress.discard(run_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这次进展。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _progress_question_page(question_id)


@app.get("/api/progress/{run_id}/audio/{number}")
def progress_audio(run_id: str, number: int):
    try:
        path, mime = progress.audio_path(run_id, number)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-cache"})


async def _score_progress(run_id: str, question_id: str) -> dict:
    import asyncio

    try:
        client = _client()
        ctx = progress.scoring_context(run_id)
    except ModelError as exc:
        try:
            progress.fail_pending(run_id, str(exc))
        except (NotFound, StorageError):
            pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这次进展。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    transcript = ctx["transcript"]
    try:
        if not transcript:
            transcript = await asyncio.to_thread(client.transcribe, ctx["audio"], ctx["mime"])
        if not str(transcript or "").strip():
            progress.fail_pending(run_id, "没听清，再说一次。")
            return _progress_question_page(question_id)
        parsed = await asyncio.to_thread(
            client.progress_score,
            topic_en=ctx["topic_en"],
            topic_zh=ctx["topic_zh"],
            prompt_en=ctx["prompt_en"],
            prompt_zh=ctx["prompt_zh"],
            transcript=transcript,
            duration_ms=ctx["duration_ms"],
            earlier=ctx["earlier"],
        )
        progress.commit_answer(run_id, transcript, parsed)
    except ModelError as exc:
        log.info("progress %s failed: %s", run_id, exc)
        try:
            progress.fail_pending(run_id, str(exc), transcript or "")
        except (NotFound, StorageError):
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _progress_question_page(question_id)
    except (NotFound, StorageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("progress %s failed", run_id)
        try:
            progress.fail_pending(run_id, "评分中断了，可以点重试。", transcript or "")
        except (NotFound, StorageError):
            raise HTTPException(status_code=502, detail="评分中断了，可以点重试。") from exc
        return _progress_question_page(question_id)
    return _progress_question_page(question_id)


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
        existing = store.latest_for_question(question["id"])
        if existing:
            practice.write(question_id=question["id"], session_id=existing["id"])
            return {"question": None, "session": existing}
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


@app.post("/api/sessions/{session_id}/reopen")
def session_reopen(session_id: str):
    try:
        return store.reopen_practice(session_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这条记录。") from exc


@app.post("/api/sessions/{session_id}/finish")
def session_finish(session_id: str):
    try:
        return store.finish_practice(session_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这条记录。") from exc
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
            raise HTTPException(status_code=400, detail="还没有修正版，不能生成发音。")
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


@app.post("/api/sessions/{session_id}/turns/{index}/speech/{level}")
async def create_variant_speech(session_id: str, index: int, level: str):
    import asyncio

    async with _turn_speech_lock(session_id, index, level):
        try:
            saved = store.keep_existing_variant_speech(session_id, index, level)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
        except StorageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if saved is not None:
            return saved
        try:
            session = store.get(session_id)
            turn = next(item for item in session["turns"] if item["index"] == index)
        except (NotFound, StopIteration) as exc:
            raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
        item = next(
            (
                variant
                for variant in ((turn.get("feedback") or {}).get("variants") or [])
                if isinstance(variant, dict) and variant.get("key") == level
            ),
            None,
        )
        text = str((item or {}).get("en") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="还没有这句表达，不能生成发音。")
        try:
            client = _client()
        except ModelError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            audio = await asyncio.to_thread(client.speak, text)
        except ModelError as exc:
            log.info("speech %s/%s/%s failed: %s", session_id, index, level, exc)
            detail = str(exc).replace("录音已留下，可以点重试。", "稍后再打开这条记录。")
            detail = detail.replace("录音已留下，过一会儿点重试。", "稍后再打开这条记录。")
            raise HTTPException(status_code=502, detail=detail) from exc
        log.info("speech %s/%s/%s saved %s bytes", session_id, index, level, len(audio))
        return store.save_variant_speech(session_id, index, level, audio, text)


@app.get("/api/sessions/{session_id}/speech/{index}/{level}")
def get_variant_speech(session_id: str, index: int, level: str):
    try:
        path, mime = store.variant_speech_path(session_id, index, level)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="还没有这段发音。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-cache"})


@app.get("/api/sessions/{session_id}/speech/{index}")
def get_speech(session_id: str, index: int):
    try:
        path, mime = store.speech_path(session_id, index)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="还没有这段发音。") from exc
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-cache"})


@app.post("/api/sessions/{session_id}/turns/{index}/cores/{level}")
async def add_core(session_id: str, index: int, level: str, payload: dict):
    import asyncio

    try:
        en, line = store.prepare_core(session_id, index, level, str(payload.get("en") or ""))
        client = _client()
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except (StorageError, ModelError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        usage, cue = await asyncio.to_thread(client.core_gloss, en, line)
    except ModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not usage or not cue:
        raise HTTPException(status_code=502, detail="用法没写好，再试一次。")
    try:
        return store.add_core(session_id, index, level, en, usage, cue)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/sessions/{session_id}/turns/{index}/cores/{level}/{number}")
def remove_core(session_id: str, index: int, level: str, number: int):
    try:
        return store.delete_core(session_id, index, level, number)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/{session_id}/turns/{index}/cores/{level}/{number}/{slot}")
async def practice_core(
    session_id: str,
    index: int,
    level: str,
    number: int,
    slot: int,
    audio: UploadFile = File(...),
    mime_type: str | None = Form(None),
):
    import asyncio

    data = await audio.read()
    if len(data) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="录音太短了，再说长一点。")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="这段录音太长了，控制在 3 分钟内。")
    mime = normalize_mime(mime_type or audio.content_type)
    try:
        phrase = store.core_phrase(session_id, index, level, number)
        client = _client()
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except (StorageError, ModelError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        transcript = await asyncio.to_thread(client.transcribe, data, mime)
    except ModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if core_was_used(phrase["en"], transcript):
        try:
            note = await asyncio.to_thread(client.core_note, phrase["en"], phrase["usage"], transcript)
        except ModelError:
            note = "用上了。"
        if not note:
            note = "用上了。"
    else:
        note = f"这句里没有用上「{phrase['en']}」。按用法再说一句。"
    try:
        return store.save_core_attempt(session_id, index, level, number, slot, transcript, note)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/{session_id}/turns/{index}/retell")
async def save_retell(
    session_id: str,
    index: int,
    audio: UploadFile = File(...),
    mime_type: str | None = Form(None),
):
    data = await audio.read()
    if len(data) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="录音太短了，再说长一点。")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="这段录音太长了，控制在 3 分钟内。")
    mime = normalize_mime(mime_type or audio.content_type)
    try:
        return store.save_retell(session_id, index, data, mime)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _read_retell_upload(data: bytes, mime: str):
    if len(data) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="录音太短了，再说长一点。")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="这段录音太长了，控制在 3 分钟内。")
    return mime


@app.post("/api/sessions/{session_id}/turns/{index}/retell/{scope}/chunks/{number}")
async def save_chunk_retell(
    session_id: str,
    index: int,
    scope: str,
    number: int,
    audio: UploadFile = File(...),
    mime_type: str | None = Form(None),
):
    data = await audio.read()
    mime = _read_retell_upload(data, normalize_mime(mime_type or audio.content_type))
    try:
        return store.save_chunk_retell(session_id, index, scope, number, data, mime)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/sessions/{session_id}/turns/{index}/retell/{scope}/chunks/{number}")
def delete_chunk_retell(session_id: str, index: int, scope: str, number: int):
    try:
        return store.drop_chunk_retell(session_id, index, scope, number)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="还没有这段跟读。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/sessions/{session_id}/retell/{index}/{scope}/chunks/{number}")
def get_chunk_retell(session_id: str, index: int, scope: str, number: int):
    try:
        path, mime = store.chunk_retell_path(session_id, index, scope, number)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="还没有这段跟读。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-cache"})


@app.post("/api/sessions/{session_id}/turns/{index}/retell/{level}")
async def save_variant_retell(
    session_id: str,
    index: int,
    level: str,
    audio: UploadFile = File(...),
    mime_type: str | None = Form(None),
):
    data = await audio.read()
    if len(data) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="录音太短了，再说长一点。")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="这段录音太长了，控制在 3 分钟内。")
    mime = normalize_mime(mime_type or audio.content_type)
    try:
        return store.save_variant_retell(session_id, index, level, data, mime)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/sessions/{session_id}/turns/{index}/retell/{level}")
def delete_variant_retell(session_id: str, index: int, level: str):
    try:
        return store.drop_variant_retell(session_id, index, level)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="还没有这段跟读。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/sessions/{session_id}/retell/{index}/{level}")
def get_variant_retell(session_id: str, index: int, level: str):
    try:
        path, mime = store.variant_retell_path(session_id, index, level)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="还没有这段跟读。") from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-cache"})


@app.delete("/api/sessions/{session_id}/turns/{index}/retell")
def delete_retell(session_id: str, index: int):
    try:
        return store.drop_retell(session_id, index)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="还没有这段跟读。") from exc


@app.get("/api/sessions/{session_id}/retell/{index}")
def get_retell(session_id: str, index: int):
    try:
        path, mime = store.retell_path(session_id, index)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="还没有这段复述。") from exc
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-cache"})


@app.get("/api/questions/{question_id}/echoes")
def question_echoes(question_id: str):
    try:
        get_question(question_id)
    except QuestionError as exc:
        raise HTTPException(status_code=404, detail="找不到这道题。") from exc
    return store.echoes_for_question(question_id)


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
