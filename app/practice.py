import json
import threading
from pathlib import Path

from app.config import ROOT

STATE_PATH = ROOT / "data" / "state.json"


class PracticeStore:
    def __init__(self, path: Path | None = None):
        self.path = path or STATE_PATH
        self._lock = threading.Lock()

    def read(self) -> dict:
        with self._lock:
            return self._read()

    def write(self, question_id: str = "", session_id: str = "") -> dict:
        data = {"question_id": question_id or "", "session_id": session_id or ""}
        with self._lock:
            self._write(data)
        return data

    def forget_question(self, question_id: str) -> None:
        with self._lock:
            current = self._read()
            if current["question_id"] == question_id and not current["session_id"]:
                self._write({"question_id": "", "session_id": ""})

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"question_id": "", "session_id": ""}
        if not isinstance(raw, dict):
            return {"question_id": "", "session_id": ""}
        return {
            "question_id": str(raw.get("question_id") or ""),
            "session_id": str(raw.get("session_id") or ""),
        }

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)
