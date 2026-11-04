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


def _prompt_text(en: str, zh: str) -> tuple[str, str]:
    en = str(en or "").strip()
    zh = str(zh or "").strip()
    if not en or not zh:
        raise StorageError("英文和中文都要写。")
    if len(en) > 240 or len(zh) > 240:
        raise StorageError("这句太长了。")
    return en, zh


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

    def replace_audio(
        self,
        session_id: str,
        index: int,
        data: bytes,
        mime: str,
        duration_ms: int | None,
    ) -> dict:
        mime = normalize_mime(mime)
        ext = EXT_BY_MIME.get(mime, ".bin")
        filename = f"turn-{index:02d}{ext}"
        if not _AUDIO.match(filename):
            raise StorageError("这段录音的格式不支持。")
        with self._lock:
            session = self._read(session_id)
            turn = self._turn(session, index)
            folder = self._dir(session_id)
            old_audio = turn.get("audio_file") or ""
            (folder / filename).write_bytes(data)
            if old_audio and old_audio != filename:
                self._delete_named_files(folder, old_audio)
            self._delete_named_files(folder, turn.get("speech_file"))
            turn["created_at"] = now_iso()
            turn["duration_ms"] = duration_ms
            turn["audio_file"] = filename
            turn["mime_type"] = mime
            turn["transcript"] = ""
            turn["segments"] = []
            turn["feedback"] = None
            turn["status"] = "saved"
            turn["error"] = None
            turn.pop("speech_file", None)
            turn.pop("speech_mime", None)
            turn.pop("speech_error", None)
            if session["turns"][-1]["index"] == index:
                prompt = turn.get("prompt") or session["question"]
                session["pending_prompt"] = {"en": prompt.get("en", ""), "zh": prompt.get("zh", "")}
            self._write(session)
            return session

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

    def update_question_text(self, session_id: str, en: str, zh: str) -> dict:
        en, zh = _prompt_text(en, zh)
        with self._lock:
            session = self._read(session_id)
            pending = session.get("pending_prompt") or {}
            same_as_question = pending.get("en") == session["question"]["en"] and pending.get("zh") == session["question"]["zh"]
            session["question"]["en"] = en
            session["question"]["zh"] = zh
            if not session.get("turns") or same_as_question:
                session["pending_prompt"] = {"en": en, "zh": zh}
            self._write(session)
            return session

    def update_follow_up(self, session_id: str, en: str, zh: str) -> dict:
        en, zh = _prompt_text(en, zh)
        with self._lock:
            session = self._read(session_id)
            if not session.get("turns"):
                raise StorageError("还没有回答，不能改追问。")
            last = session["turns"][-1]
            feedback = last.get("feedback")
            if isinstance(feedback, dict):
                feedback["follow_up"] = en
                feedback["follow_up_zh"] = zh
            session["pending_prompt"] = {"en": en, "zh": zh}
            self._write(session)
            return session

    def clear_follow_up(self, session_id: str) -> dict:
        with self._lock:
            session = self._read(session_id)
            if not session.get("turns"):
                raise StorageError("没有追问。")
            last = session["turns"][-1]
            feedback = last.get("feedback")
            if isinstance(feedback, dict):
                feedback["follow_up"] = ""
                feedback["follow_up_zh"] = ""
            prompt = last.get("prompt") or session["question"]
            session["pending_prompt"] = {"en": prompt.get("en", ""), "zh": prompt.get("zh", "")}
            self._write(session)
            return session

    def update_answered_turn(
        self,
        session_id: str,
        index: int,
        en: str,
        zh: str,
        natural: str | None = None,
        natural_zh: str | None = None,
    ) -> dict:
        en, zh = _prompt_text(en, zh)
        if natural is not None:
            natural = str(natural or "").strip()
            natural_zh = str(natural_zh or "").strip()
            if not natural:
                raise StorageError("地道版不能空。")
            if len(natural) > 800 or len(natural_zh) > 500:
                raise StorageError("这句太长了。")
        with self._lock:
            session = self._read(session_id)
            turn = self._turn(session, index)
            old = turn.get("prompt") or {}
            turn["prompt"] = {"en": en, "zh": zh}
            feedback = turn.get("feedback")
            if natural is not None and isinstance(feedback, dict):
                feedback["natural"] = natural
                feedback["natural_zh"] = natural_zh
            pending = session.get("pending_prompt") or {}
            is_last = session["turns"][-1]["index"] == index
            if is_last and pending.get("en") == old.get("en") and pending.get("zh") == old.get("zh"):
                session["pending_prompt"] = {"en": en, "zh": zh}
            self._write(session)
            return session

    def delete_turn(self, session_id: str, index: int) -> dict:
        with self._lock:
            session = self._read(session_id)
            turn = self._turn(session, index)
            folder = self._dir(session_id)
            was_last = session["turns"][-1]["index"] == index
            self._delete_named_files(folder, turn.get("audio_file"), turn.get("speech_file"))
            session["turns"] = [item for item in session["turns"] if item.get("index") != index]
            self._renumber_turns(folder, session["turns"])
            if was_last:
                self._set_pending_from_last(session)
            self._write(session)
            return session

    def keep_existing_speech(self, session_id: str, index: int) -> dict | None:
        name = f"turn-{index:02d}-speech.wav"
        if not _SPEECH.match(name):
            raise NotFound(session_id)
        with self._lock:
            session = self._read(session_id)
            turn = self._turn(session, index)
            path = self._dir(session_id) / name
            has_file = path.is_file() and path.stat().st_size > 0
            has_flag = turn.get("speech_file") == name
            if not has_file and not has_flag:
                return None
            if has_file and not has_flag:
                turn["speech_file"] = name
                turn["speech_mime"] = "audio/wav"
                turn.pop("speech_error", None)
                self._write(session)
            return session

    def save_speech(self, session_id: str, index: int, data: bytes, natural: str | None = None) -> dict:
        name = f"turn-{index:02d}-speech.wav"
        if not _SPEECH.match(name):
            raise NotFound(session_id)
        with self._lock:
            session = self._read(session_id)
            turn = self._turn(session, index)
            current = str((turn.get("feedback") or {}).get("natural") or "").strip()
            if natural is not None and current != str(natural).strip():
                return session
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

    def _delete_named_files(self, folder: Path, *names: str) -> None:
        for name in names:
            if not name or not (_AUDIO.match(name) or _SPEECH.match(name)):
                continue
            path = folder / name
            if path.is_file():
                path.unlink()

    def _renumber_turns(self, folder: Path, turns: list[dict]) -> None:
        turns.sort(key=lambda item: item.get("index") or 0)
        staged = []
        for new_index, turn in enumerate(turns, start=1):
            old_index = turn.get("index")
            planned = []
            audio = turn.get("audio_file") or ""
            if audio and _AUDIO.match(audio):
                planned.append(("audio_file", audio, f"turn-{new_index:02d}{Path(audio).suffix}"))
            speech = turn.get("speech_file") or ""
            if speech and _SPEECH.match(speech):
                planned.append(("speech_file", speech, f"turn-{new_index:02d}-speech.wav"))
            moved = []
            for field, old_name, new_name in planned:
                if old_name == new_name:
                    continue
                src = folder / old_name
                if not src.is_file():
                    continue
                tmp = folder / f".tmp-{old_index}-{old_name}"
                src.rename(tmp)
                moved.append((field, tmp, new_name))
            staged.append((turn, new_index, moved))
        for turn, new_index, moved in staged:
            for field, tmp, new_name in moved:
                dest = folder / new_name
                if dest.exists():
                    dest.unlink()
                tmp.rename(dest)
                turn[field] = new_name
            turn["index"] = new_index

    def _set_pending_from_last(self, session: dict) -> None:
        turns = session.get("turns") or []
        if not turns:
            question = session["question"]
            session["pending_prompt"] = {"en": question.get("en", ""), "zh": question.get("zh", "")}
            return
        last = turns[-1]
        feedback = last.get("feedback") if isinstance(last.get("feedback"), dict) else {}
        follow = str(feedback.get("follow_up") or "").strip()
        if last.get("status") == "ready" and follow:
            session["pending_prompt"] = {"en": follow, "zh": str(feedback.get("follow_up_zh") or "").strip()}
            return
        prompt = last.get("prompt") or session["question"]
        session["pending_prompt"] = {"en": prompt.get("en", ""), "zh": prompt.get("zh", "")}

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
