import base64
import difflib
import json
import logging
import re

import zhconv

from app.prompts import (
    build_chunk_repair_prompt,
    build_core_gloss_prompt,
    build_core_note_prompt,
    build_core_repair_prompt,
    build_feedback_prompt,
    build_progress_prompt,
)

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
        chunks = _chunks_from(item.get("chunks"), en)
        items.append({
            "key": key,
            "en": en,
            "zh": str(item.get("zh") or "").strip()[:500],
            "chunks": chunks,
            "cores": _cores_from(item.get("cores"), en, chunks),
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


_CHUNK_REPAIR_LEVELS = ("professional", "native")


def missing_variant_lines(feedback: dict) -> list[tuple[str, str]]:
    lines = []
    for item in feedback.get("variants") or []:
        key = item.get("key")
        en = str(item.get("en") or "").strip()
        if key in _CHUNK_REPAIR_LEVELS and en and not item.get("chunks"):
            lines.append((key, en))
    return lines


_FRAME_MARKERS = {
    "but": "pivot",
    "because": "pivot",
    "though": "pivot",
    "so": "pivot",
    "honestly": "refactor",
    "actually": "refactor",
}
_FRAME_LEADING = {"and"}
_FRAME_FOLLOW = {"i", "they", "they're", "we", "it", "my", "me", "you", "he", "she", "not", "consistently"}


def _token_key(token: str) -> str:
    return (token or "").lower().replace("’", "'")


def _frame_tokens(text: str) -> list[tuple[str, int, int]]:
    return [
        (match.group(), match.start(), match.end())
        for match in re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)?", text or "")
    ]


def _short_slice(source: str, en: str) -> str:
    words = (en or "").split()
    if not words:
        return ""
    limit = 3 if len(words) > 3 else len(words)
    if limit == 3 and _token_key(words[2]) in {"a", "an", "the", "my"}:
        limit = 2
    wanted = _normalize_answer(" ".join(words[:limit]))
    tokens = _frame_tokens(source)
    normalized = [_normalize_answer(token) for token, _start, _end in tokens]
    size = len(wanted.split())
    for index in range(len(tokens) - size + 1):
        if normalized[index:index + size] == wanted.split():
            start = tokens[index][1]
            end = tokens[index + size - 1][2]
            return source[start:end]
    return ""


def _marker_slices(source: str) -> list[tuple[int, str, str]]:
    tokens = _frame_tokens(source)
    found = []
    for index, (token, start, end) in enumerate(tokens):
        role = _FRAME_MARKERS.get(_token_key(token))
        if not role:
            continue
        if index > 0 and _token_key(tokens[index - 1][0]) in _FRAME_LEADING:
            start = tokens[index - 1][1]
        if index + 1 < len(tokens) and _token_key(tokens[index + 1][0]) in _FRAME_FOLLOW:
            end = tokens[index + 1][2]
        found.append((start, source[start:end], role))
    return found


def _refine_frames(source: str, chunks: list[dict]) -> list[dict]:
    kept = []
    for chunk in chunks:
        short = _short_slice(source, chunk.get("en") or "")
        if not short:
            continue
        role = chunk.get("role") if chunk.get("role") in {"flow", "pivot", "refactor"} else "flow"
        kept.append((source.lower().find(short.lower()), short, role))
    flows = [item for item in kept if item[2] == "flow"][:2]
    spans = flows + [item for item in kept if item[2] != "flow"]
    for start, text, role in _marker_slices(source):
        if any(text.lower() in item[1].lower() or item[1].lower() in text.lower() for item in spans):
            continue
        spans.append((start, text, role))
    spans.sort(key=lambda item: item[0])
    while len(spans) > 4:
        flow_at = next((index for index in range(len(spans) - 1, -1, -1) if spans[index][2] == "flow"), None)
        spans.pop(flow_at if flow_at is not None else -1)
    refined = [{"en": text, "role": role} for _start, text, role in spans]
    return _chunks_from(refined, source)


_CORE_LEVELS = ("professional", "native")


def missing_core_lines(feedback: dict) -> list[tuple[str, str]]:
    lines = []
    for item in feedback.get("variants") or []:
        key = item.get("key")
        en = str(item.get("en") or "").strip()
        if key in _CORE_LEVELS and en and not item.get("cores"):
            lines.append((key, en))
    return lines


def _exact_phrase(source: str, en: str) -> str:
    wanted = _normalize_answer(en).split()
    if len(wanted) < 2 or len(wanted) > 6:
        return ""
    tokens = _frame_tokens(source)
    normalized = [_normalize_answer(token) for token, _start, _end in tokens]
    size = len(wanted)
    for index in range(len(tokens) - size + 1):
        if normalized[index:index + size] == wanted:
            start = tokens[index][1]
            end = tokens[index + size - 1][2]
            return source[start:end]
    return ""


def _cores_from(raw, source: str, chunks: list | None = None) -> list[dict]:
    source_norm = _normalize_answer(source)
    if not source_norm or not isinstance(raw, list):
        return []
    blocked = set()
    for chunk in chunks or []:
        if isinstance(chunk, dict):
            blocked.add(_normalize_answer(str(chunk.get("en") or "")))
    found = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        usage = str(item.get("usage") or "").strip()
        cue = str(item.get("cue") or "").strip()
        if not usage or not cue:
            continue
        if not re.search(r"[\u3400-\u9fff]", usage) or not re.search(r"[\u3400-\u9fff]", cue):
            continue
        phrase = _exact_phrase(source, str(item.get("en") or ""))
        if not phrase:
            continue
        key = _normalize_answer(phrase)
        if not key or key in seen or key in blocked or key == source_norm:
            continue
        seen.add(key)
        found.append((source.lower().find(phrase.lower()), {
            "en": phrase[:80],
            "usage": usage[:180],
            "cue": cue[:120],
        }))
    found.sort(key=lambda item: item[0] if item[0] >= 0 else 10**9)
    return [item for _at, item in found[:3]]


def merge_variant_cores(feedback: dict, raw: dict) -> dict:
    """Fill native and professional core lists that came back empty."""
    if not isinstance(raw, dict):
        return feedback
    for item in feedback.get("variants") or []:
        if item.get("cores"):
            continue
        key = item.get("key")
        if key not in _CORE_LEVELS:
            continue
        source = raw.get(key)
        if isinstance(source, dict):
            source = source.get("cores")
        filled = _cores_from(source, item.get("en") or "", item.get("chunks") or [])
        if filled:
            item["cores"] = filled
    return feedback


def core_was_used(phrase: str, transcript: str) -> bool:
    needle = _normalize_answer(phrase)
    hay = _normalize_answer(transcript)
    return bool(needle) and needle in hay


def gloss_from_core_response(text: str) -> tuple[str, str]:
    raw = _FENCE.sub("", (text or "").strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    usage = _clip_zh(zhconv.convert(str(data.get("usage") or ""), "zh-cn"), 180)
    cue = _clip_zh(zhconv.convert(str(data.get("cue") or ""), "zh-cn"), 120)
    if not _ZH.search(usage) or not _ZH.search(cue):
        return "", ""
    return usage, cue


def _clip_zh(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    at = max(cut.rfind(mark) for mark in ("。", "！", "？", "，"))
    if at >= 12:
        return cut[: at + 1]
    return cut


_ZH = re.compile(r"[\u3400-\u9fff]")


def note_from_core_response(text: str) -> str:
    raw = _FENCE.sub("", (text or "").strip())
    note = ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        note = str(data.get("note") or "").strip()
    elif raw and not raw.startswith("{"):
        note = raw
    return note[:240]


def _progress_fixes(raw, transcript: str) -> list[dict]:
    if not isinstance(raw, list):
        return []
    source = _normalize_answer(transcript)
    source_words = source.split()
    found = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        spoken = " ".join(str(item.get("spoken") or "").split())
        better = " ".join(str(item.get("better") or "").split())
        spoken_norm = _normalize_answer(spoken)
        better_norm = _normalize_answer(better)
        words = spoken_norm.split()
        if (
            not spoken_norm
            or not better_norm
            or not re.search(r"[A-Za-z]", better)
            or not words
            or len(words) > 12
            or len(better_norm.split()) > 16
            or spoken_norm not in source
            or spoken_norm == better_norm
            or spoken_norm in seen
        ):
            continue
        if spoken_norm == source and len(source_words) > 12:
            continue
        seen.add(spoken_norm)
        note = _clip_zh(zhconv.convert(str(item.get("note") or ""), "zh-cn"), 120)
        if not _ZH.search(note):
            note = ""
        found.append({"spoken": spoken[:120], "better": better[:160], "note": note})
        if len(found) >= 3:
            break
    return found


def parse_progress_score(text: str, transcript: str = "") -> dict:
    raw = _FENCE.sub("", (text or "").strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelError("这次没评上分，可以点重试。") from exc
    if not isinstance(data, dict):
        raise ModelError("这次没评上分，可以点重试。")

    def score(value) -> int:
        if isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip()):
            value = float(value.strip())
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ModelError("这次没评上分，可以点重试。")
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise ModelError("这次没评上分，可以点重试。")
        rounded = int(number + 0.5) if number >= 0 else 0
        return max(0, min(100, rounded))

    keys = ("comprehensibility", "relevance", "fluency")
    scores = {key: score(data.get(key)) for key in keys}
    notes = {}
    for key in keys:
        note = _clip_zh(zhconv.convert(str(data.get(f"{key}_note") or ""), "zh-cn"), 160)
        notes[key] = note if _ZH.search(note) else ""
    if data.get("content") not in (None, ""):
        scores["content"] = score(data.get("content"))
        content_note = _clip_zh(zhconv.convert(str(data.get("content_note") or ""), "zh-cn"), 160)
        notes["content"] = content_note if _ZH.search(content_note) else ""
    follow = " ".join(str(data.get("follow_up") or "").split())
    if not re.search(r"[A-Za-z]", follow):
        follow = ""
    follow_zh = _clip_zh(zhconv.convert(str(data.get("follow_up_zh") or ""), "zh-cn"), 160)
    if not _ZH.search(follow_zh):
        follow_zh = ""
    natural = re.sub(r"[\u3400-\u9fff]+", " ", str(data.get("natural") or ""))
    natural = " ".join(natural.split())
    if not re.search(r"[A-Za-z]", natural):
        natural = ""
    natural_zh = _clip_zh(zhconv.convert(str(data.get("natural_zh") or ""), "zh-cn"), 800)
    if not _ZH.search(natural_zh):
        natural_zh = ""
    return {
        "scores": scores,
        "notes": notes,
        "fixes": _progress_fixes(data.get("fixes"), transcript),
        "follow_up": follow[:240],
        "follow_up_zh": follow_zh,
        "natural": natural[:800],
        "natural_zh": natural_zh,
    }


def merge_variant_chunks(feedback: dict, raw: dict) -> dict:
    """Fill variant chunk lists that came back empty. Leave lists that already have frames."""
    if not isinstance(raw, dict):
        return feedback
    for item in feedback.get("variants") or []:
        if item.get("chunks"):
            continue
        key = item.get("key")
        source = raw.get(key)
        if isinstance(source, dict):
            source = source.get("chunks")
        filled = _refine_frames(item.get("en") or "", _chunks_from(source, item.get("en") or ""))
        if filled:
            item["chunks"] = filled
    return feedback


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

    def _repair_variant_chunks(self, parsed: dict) -> None:
        missing = missing_variant_lines(parsed)
        if not missing:
            return
        _genai, types = _sdk()
        client = self._client_or_create()
        try:
            response = client.models.generate_content(
                model=self.feedback_model,
                contents=build_chunk_repair_prompt(missing),
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
        except Exception:
            return
        text = _response_text(response)
        if not text:
            return
        try:
            data = json.loads(_FENCE.sub("", text))
        except json.JSONDecodeError:
            return
        merge_variant_chunks(parsed, data)

    def _repair_variant_cores(self, parsed: dict) -> None:
        missing = missing_core_lines(parsed)
        if not missing:
            return
        _genai, types = _sdk()
        client = self._client_or_create()
        try:
            response = client.models.generate_content(
                model=self.feedback_model,
                contents=build_core_repair_prompt(missing),
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
        except Exception:
            return
        text = _response_text(response)
        if not text:
            return
        try:
            data = json.loads(_FENCE.sub("", text))
        except json.JSONDecodeError:
            return
        merge_variant_cores(parsed, data)

    def core_gloss(self, en: str, line: str) -> tuple[str, str]:
        _genai, types = _sdk()
        client = self._client_or_create()
        try:
            response = client.models.generate_content(
                model=self.feedback_model,
                contents=build_core_gloss_prompt(en, line),
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:
            raise GeminiError(_friendly(exc)) from exc
        return gloss_from_core_response(_response_text(response))

    def core_note(self, en: str, usage: str, transcript: str) -> str:
        _genai, types = _sdk()
        client = self._client_or_create()
        try:
            response = client.models.generate_content(
                model=self.feedback_model,
                contents=build_core_note_prompt(en=en, usage=usage, transcript=transcript),
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:
            raise GeminiError(_friendly(exc)) from exc
        return note_from_core_response(_response_text(response))

    def progress_score(
        self,
        *,
        topic_en: str,
        topic_zh: str,
        prompt_en: str,
        prompt_zh: str,
        transcript: str,
        duration_ms: int | None,
        earlier: list[tuple[str, str]],
        reference: bytes | None = None,
        reference_mime: str = "audio/wav",
    ) -> dict:
        _genai, types = _sdk()
        client = self._client_or_create()
        prompt = build_progress_prompt(
            topic_en=topic_en,
            topic_zh=topic_zh,
            prompt_en=prompt_en,
            prompt_zh=prompt_zh,
            transcript=transcript,
            duration_ms=duration_ms,
            earlier=earlier,
            reference=bool(reference),
        )
        contents: list = [prompt]
        if reference:
            contents.append(_audio_part(types, reference, reference_mime or "audio/wav"))
        try:
            response = client.models.generate_content(
                model=self.feedback_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:
            raise GeminiError(_friendly(exc)) from exc
        return parse_progress_score(_response_text(response), transcript)

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
            parsed = parse_feedback(text)
            self._repair_variant_chunks(parsed)
            self._repair_variant_cores(parsed)
            return apply_review_policy(
                parsed,
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
