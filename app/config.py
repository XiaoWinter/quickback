import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _data_dir() -> Path:
    raw = os.getenv("QUICKBACK_DATA_DIR", "").strip()
    path = Path(raw) if raw else ROOT / "data" / "sessions"
    if not path.is_absolute():
        path = ROOT / path
    return path


class Settings:
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    transcribe_model = os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe").strip()
    feedback_model = os.getenv("GEMINI_FEEDBACK_MODEL", "gemini-3.6-flash").strip()
    speech_model = os.getenv("GEMINI_SPEECH_MODEL", "gemini-3.8-flash-tts").strip()
    speech_voice = os.getenv("GEMINI_SPEECH_VOICE", "Kore").strip() or "Kore"
    data_dir = _data_dir()
    host = os.getenv("QUICKBACK_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("QUICKBACK_PORT", "8765"))


settings = Settings()
