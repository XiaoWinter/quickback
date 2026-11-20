import json
import threading
from pathlib import Path

from app.config import ROOT, settings

CHOICES = ("gemini", "qwen")
LABELS = {"gemini": "Gemini", "qwen": "千问"}
PATH = ROOT / "data" / "provider.json"


def provider_label(name: str) -> str:
    key = str(name or "").strip().lower()
    return LABELS.get(key, key or "")


class ProviderStore:
    def __init__(self, path: Path | None = None, default: str | None = None):
        self.path = path or PATH
        self.default = default if default in CHOICES else "gemini"
        self._lock = threading.Lock()

    def read(self) -> str:
        with self._lock:
            return self._read()

    def write(self, name: str) -> str:
        if name not in CHOICES:
            raise ValueError(name)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"provider": name}, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            return name

    def _read(self) -> str:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        name = str((raw or {}).get("provider") or self.default)
        return name if name in CHOICES else self.default


providers = ProviderStore(default=settings.provider if settings.provider in CHOICES else "gemini")
