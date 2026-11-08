import base64
import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

import zhconv

from app.gemini_client import (
    ModelError,
    apply_review_policy,
    merge_variant_chunks,
    merge_variant_cores,
    missing_core_lines,
    missing_variant_lines,
    gloss_from_core_response,
    note_from_core_response,
    parse_feedback,
    parse_progress_score,
)
from app.prompts import (
    build_chunk_repair_prompt,
    build_core_gloss_prompt,
    build_core_note_prompt,
    build_core_repair_prompt,
    build_feedback_prompt,
    build_progress_prompt,
)

_MAX_AUDIO_B64 = 10_000_000


def chat_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    root = base[: -len("/api/v1")] if base.endswith("/api/v1") else base
    return root + "/compatible-mode/v1/chat/completions"


def to_simplified(text: str) -> str:
    return zhconv.convert(text or "", "zh-cn")


def transcript_from_qwen(payload: dict) -> str:
    choices = ((payload or {}).get("output") or {}).get("choices") or []
    if not choices:
        return ""
    content = ((choices[0] or {}).get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts).strip()
    return ""


def audio_url_from_qwen(payload: dict) -> str:
    audio = ((payload or {}).get("output") or {}).get("audio") or {}
    return str(audio.get("url") or "").strip()


def contains_cjk(text: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in text or "")


def usable_audio_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme in {"http", "https"} and (host == "aliyuncs.com" or host.endswith(".aliyuncs.com")):
        return parsed.geturl()
    return ""


def _friendly(status: int | None, text: str) -> str:
    if status == 429 or "Throttling" in text or "RateQuota" in text:
        return "千问这会儿请求太多。录音已留下，过一会儿点重试。"
    if status in (401, 403) or "InvalidApiKey" in text or "invalid_api_key" in text:
        return "千问 Key 不可用。检查 .env 里的 DASHSCOPE_API_KEY，保存后重启。"
    if status == 400:
        return "千问没有接受这次请求。录音已留下，可以点重试。"
    return "千问调用失败。录音已留下，可以点重试。"


