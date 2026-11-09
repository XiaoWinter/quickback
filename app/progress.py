import json
import math
import re
import shutil
import threading
import uuid
from pathlib import Path

from app.storage import EXT_BY_MIME, NotFound, StorageError, normalize_mime, now_iso

_QUESTION = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RUN = re.compile(r"^[a-f0-9]{32}$")
_FILE = re.compile(r"^(?:pending|\d{2})\.(?:webm|m4a|mp3|wav|ogg)$")

DRAFT_LIMIT = 8
DRAFT_LENGTH = 120


def round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def weighted_raw(comprehensibility: int, relevance: int, fluency: int) -> int:
    value = comprehensibility * 0.4 + relevance * 0.3 + fluency * 0.3
    return max(0, min(100, round_half_up(value)))


def clean_drafts(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    found = []
    for item in raw:
        text = " ".join(str(item or "").split())[:DRAFT_LENGTH]
        if text:
            found.append(text)
        if len(found) >= DRAFT_LIMIT:
            break
    return found


def _stored_fixes(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    found = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        spoken = " ".join(str(item.get("spoken") or "").split())[:120]
        better = " ".join(str(item.get("better") or "").split())[:160]
        note = " ".join(str(item.get("note") or "").split())[:120]
        if spoken and better:
            found.append({"spoken": spoken, "better": better, "note": note})
        if len(found) >= 3:
            break
    return found


def summarize(answers: list[dict]) -> dict:
    if not answers:
        raise StorageError("先答完一题，再结束。")
    raws = [int(item["raw"]) for item in answers]
    total = sum(raws)
    return {
        "raw": total,
        "bonus": total,
        "average": round_half_up(total / len(raws)),
        "count": len(raws),
    }


def _follow(en: str, zh: str) -> dict | None:
    en = " ".join(str(en or "").split())
    zh = " ".join(str(zh or "").split())
    if not re.search(r"[A-Za-z]", en):
        return None
    return {"en": en[:240], "zh": zh[:240]}


def _duration(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 10 * 60 * 1000:
        return None
    return number


class ProgressStore:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()

    def charts(self) -> dict[str, list[dict]]:
        found: dict[str, list[dict]] = {}
        with self._lock:
            if not self.root.exists():
                return {}
            for question_dir in self.root.iterdir():
                if not question_dir.is_dir() or not _QUESTION.match(question_dir.name):
                    continue
                rows = []
                for run_dir in question_dir.iterdir():
                    run = self._read_dir(run_dir)
                    if not run or run.get("status") != "finished" or not isinstance(run.get("stats"), dict):
                        continue
                    stats = self._shown_stats(run) or {}
                    rows.append(
                        {
                            "id": run["id"],
                            "finished_at": run.get("finished_at") or "",
                            "created_at": run.get("created_at") or "",
                            "raw": int(stats.get("raw") or 0),
                            "bonus": int(stats.get("bonus") or 0),
                            "average": int(stats.get("average") or 0),
                            "count": int(stats.get("count") or 0),
                        }
                    )
                rows.sort(key=lambda item: (item["finished_at"], item["created_at"]))
                if rows:
                    found[question_dir.name] = rows
        return found

    def page(self, question: dict) -> dict:
        question_id = str(question.get("id") or "")
        with self._lock:
            open_run, finished = self._runs(question_id)
        return {
            "question": {
                "id": question_id,
                "en": question.get("en") or "",
                "zh": question.get("zh") or "",
            },
            "open": self._public(open_run) if open_run else None,
            "runs": [self._public(item) for item in finished],
        }

    def open_run(self, question: dict) -> dict:
        question_id = str(question.get("id") or "")
        en = str(question.get("en") or "").strip()
        zh = str(question.get("zh") or "").strip()
        if not _QUESTION.match(question_id) or not en or not zh:
            raise StorageError("还没有题目。")
        with self._lock:
            opened, _finished = self._runs(question_id)
            if opened:
                return self._public(opened)
            run_id = uuid.uuid4().hex
            now = now_iso()
            run = {
                "id": run_id,
                "question_id": question_id,
                "status": "open",
                "created_at": now,
                "updated_at": now,
                "finished_at": "",
                "question": {"en": en, "zh": zh},
                "pending_prompt": {"en": en, "zh": zh},
                "pending": None,
                "answers": [],
                "stats": None,
            }
            self._write(run)
            return self._public(run)

    def add_draft(self, run_id: str, text: str) -> str:
        cleaned = " ".join(str(text or "").split())[:DRAFT_LENGTH]
        if not cleaned:
            raise StorageError("先写一条提示。")
        with self._lock:
            run = self._locate(run_id)
            self._require_open(run)
            drafts = clean_drafts(run.get("drafts"))
            if cleaned in drafts:
                raise StorageError("这条提示已经有了。")
            if len(drafts) >= DRAFT_LIMIT:
                raise StorageError("最多 8 条提示。")
            drafts.append(cleaned)
            run["drafts"] = drafts
            run["updated_at"] = now_iso()
            self._write(run)
            return run["question_id"]

    def remove_draft(self, run_id: str, index: int) -> str:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise StorageError("没有这条提示。")
        with self._lock:
            run = self._locate(run_id)
            self._require_open(run)
            drafts = clean_drafts(run.get("drafts"))
            if index >= len(drafts):
                raise StorageError("没有这条提示。")
            del drafts[index]
            run["drafts"] = drafts
            run["updated_at"] = now_iso()
            self._write(run)
            return run["question_id"]

    def align_prompts(self, question_id: str, prompts: list[dict]) -> None:
        cleaned = []
        for item in prompts or []:
            if not isinstance(item, dict):
                continue
            prompt = _follow(item.get("en"), item.get("zh"))
            if prompt:
                cleaned.append(prompt)
        with self._lock:
            opened, _finished = self._runs(question_id)
            if not opened or opened.get("status") != "open" or opened.get("pending"):
                return
            index = len(opened.get("answers") or [])
            prompt = cleaned[index] if index < len(cleaned) else None
            current = opened.get("pending_prompt") or {}
            same_en = str(current.get("en") or "") == str((prompt or {}).get("en") or "")
            same_zh = str(current.get("zh") or "") == str((prompt or {}).get("zh") or "")
            if same_en and same_zh:
                return
            opened["pending_prompt"] = prompt
            if prompt is None or not same_en:
                opened["drafts"] = []
            opened["updated_at"] = now_iso()
            self._write(opened)

    def save_pending(self, run_id: str, data: bytes, mime: str, duration_ms: int | None, retake: int | None = None) -> str:
        mime = normalize_mime(mime)
        ext = EXT_BY_MIME.get(mime)
        if not ext:
            raise StorageError("这段录音的格式不支持。")
        with self._lock:
            run = self._locate(run_id)
            self._require_open(run)
            answers = run.get("answers") or []
            if retake is not None:
                if isinstance(retake, bool) or not isinstance(retake, int) or not any(item.get("number") == retake for item in answers):
                    raise StorageError("没有这道题。")
            elif not run.get("pending_prompt"):
                raise StorageError("这次可以结束了。")
            folder = self._folder(run)
            self._delete_pending_file(folder, run)
            filename = f"pending{ext}"
            (folder / filename).write_bytes(data)
            pending = {
                "audio_file": filename,
                "mime_type": mime,
                "duration_ms": _duration(duration_ms),
                "transcript": "",
                "error": "",
            }
            if retake is not None:
                pending["retake"] = retake
            run["pending"] = pending
            run["updated_at"] = now_iso()
            self._write(run)
            return run["question_id"]

    def scoring_context(self, run_id: str) -> dict:
        with self._lock:
            run = self._locate(run_id)
            self._require_open(run)
            pending = run.get("pending") if isinstance(run.get("pending"), dict) else None
            path = self._named_file(self._folder(run), (pending or {}).get("audio_file"))
            if pending is None or path is None or not path.is_file():
                raise StorageError("找不到这段录音。")
            retake = pending.get("retake")
            prompt = run.get("pending_prompt") or {}
            if isinstance(retake, int) and not isinstance(retake, bool):
                matched = next((item for item in run.get("answers") or [] if item.get("number") == retake), None)
                if matched:
                    prompt = matched.get("prompt") or prompt
            earlier = []
            for item in run.get("answers") or []:
                if isinstance(retake, int) and not isinstance(retake, bool) and int(item.get("number") or 0) >= retake:
                    continue
                asked = item.get("prompt") or {}
                earlier.append((str(asked.get("en") or ""), str(item.get("transcript") or "")))
            return {
                "audio": path.read_bytes(),
                "mime": pending.get("mime_type") or "application/octet-stream",
                "duration_ms": pending.get("duration_ms"),
                "transcript": str(pending.get("transcript") or ""),
                "topic_en": (run.get("question") or {}).get("en") or "",
                "topic_zh": (run.get("question") or {}).get("zh") or "",
                "prompt_en": prompt.get("en") or "",
                "prompt_zh": prompt.get("zh") or "",
                "earlier": earlier,
                "question_id": run["question_id"],
            }

    def fail_pending(self, run_id: str, error: str, transcript: str = "") -> str:
        with self._lock:
            run = self._locate(run_id)
            pending = run.get("pending") if isinstance(run.get("pending"), dict) else None
            if pending is None:
                raise StorageError("找不到这段录音。")
            pending["error"] = str(error or "").strip() or "评分中断了，可以点重试。"
            if transcript.strip():
                pending["transcript"] = transcript.strip()[:8000]
            run["pending"] = pending
            run["updated_at"] = now_iso()
            self._write(run)
            return run["question_id"]

    def commit_answer(self, run_id: str, transcript: str, parsed: dict) -> str:
        scores = parsed.get("scores") or {}
        notes = parsed.get("notes") or {}
        raw = weighted_raw(int(scores["comprehensibility"]), int(scores["relevance"]), int(scores["fluency"]))
        with self._lock:
            run = self._locate(run_id)
            self._require_open(run)
            pending = run.get("pending") if isinstance(run.get("pending"), dict) else None
            if pending is None:
                raise StorageError("找不到这段录音。")
            folder = self._folder(run)
            src = self._named_file(folder, pending.get("audio_file"))
            if src is None or not src.is_file():
                raise StorageError("找不到这段录音。")
            retake = pending.get("retake")
            replacing = isinstance(retake, int) and not isinstance(retake, bool)
            answers = run.setdefault("answers", [])
            if replacing:
                target = next((item for item in answers if item.get("number") == retake), None)
                if target is None:
                    raise StorageError("没有这道题。")
                number = retake
            else:
                target = None
                number = len(answers) + 1
            dest_name = f"{number:02d}{src.suffix.lower()}"
            if not _FILE.match(dest_name):
                raise StorageError("这段录音的格式不支持。")
            if target is not None:
                old_name = target.get("audio_file")
                if old_name and old_name != dest_name:
                    old = self._named_file(folder, old_name)
                    if old is not None and old.is_file():
                        old.unlink()
            src.replace(folder / dest_name)
            scored = {
                "audio_file": dest_name,
                "mime_type": pending.get("mime_type") or "application/octet-stream",
                "duration_ms": pending.get("duration_ms"),
                "transcript": transcript.strip()[:8000],
                "scores": {
                    "comprehensibility": int(scores["comprehensibility"]),
                    "relevance": int(scores["relevance"]),
                    "fluency": int(scores["fluency"]),
                },
                "notes": {
                    "comprehensibility": str(notes.get("comprehensibility") or ""),
                    "relevance": str(notes.get("relevance") or ""),
                    "fluency": str(notes.get("fluency") or ""),
                },
                "fixes": _stored_fixes(parsed.get("fixes")),
                "raw": raw,
                "bonus": 0,
            }
            if target is not None:
                target.update(scored)
            else:
                prompt = run.get("pending_prompt") or run.get("question") or {}
                answers.append(
                    {
                        "number": number,
                        "prompt": {"en": prompt.get("en") or "", "zh": prompt.get("zh") or ""},
                        "drafts": clean_drafts(run.get("drafts")),
                        **scored,
                    }
                )
                run["drafts"] = []
            run["pending"] = None
            if not replacing:
                run["pending_prompt"] = None
            run["updated_at"] = now_iso()
            self._write(run)
            return run["question_id"]

    def clear_pending(self, run_id: str) -> str:
        with self._lock:
            run = self._locate(run_id)
            self._require_open(run)
            self._delete_pending_file(self._folder(run), run)
            run["pending"] = None
            run["updated_at"] = now_iso()
            self._write(run)
            return run["question_id"]

    def finish(self, run_id: str) -> str:
        with self._lock:
            run = self._locate(run_id)
            if run.get("status") == "finished":
                return run["question_id"]
            self._require_open(run)
            if not run.get("answers"):
                raise StorageError("先答完一题，再结束。")
            self._delete_pending_file(self._folder(run), run)
            run["pending"] = None
            run["pending_prompt"] = None
            run.pop("drafts", None)
            run["stats"] = summarize(run["answers"])
            run["status"] = "finished"
            run["finished_at"] = now_iso()
            run["updated_at"] = run["finished_at"]
            self._write(run)
            return run["question_id"]

    def discard(self, run_id: str) -> str:
        with self._lock:
            run = self._locate(run_id)
            if run.get("status") != "open":
                raise StorageError("这次进展已经记下了。")
            question_id = run["question_id"]
            folder = self._folder(run)
            shutil.rmtree(folder)
            parent = folder.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
            return question_id

    def delete_question(self, question_id: str) -> None:
        if not _QUESTION.match(question_id or ""):
            return
        folder = self.root / question_id
        if folder.is_dir():
            shutil.rmtree(folder)

    def audio_path(self, run_id: str, number: int) -> tuple[Path, str]:
        with self._lock:
            run = self._locate(run_id)
            for item in run.get("answers") or []:
                if int(item.get("number") or 0) != number:
                    continue
                path = self._named_file(self._folder(run), item.get("audio_file"))
                if path is None or not path.is_file():
                    break
                return path, item.get("mime_type") or "application/octet-stream"
        raise NotFound("找不到这段录音。")

    def _runs(self, question_id: str) -> tuple[dict | None, list[dict]]:
        opened = None
        finished = []
        folder = self.root / question_id
        if not _QUESTION.match(question_id) or not folder.is_dir():
            return None, []
        for run_dir in folder.iterdir():
            run = self._read_dir(run_dir)
            if not run:
                continue
            if run.get("status") == "open" and opened is None:
                opened = run
            elif run.get("status") == "finished" and isinstance(run.get("stats"), dict):
                finished.append(run)
        finished.sort(key=lambda item: (item.get("finished_at") or "", item.get("created_at") or ""))
        return opened, finished

    def _read_dir(self, run_dir: Path) -> dict | None:
        if not run_dir.is_dir() or not _RUN.match(run_dir.name):
            return None
        path = run_dir / "run.json"
        if not path.is_file():
            return None
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(run, dict) or run.get("id") != run_dir.name:
            return None
        return run

    def _locate(self, run_id: str) -> dict:
        if not _RUN.match(run_id or ""):
            raise NotFound("找不到这次进展。")
        if self.root.exists():
            for question_dir in self.root.iterdir():
                if not question_dir.is_dir() or not _QUESTION.match(question_dir.name):
                    continue
                run = self._read_dir(question_dir / run_id)
                if run:
                    return run
        raise NotFound("找不到这次进展。")

    def _require_open(self, run: dict) -> None:
        if run.get("status") != "open":
            raise StorageError("这次进展已经记下了。")

    def _folder(self, run: dict) -> Path:
        return self.root / run["question_id"] / run["id"]

    def _write(self, run: dict) -> None:
        folder = self._folder(run)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "run.json"
        tmp = folder / "run.json.tmp"
        tmp.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _named_file(self, folder: Path, name: str | None) -> Path | None:
        filename = str(name or "")
        if not _FILE.match(filename):
            return None
        path = folder / filename
        if path.parent != folder:
            return None
        return path

    def _delete_pending_file(self, folder: Path, run: dict) -> None:
        pending = run.get("pending") if isinstance(run.get("pending"), dict) else None
        if not pending:
            return
        path = self._named_file(folder, pending.get("audio_file"))
        if path is not None and path.is_file():
            path.unlink()

    def _public(self, run: dict) -> dict:
        folder = self._folder(run)
        pending = run.get("pending") if isinstance(run.get("pending"), dict) else None
        answers = []
        for item in run.get("answers") or []:
            audio = self._named_file(folder, item.get("audio_file"))
            answers.append(
                {
                    "number": item.get("number"),
                    "prompt": item.get("prompt") or {"en": "", "zh": ""},
                    "transcript": item.get("transcript") or "",
                    "duration_ms": item.get("duration_ms"),
                    "scores": item.get("scores") or {},
                    "notes": item.get("notes") or {},
                    "fixes": item.get("fixes") if isinstance(item.get("fixes"), list) else None,
                    "drafts": clean_drafts(item.get("drafts")),
                    "raw": int(item.get("raw") or 0),
                    "bonus": int(item.get("bonus") or 0),
                    "has_audio": bool(audio and audio.is_file()),
                }
            )
        pending_error = ""
        if pending:
            pending_error = str(pending.get("error") or "").strip() or "评分中断了，可以点重试。"
        return {
            "id": run["id"],
            "question_id": run["question_id"],
            "status": run.get("status") or "open",
            "created_at": run.get("created_at") or "",
            "finished_at": run.get("finished_at") or "",
            "question": run.get("question") or {"en": "", "zh": ""},
            "pending_prompt": run.get("pending_prompt"),
            "pending_retake": pending.get("retake") if pending and isinstance(pending.get("retake"), int) else None,
            "drafts": clean_drafts(run.get("drafts")) if (run.get("status") or "open") == "open" else [],
            "pending_error": pending_error,
            "answers": answers,
            "stats": self._shown_stats(run),
        }

    def _shown_stats(self, run: dict) -> dict | None:
        if run.get("status") == "finished":
            answers = run.get("answers")
            if isinstance(answers, list) and answers:
                return summarize(answers)
        stats = run.get("stats")
        return stats if isinstance(stats, dict) else None
