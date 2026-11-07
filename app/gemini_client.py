import base64
import difflib
import json
import logging
import re

from app.prompts import build_feedback_prompt

log = logging.getLogger("quickback")
_FEEDBACK_FALLBACKS = ("gemini-3.6-flash", "gemini-3.5-flash-lite")

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_VARIANT_LEVELS = ("simple", "professional", "native")


class ModelError(Exception):
    pass


class GeminiError(ModelError):
    pass


def parse_feedback(text: str) -> dict:
    raw = _FENCE.sub("", (text or "").strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeminiError("反馈没有按 JSON 返回，可以点重试。") from exc
    if not isinstance(data, dict):
        raise GeminiError("反馈没有按 JSON 返回，可以点重试。")
    natural = data.get("natural")
    if not isinstance(natural, str) or not natural.strip():
        raise GeminiError("这次没有生成修正版，可以点重试。")

    changes = []
    for item in data.get("changes") or []:
        if not isinstance(item, dict):
            continue
        spoken = str(item.get("spoken") or "").strip()
        better = str(item.get("better") or "").strip()
        note = str(item.get("note") or "").strip()
        if spoken and better:
            changes.append({"spoken": spoken, "better": better, "note": note})

    extensions = []
    for item in data.get("extensions") or []:
        if isinstance(item, str):
            line, note = item.strip(), ""
        elif isinstance(item, dict):
            line = str(item.get("line") or "").strip()
            note = str(item.get("note") or "").strip()
        else:
            continue
        if line:
            extensions.append({"line": line, "note": note})

    review = data.get("review")
    if not isinstance(review, str):
        review = ""
    return {
        "natural": natural.strip(),
        "intent": str(data.get("intent") or "").strip()[:200],
        "natural_zh": str(data.get("natural_zh") or "").strip()[:500],
        "spoken_zh": str(data.get("spoken_zh") or "").strip()[:500],
        "changes": changes[:12],
        "chunks": _chunks_from(data.get("chunks"), natural.strip()),
        "variants": _variants(data),
        "extensions": extensions[:3],
        "follow_up": str(data.get("follow_up") or "").strip(),
        "follow_up_zh": str(data.get("follow_up_zh") or "").strip(),
        "review": review.strip()[:500],
    }


def _variants(data: dict) -> list[dict]:
    raw = data.get("variants")
    found: dict[str, dict] = {}
    if isinstance(raw, dict):
        for key in _VARIANT_LEVELS:
            item = raw.get(key)
            if isinstance(item, dict):
                found[key] = item
            elif isinstance(item, str):
                found[key] = {"en": item}
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key in _VARIANT_LEVELS:
                found[key] = item
    items = []
    for key in _VARIANT_LEVELS:
        item = found.get(key)
        if not item:
            continue
        en = str(item.get("en") or item.get("line") or "").strip()[:800]
        if not en:
            continue
        items.append({
            "key": key,
            "en": en,
            "zh": str(item.get("zh") or "").strip()[:500],
            "chunks": _chunks_from(item.get("chunks"), en),
        })
    return items


_CHUNK_ROLES = {
    "flow": "flow",
    "flow_initiator": "flow",
    "pivot": "pivot",
    "logic_pivot": "pivot",
    "refactor": "refactor",
    "intent_refactor": "refactor",
}


def _chunk_role(item: dict) -> str:
    raw = str(item.get("role") or item.get("intent") or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _CHUNK_ROLES.get(raw, "")


def _chunks_from(raw, source: str) -> list[dict]:
    source_norm = _normalize_answer(source)
    if not source_norm or not isinstance(raw, list):
        return []
    items = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = _chunk_role(item)
        en = str(item.get("en") or "").replace("…", "...").strip()
        if not role or not en or re.search(r"[\u3400-\u9fff]", en):
            continue
        core = en.replace("...", " ")
        core_norm = _normalize_answer(core)
        words = core_norm.split()
        if len(words) < 2 or len(words) > 5 or core_norm not in source_norm or core_norm == source_norm or core_norm in seen:
            continue
        seen.add(core_norm)
        items.append({"en": en[:80], "role": role})
        if len(items) >= 4:
            break
    return items


def _normalize_answer(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", text)
    return " ".join(text.split())


def answers_match(left: str, right: str) -> bool:
    a = _normalize_answer(left)
    b = _normalize_answer(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer and len(shorter) / len(longer) >= 0.9:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.92


def apply_review_policy(
    feedback: dict,
    transcript: str,
    previous_natural: str = "",
    previous_natural_zh: str = "",
) -> dict:
    """Drop the critique when the answer is already good, and don't rewrite a suggested answer again."""
    previous = (previous_natural or "").strip()
    if previous and answers_match(transcript, previous):
        feedback["natural"] = previous
        feedback["natural_zh"] = (previous_natural_zh or "").strip()
        feedback["changes"] = []
        feedback["review"] = ""
    transcript_words = _normalize_answer(transcript)
    kept = []
    for change in feedback.get("changes") or []:
        spoken = _normalize_answer(str(change.get("spoken") or ""))
        if spoken and spoken in transcript_words:
            kept.append(change)
    feedback["changes"] = kept
    if answers_match(str(feedback.get("natural") or ""), transcript):
        feedback["changes"] = []
    return feedback


def _clean_transcript(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^(transcript|verbatim)\s*:\s*", "", text, flags=re.IGNORECASE)
    return text.strip().strip('"')


def _friendly(exc: Exception) -> str:
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc)
    if status == 429 or "RESOURCE_EXHAUSTED" in text or " 429" in text:
        return "Gemini 免费额度暂时用完了，或请求太密。录音已留下，过一会儿点重试。"
    if status == 503 or "UNAVAILABLE" in text or "high demand" in text:
        return "Gemini 这会儿忙。录音已留下，过一会儿点重试。"
    if status in (401, 403) or "API_KEY_INVALID" in text or "API key" in text:
        return "Gemini Key 不可用。检查 .env 里的 GEMINI_API_KEY，保存后重启。"
    if status == 400:
        return "Gemini 没有接受这次请求。录音已留下，可以点重试。"
    return "Gemini 调用失败。录音已留下，可以点重试。"


def _sdk():
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise GeminiError("没有安装 google-genai。在项目目录运行 run.ps1。") from exc
    return genai, types


def _audio_part(types, data: bytes, mime: str):
    if hasattr(types.Part, "from_bytes"):
        return types.Part.from_bytes(data=data, mime_type=mime)
    return types.Part(inline_data=types.Blob(data=data, mime_type=mime))


def _unavailable(exc: Exception) -> bool:
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc)
    return status == 503 or "UNAVAILABLE" in text or "high demand" in text


def _join_transcript_chunks(chunks: list[str]) -> str:
    pieces = [chunk for chunk in chunks if chunk and chunk.strip()]
    if not pieces:
        return ""
    merged = pieces[0]
    for chunk in pieces[1:]:
        if merged[-1].isspace() or chunk[0].isspace():
            merged += chunk
        else:
            merged += " " + chunk
    return merged.strip()


def transcript_from_response(response) -> str:
    """Read a transcribe-model response. The words live on audio_transcription, not text."""
    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            transcription = getattr(part, "audio_transcription", None)
            if transcription is not None:
                value = getattr(transcription, "text", None) or ""
                if not value.strip():
                    words = []
                    for word in getattr(transcription, "words", None) or []:
                        token = word.get("word", "") if isinstance(word, dict) else getattr(word, "word", "")
                        if token:
                            words.append(token)
                    value = " ".join(words)
                if value.strip():
                    chunks.append(value)
                continue
            value = getattr(part, "text", None)
            if value and value.strip():
                chunks.append(value)
    if chunks:
        return _join_transcript_chunks(chunks)
    return (getattr(response, "text", None) or "").strip()


def wav_from_response(response) -> bytes:
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is None:
                continue
            data = getattr(inline, "data", None) or b""
            if isinstance(data, str):
                data = base64.b64decode(data)
            if data.startswith(b"RIFF"):
                return data
    raise GeminiError("没有生成发音。修正版还在，可以稍后再试。")


def _response_text(response) -> str:
    text = getattr(response, "text", None)
    if text and text.strip():
        return text.strip()
    parts = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            value = getattr(part, "text", None)
            if value:
                parts.append(value)
    return "\n".join(parts).strip()


class GeminiClient:
    def __init__(self, api_key: str, transcribe_model: str, feedback_model: str, speech_model: str, speech_voice: str):
        self.api_key = api_key
        self.transcribe_model = transcribe_model
        self.feedback_model = feedback_model
        self.speech_model = speech_model
        self.speech_voice = speech_voice
        self._client = None

    def _client_or_create(self):
        if self._client is None:
            genai, _types = _sdk()
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def transcribe(self, data: bytes, mime: str) -> str:
        _genai, types = _sdk()
        client = self._client_or_create()
        part = _audio_part(types, data, mime)
        config = None
        if hasattr(types, "AudioTranscriptionConfig"):
            try:
                config = types.GenerateContentConfig(
                    audio_transcription_config=types.AudioTranscriptionConfig(
                        mode="VERBATIM",
                        language_codes=["en-US", "zh-CN"],
                    )
                )
            except TypeError:
                config = None
        try:
            response = client.models.generate_content(
                model=self.transcribe_model,
                contents=[part],
                config=config,
            )
        except Exception as exc:
            raise GeminiError(_friendly(exc)) from exc
        text = _clean_transcript(transcript_from_response(response))
        if not text:
            raise GeminiError("没有听清。录音已留下，可以再说一次，或点重试。")
        return text

    def feedback(
        self,
        *,
        topic_en: str,
        topic_zh: str,
        prompt_en: str,
        prompt_zh: str,
        transcript: str,
        previous_natural: str = "",
        previous_natural_zh: str = "",
    ) -> dict:
        _genai, types = _sdk()
        client = self._client_or_create()
        prompt = build_feedback_prompt(
            topic_en=topic_en,
            topic_zh=topic_zh,
            prompt_en=prompt_en,
            prompt_zh=prompt_zh,
            transcript=transcript,
            previous_natural=previous_natural,
        )
        models = [self.feedback_model]
        for name in _FEEDBACK_FALLBACKS:
            if name not in models:
                models.append(name)
        last_error = None
        for model in models:
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.3,
                        response_mime_type="application/json",
                    ),
                )
            except Exception as exc:
                last_error = exc
                if _unavailable(exc) and model != models[-1]:
                    log.info("feedback model %s busy, trying the next one", model)
                    continue
                raise GeminiError(_friendly(exc)) from exc
            text = _response_text(response)
            if not text:
                raise GeminiError("这次没有生成反馈。录音已留下，可以点重试。")
            return apply_review_policy(
                parse_feedback(text),
                transcript,
                previous_natural,
                previous_natural_zh,
            )
        raise GeminiError(_friendly(last_error) if last_error else "这次没有生成反馈。录音已留下，可以点重试。")

    def speak(self, text: str) -> bytes:
        spoken = " ".join((text or "").split())
        if not spoken:
            raise GeminiError("没有可以朗读的修正版。")
        if len(spoken) > 800:
            spoken = spoken[:800].rsplit(" ", 1)[0]
        _genai, types = _sdk()
        client = self._client_or_create()
        try:
            response = client.models.generate_content(
                model=self.speech_model,
                contents=spoken,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        language_code="en",
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=self.speech_voice
                            )
                        ),
                    ),
                ),
            )
        except Exception as exc:
            raise GeminiError(_friendly(exc)) from exc
        return wav_from_response(response)
