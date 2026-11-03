import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import ROOT, settings
from app.gemini_client import GeminiClient, GeminiError
from app.questions import QuestionError, create_question, delete_question, get_question, load_questions, next_question, update_question
from app.segments import split_transcript
from app.storage import NotFound, SessionStore, normalize_mime

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("quickback")

MAX_AUDIO_BYTES = 12 * 1024 * 1024
MIN_AUDIO_BYTES = 400

app = FastAPI(title="Quickback")
store = SessionStore(settings.data_dir)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def _client() -> GeminiClient:
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
    return {
        "ok": True,
        "has_key": bool(settings.gemini_api_key),
        "transcribe_model": settings.transcribe_model,
        "feedback_model": settings.feedback_model,
    }


@app.get("/api/questions")
def questions_list():
    try:
        return load_questions()
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
    return {"ok": True}


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


@app.post("/api/sessions/{session_id}/turns/{index}/speech")
async def create_speech(session_id: str, index: int):
    import asyncio

    try:
        session = store.get(session_id)
        turn = next(item for item in session["turns"] if item["index"] == index)
    except (NotFound, StopIteration) as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    if turn.get("speech_file"):
        return session
    natural = str((turn.get("feedback") or {}).get("natural") or "").strip()
    if not natural:
        raise HTTPException(status_code=400, detail="还没有地道版，不能生成发音。")
    if not settings.gemini_api_key:
        raise HTTPException(status_code=400, detail="还没有填写 Gemini Key。写进 .env 后重启。")
    try:
        audio = await asyncio.to_thread(_client().speak, natural)
    except GeminiError as exc:
        log.info("speech %s/%s failed: %s", session_id, index, exc)
        detail = str(exc).replace("录音已留下，可以点重试。", "稍后再打开这条记录。")
        detail = detail.replace("录音已留下，过一会儿点重试。", "稍后再打开这条记录。")
        raise HTTPException(status_code=502, detail=detail) from exc
    log.info("speech %s/%s saved %s bytes", session_id, index, len(audio))
    return store.save_speech(session_id, index, audio)


@app.get("/api/sessions/{session_id}/speech/{index}")
def get_speech(session_id: str, index: int):
    try:
        path, mime = store.speech_path(session_id, index)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="还没有这段发音。") from exc
    return FileResponse(path, media_type=mime)


@app.get("/api/sessions/{session_id}/audio/{index}")
def get_audio(session_id: str, index: int):
    try:
        path, mime = store.audio_path(session_id, index)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="找不到这段录音。") from exc
    return FileResponse(path, media_type=mime)


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
    _session, index = store.append_audio(session_id, data, mime, duration_ms)
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

    if not settings.gemini_api_key:
        return store.update_turn(
            session_id,
            index,
            status="error",
            error="还没有填写 Gemini Key。写进 .env 后重启，再点重试。",
        )

    audio_path = Path(store.audio_path(session_id, index)[0])
    mime = turn["mime_type"]
    transcript = ""
    try:
        client = _client()
        transcript = await asyncio.to_thread(client.transcribe, audio_path.read_bytes(), mime)
        previous_natural = ""
        for earlier in session["turns"]:
            if earlier["index"] >= index:
                continue
            earlier_natural = str((earlier.get("feedback") or {}).get("natural") or "").strip()
            if earlier_natural:
                previous_natural = earlier_natural
        feedback = await asyncio.to_thread(
            client.feedback,
            topic_en=session["question"]["en"],
            topic_zh=session["question"]["zh"],
            prompt_en=turn["prompt"]["en"],
            prompt_zh=turn["prompt"]["zh"],
            transcript=transcript,
            previous_natural=previous_natural,
        )
    except GeminiError as exc:
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