class QwenClient:
    def __init__(
        self,
        api_key: str,
        feedback_model: str,
        asr_model: str,
        tts_model: str,
        tts_voice: str,
        base_url: str,
    ):
        self.api_key = api_key
        self.feedback_model = feedback_model
        self.asr_model = asr_model
        self.tts_model = tts_model
        self.tts_voice = tts_voice
        self.base_url = base_url.rstrip("/")

    def transcribe(self, data: bytes, mime: str) -> str:
        encoded = base64.b64encode(data).decode("ascii")
        if len(encoded) > _MAX_AUDIO_B64:
            raise ModelError("这段录音对千问来说太长了。可以再说短一点。")
        mime = (mime or "audio/webm").split(";", 1)[0].strip() or "audio/webm"
        payload = self._post(
            self.base_url + "/services/aigc/multimodal-generation/generation",
            {
                "model": self.asr_model,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"audio": f"data:{mime};base64,{encoded}"}],
                        }
                    ]
                },
                "parameters": {"asr_options": {"enable_itn": False}},
            },
        )
        text = transcript_from_qwen(payload)
        if not text:
            raise ModelError("没有听清。录音已留下，可以再说一次，或点重试。")
        return to_simplified(text)

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
        prompt = build_feedback_prompt(
            topic_en=topic_en,
            topic_zh=topic_zh,
            prompt_en=prompt_en,
            prompt_zh=prompt_zh,
            transcript=transcript,
            previous_natural=previous_natural,
        )
        payload = self._post(
            chat_url(self.base_url),
            {
                "model": self.feedback_model,
                "temperature": 0.3,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        choices = payload.get("choices") or []
        text = ""
        if choices:
            text = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip()
        if not text:
            raise ModelError("这次没有生成反馈。录音已留下，可以点重试。")
        try:
            parsed = parse_feedback(text)
        except ModelError:
            raise
        if contains_cjk(parsed.get("natural") or ""):
            repaired = self._translate_leftover_chinese(parsed, transcript)
            if repaired and not contains_cjk(repaired.get("natural") or ""):
                parsed = repaired
        self._repair_variant_chunks(parsed)
        self._repair_variant_cores(parsed)
        return apply_review_policy(parsed, transcript, previous_natural, previous_natural_zh)

    def _repair_variant_chunks(self, parsed: dict) -> None:
        missing = missing_variant_lines(parsed)
        if not missing:
            return
        try:
            payload = self._post(
                chat_url(self.base_url),
                {
                    "model": self.feedback_model,
                    "temperature": 0.2,
                    "enable_thinking": False,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": build_chunk_repair_prompt(missing)}],
                },
            )
        except ModelError:
            return
        choices = payload.get("choices") or []
        text = ""
        if choices:
            text = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip()
        if not text:
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return
        merge_variant_chunks(parsed, data)

    def _repair_variant_cores(self, parsed: dict) -> None:
        missing = missing_core_lines(parsed)
        if not missing:
            return
        try:
            payload = self._post(
                chat_url(self.base_url),
                {
                    "model": self.feedback_model,
                    "temperature": 0.2,
                    "enable_thinking": False,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": build_core_repair_prompt(missing)}],
                },
            )
        except ModelError:
            return
        choices = payload.get("choices") or []
        text = ""
        if choices:
            text = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip()
        if not text:
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return
        merge_variant_cores(parsed, data)

    def core_gloss(self, en: str, line: str) -> tuple[str, str]:
        payload = self._post(
            chat_url(self.base_url),
            {
                "model": self.feedback_model,
                "temperature": 0.2,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": build_core_gloss_prompt(en, line)}],
            },
        )
        choices = payload.get("choices") or []
        text = ""
        if choices:
            text = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip()
        return gloss_from_core_response(text)

    def core_note(self, en: str, usage: str, transcript: str) -> str:
        payload = self._post(
            chat_url(self.base_url),
            {
                "model": self.feedback_model,
                "temperature": 0.2,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": build_core_note_prompt(en=en, usage=usage, transcript=transcript)}],
            },
        )
        choices = payload.get("choices") or []
        text = ""
        if choices:
            text = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip()
        return note_from_core_response(text)

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
    ) -> dict:
        payload = self._post(
            chat_url(self.base_url),
            {
                "model": self.feedback_model,
                "temperature": 0.2,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "user",
                        "content": build_progress_prompt(
                            topic_en=topic_en,
                            topic_zh=topic_zh,
                            prompt_en=prompt_en,
                            prompt_zh=prompt_zh,
                            transcript=transcript,
                            duration_ms=duration_ms,
                            earlier=earlier,
                        ),
                    }
                ],
            },
        )
        choices = payload.get("choices") or []
        text = ""
        if choices:
            text = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip()
        return parse_progress_score(text, transcript)

    def _translate_leftover_chinese(self, parsed: dict, transcript: str) -> dict | None:
        payload = self._post(
            chat_url(self.base_url),
            {
                "model": self.feedback_model,
                "temperature": 0.2,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "The natural line still contains Chinese. "
                            "Translate each Chinese stretch into simple English with the same meaning. "
                            "Keep the English that is already there. "
                            "natural must not contain Chinese characters. "
                            "Add one changes item for each Chinese stretch you replace. "
                            "Return the full JSON object and nothing else.\n\n"
                            f"Verbatim answer:\n\"\"\"{transcript}\"\"\"\n\n"
                            "Current JSON:\n"
                            + json.dumps(parsed, ensure_ascii=False)
                        ),
                    }
                ],
            },
        )
        choices = payload.get("choices") or []
        text = ""
        if choices:
            text = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip()
        if not text:
            return None
        try:
            return parse_feedback(text)
        except ModelError:
            return None

    def speak(self, text: str) -> bytes:
        spoken = " ".join((text or "").split())
        if not spoken:
            raise ModelError("没有可以朗读的修正版。")
        if len(spoken) > 600:
            spoken = spoken[:600].rsplit(" ", 1)[0]
        payload = self._post(
            self.base_url + "/services/aigc/multimodal-generation/generation",
            {
                "model": self.tts_model,
                "input": {
                    "text": spoken,
                    "voice": self.tts_voice,
                    "language_type": "English",
                },
            },
        )
        url = usable_audio_url(audio_url_from_qwen(payload))
        if not url:
            raise ModelError("没有生成发音。修正版还在，可以稍后再试。")
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                audio = response.read()
        except Exception as exc:
            raise ModelError("没有生成发音。修正版还在，可以稍后再试。") from exc
        if not audio:
            raise ModelError("没有生成发音。修正版还在，可以稍后再试。")
        return audio

    def _post(self, url: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelError(_friendly(exc.code, detail)) from exc
        except Exception as exc:
            raise ModelError("千问调用失败。录音已留下，可以点重试。") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelError("千问没有按预期返回。录音已留下，可以点重试。") from exc
        if isinstance(data, dict) and data.get("code"):
            raise ModelError(_friendly(None, str(data.get("code"))))
        return data
