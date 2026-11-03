import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

_ID = re.compile(r"^[a-f0-9]{32}$")
_AUDIO = re.compile(r"^turn-\d{2}\.[A-Za-z0-9]{1,5}$")
_SPEECH = re.compile(r"^turn-\d{2}-speech\.wav$")

EXT_BY_MIME = {
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/ogg": ".ogg",
    "audio/x-m4a": ".m4a",
}


class StorageError(Exception):
    pass


class NotFound(StorageError):
    pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_mime(mime: str | None) -> str:
    base = (mime or "").split(";", 1)[0].strip().lower()
    return base or "application/octet-stream"


class SessionStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def create(self, question: dict) -> dict:
        session = {
            "id": uuid.uuid4().hex,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "question": {
                "id": question["id"],
                "en": question["en"],
                "zh": question["zh"],
            },
            "pending_prompt": {"en": question["en"], "zh": question["zh"]},
            "turns": [],
        }
        with self._lock:
            self._write(session)
        return session

    def get(self, session_id: str) -> dict:
        with self._lock:
            return self._read(session_id)

    def list_summaries(self) -> list[dict]:
        summaries = []
        with self._lock:
            if not self.root.exists():
                return []
            for folder in self.root.iterdir():
                path = folder / "session.json"
                if not path.is_file():
                    continue
                try:
                    session = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                turns = session.get("turns") or []
                last_text = ""
                if turns:
                    last_text = (turns[-1].get("transcript") or "").strip()
                question = session.get("question") or {}
                summaries.append(
                    {
                        "id": session.get("id"),
                        "created_at": session.get("created_at"),
                        "updated_at": session.get("updated_at"),
                        "question": {
                            "id": question.get("id", ""),
                            "en": question.get("en", ""),
                            "zh": question.get("zh", ""),
                        },
                        "turn_count": len(turns),
                        "preview": last_text[:80],
                    }
                )
        summaries.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return summaries

    def append_audio(
        self,
        session_id: str,
        data: bytes,
        mime: str,
        duration_ms: int | None,
    ) -> tuple[dict, int]:
        mime = normalize_mime(mime)
        ext = EXT_BY_MIME.get(mime, ".bin")
        with self._lock:
            session = self._read(session_id)
            index = len(session["turns"]) + 1
            filename = f"turn-{index:02d}{ext}"
            (self._dir(session_id) / filename).write_bytes(data)
            prompt = session.get("pending_prompt") or session["question"]
            session["turns"].append(
                {
                    "index": index,
                    "created_at": now_iso(),
                    "duration_ms": duration_ms,
                    "prompt": {"en": prompt.get("en", ""), "zh": prompt.get("zh", "")},
                    "audio_file": filename,
                    "mime_type": mime,
                    "transcript": "",
                    "segments": [],
                    "feedback": None,
                    "status": "saved",
                    "error": None,
                }
            )
            self._write(session)
            return session, index

    def update_turn(self, session_id: str, index: int, **fields) -> dict:
        with self._lock:
            session = self._read(session_id)
            turn = self._turn(session, index)
            turn.update(fields)
            feedback = turn.get("feedback") or {}
            follow = str(feedback.get("follow_up") or "").strip()
            follow_zh = str(feedback.get("follow_up_zh") or "").strip()
            is_last = session["turns"][-1]["index"] == index
            if turn.get("status") == "ready" and is_last and follow:
                session["pending_prompt"] = {"en": follow, "zh": follow_zh}
            self._write(session)
            return session

    def save_speech(self, session_id: str, index: int, data: bytes) -> dict:
        name = f"turn-{index:02d}-speech.wav"
        if not _SPEECH.match(name):
            raise NotFound(session_id)
        with self._lock:
            session = self._read(session_id)
            turn = self._turn(session, index)
            (self._dir(session_id) / name).write_bytes(data)
            turn["speech_file"] = name
            turn["speech_mime"] = "audio/wav"
            turn.pop("speech_error", None)
            self._write(session)
            return session

    def speech_path(self, session_id: str, index: int) -> tuple[Path, str]:
        with self._lock:
            session = self._read(session_id)
            turn = self._turn(session, index)
            name = turn.get("speech_file") or ""
            if name != f"turn-{index:02d}-speech.wav" or not _SPEECH.match(name):
                raise NotFound(session_id)
            path = self._dir(session_id) / name
            if not path.is_file():
                raise NotFound(session_id)
            return path, turn.get("speech_mime") or "audio/wav"

    def audio_path(self, session_id: str, index: int) -> tuple[Path, str]:
        with self._lock:
            session = self._read(session_id)
            turn = self._turn(session, index)
            name = turn.get("audio_file") or ""
            if not _AUDIO.match(name):
                raise NotFound(session_id)
            path = self._dir(session_id) / name
            if not path.is_file():
                raise NotFound(session_id)
            return path, turn.get("mime_type") or "application/octet-stream"

    def _dir(self, session_id: str) -> Path:
        if not _ID.match(session_id or ""):
            raise NotFound(session_id)
        return self.root / session_id

    def _read(self, session_id: str) -> dict:
        path = self._dir(session_id) / "session.json"
        if not path.is_file():
            raise NotFound(session_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, session: dict) -> None:
        folder = self._dir(session["id"])
        folder.mkdir(parents=True, exist_ok=True)
        session["updated_at"] = now_iso()
        target = folder / "session.json"
        tmp = folder / "session.json.tmp"
        tmp.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)

    @staticmethod
    def _turn(session: dict, index: int) -> dict:
        for turn in session.get("turns") or []:
            if turn.get("index") == index:
                return turn
        raise NotFound(session.get("id", ""))
