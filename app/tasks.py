"""Daily spaced-repetition tasks for archived questions (simplified SM-2)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import ROOT
from app.storage import now_iso

TASKS_PATH = ROOT / "data" / "tasks.json"
DAILY_MIN = 3
DAILY_MAX = 5
DEFAULT_EASE = 2.5
MIN_EASE = 1.3
PASS_AVERAGE = 60

_lock = threading.Lock()


class TaskError(Exception):
    pass


def _parse_when(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _due_iso(days: float, *, now: datetime | None = None) -> str:
    stamp = now or datetime.now().astimezone()
    delta = timedelta(days=max(0.0, float(days)))
    return (stamp + delta).isoformat(timespec="seconds")


def average_value(average: int | float | None) -> float | None:
    if average is None or (isinstance(average, str) and not str(average).strip()):
        return None
    try:
        return float(average)
    except (TypeError, ValueError):
        return None


def meets_pass_average(average: int | float | None) -> bool:
    score = average_value(average)
    return score is not None and score >= PASS_AVERAGE


def grade_from_average(average: int | float | None) -> str:
    score = average_value(average)
    if score is None:
        score = 0.0
    if score >= 85:
        return "easy"
    if score >= 70:
        return "good"
    if score >= 50:
        return "hard"
    return "again"


def next_schedule(card: dict, grade: str, *, now: datetime | None = None) -> dict:
    stamp = now or datetime.now().astimezone()
    ease = float(card.get("ease") or DEFAULT_EASE)
    interval = float(card.get("interval_days") or 0)
    reps = int(card.get("reps") or 0)
    lapses = int(card.get("lapses") or 0)
    key = str(grade or "").strip().lower()

    if key == "again":
        interval = 1.0
        ease = max(MIN_EASE, ease - 0.2)
        reps = 0
        lapses += 1
    elif key == "hard":
        if reps <= 0:
            interval = 1.0
        else:
            interval = max(1.0, interval * 1.2)
        ease = max(MIN_EASE, ease - 0.15)
        reps += 1
    elif key == "easy":
        if reps <= 0:
            interval = 4.0
        elif reps == 1:
            interval = 6.0
        else:
            interval = max(1.0, interval * (ease + 0.15))
        ease = ease + 0.15
        reps += 1
    else:  # good
        if reps <= 0:
            interval = 1.0
        elif reps == 1:
            interval = 6.0
        else:
            interval = max(1.0, interval * ease)
        reps += 1

    return {
        "ease": round(ease, 3),
        "interval_days": round(interval, 3),
        "reps": reps,
        "lapses": lapses,
        "due_at": _due_iso(interval, now=stamp),
        "last_grade": key or "good",
        "updated_at": now_iso(),
    }


class TaskStore:
    def __init__(self, path: Path | None = None):
        self.path = path or TASKS_PATH

    def today(self, closed_questions: list[dict], *, now: datetime | None = None) -> dict:
        stamp = now or datetime.now().astimezone()
        day = stamp.date().isoformat()
        with _lock:
            data = self._read()
            cards = data.setdefault("cards", {})
            closed_ids = []
            by_id = {}
            for item in closed_questions or []:
                qid = str((item or {}).get("id") or "").strip()
                if not qid:
                    continue
                closed_ids.append(qid)
                by_id[qid] = item
            closed_set = set(closed_ids)

            for qid in list(cards.keys()):
                if qid not in closed_set:
                    cards.pop(qid, None)

            daily = data.get("daily") if isinstance(data.get("daily"), dict) else {}
            plan = daily.get("items") if isinstance(daily.get("items"), list) else []
            if daily.get("date") == day and plan:
                items = self._present_plan(plan, by_id, closed_set)
                self._write(data)
                done_count = sum(1 for item in items if item.get("done"))
                return {
                    "date": day,
                    "min": DAILY_MIN,
                    "max": DAILY_MAX,
                    "items": items,
                    "done_count": done_count,
                    "pending_count": len(items) - done_count,
                    "due_count": sum(1 for item in items if item.get("kind") == "due" and not item.get("done")),
                    "new_count": sum(1 for item in items if item.get("kind") == "new" and not item.get("done")),
                    "waiting_count": 0,
                    "archived_count": len(closed_ids),
                }

            due: list[dict] = []
            fresh: list[dict] = []
            waiting = 0
            for qid in closed_ids:
                card = cards.get(qid)
                question = by_id[qid]
                row = {
                    "id": qid,
                    "en": question.get("en") or "",
                    "zh": question.get("zh") or "",
                    "category": question.get("category") or "",
                }
                if not card:
                    fresh.append(row)
                    continue
                due_at = _parse_when(card.get("due_at"))
                row = {
                    **row,
                    "due_at": card.get("due_at") or "",
                    "interval_days": card.get("interval_days") or 0,
                    "reps": card.get("reps") or 0,
                }
                if due_at is None or due_at <= stamp:
                    due.append(row)
                else:
                    waiting += 1

            due.sort(key=lambda item: (item.get("due_at") or "", item["id"]))
            fresh.sort(key=lambda item: item["id"])
            queue: list[dict] = []
            for item in due:
                if len(queue) >= DAILY_MAX:
                    break
                queue.append({**item, "kind": "due"})
            for item in fresh:
                if len(queue) >= DAILY_MAX:
                    break
                if len(queue) >= DAILY_MIN:
                    break
                queue.append({**item, "kind": "new"})

            plan = [{"id": item["id"], "kind": item["kind"]} for item in queue]
            data["daily"] = {"date": day, "items": plan}
            items = [{**item, "done": False, "done_at": ""} for item in queue]
            self._write(data)
            return {
                "date": day,
                "min": DAILY_MIN,
                "max": DAILY_MAX,
                "items": items,
                "done_count": 0,
                "pending_count": len(items),
                "due_count": sum(1 for item in items if item.get("kind") == "due"),
                "new_count": sum(1 for item in items if item.get("kind") == "new"),
                "waiting_count": waiting,
                "archived_count": len(closed_ids),
            }

    def ensure_card(self, question_id: str) -> dict:
        qid = str(question_id or "").strip()
        if not qid:
            raise TaskError("没有这道题。")
        with _lock:
            data = self._read()
            cards = data.setdefault("cards", {})
            card = cards.get(qid)
            if card:
                return dict(card)
            now = datetime.now().astimezone()
            card = {
                "ease": DEFAULT_EASE,
                "interval_days": 0,
                "reps": 0,
                "lapses": 0,
                "due_at": now.isoformat(timespec="seconds"),
                "last_grade": "",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            cards[qid] = card
            self._write(data)
            return dict(card)

    def complete(self, question_id: str, average: int | float | None = None, grade: str | None = None) -> dict:
        qid = str(question_id or "").strip()
        if not qid:
            raise TaskError("没有这道题。")
        if not meets_pass_average(average):
            score = average_value(average)
            shown = "—" if score is None else str(int(score + 0.5) if score >= 0 else 0)
            raise TaskError(f"平均 {shown} 分，低于 {PASS_AVERAGE}，请重做。")
        mark = str(grade or "").strip().lower() or grade_from_average(average)
        if mark not in {"again", "hard", "good", "easy"}:
            mark = "good"
        with _lock:
            data = self._read()
            cards = data.setdefault("cards", {})
            daily = data.get("daily") if isinstance(data.get("daily"), dict) else {}
            plan = daily.get("items") if isinstance(daily.get("items"), list) else []
            for entry in plan:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("id") or "") != qid:
                    continue
                if str(entry.get("done_at") or "").strip():
                    current = cards.get(qid) or {}
                    return {
                        "question_id": qid,
                        "grade": str(current.get("last_grade") or mark),
                        "already_done": True,
                        **current,
                    }
            current = cards.get(qid) or {
                "ease": DEFAULT_EASE,
                "interval_days": 0,
                "reps": 0,
                "lapses": 0,
                "due_at": "",
                "last_grade": "",
                "created_at": now_iso(),
            }
            updated = {**current, **next_schedule(current, mark)}
            if "created_at" not in updated:
                updated["created_at"] = current.get("created_at") or now_iso()
            cards[qid] = updated
            history = data.setdefault("history", [])
            history.append(
                {
                    "question_id": qid,
                    "grade": mark,
                    "average": average,
                    "at": now_iso(),
                    "due_at": updated["due_at"],
                    "interval_days": updated["interval_days"],
                }
            )
            data["history"] = history[-500:]
            for entry in plan:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("id") or "") != qid:
                    continue
                entry["done_at"] = now_iso()
            if plan:
                data["daily"] = daily
            self._write(data)
            return {"question_id": qid, "grade": mark, **updated}

    def complete_if_today(
        self,
        question_id: str,
        average: int | float | None = None,
        grade: str | None = None,
        *,
        now: datetime | None = None,
    ) -> dict | None:
        """Mark today's planned task done once. No-op if not in today's plan or already done."""
        qid = str(question_id or "").strip()
        if not qid:
            return None
        stamp = now or datetime.now().astimezone()
        day = stamp.date().isoformat()
        with _lock:
            data = self._read()
            daily = data.get("daily") if isinstance(data.get("daily"), dict) else {}
            if str(daily.get("date") or "") != day:
                return None
            plan = daily.get("items") if isinstance(daily.get("items"), list) else []
            entry = next(
                (
                    item
                    for item in plan
                    if isinstance(item, dict) and str(item.get("id") or "") == qid
                ),
                None,
            )
            if entry is None:
                return None
            if str(entry.get("done_at") or "").strip():
                return None
        if not meets_pass_average(average):
            score = average_value(average)
            return {
                "question_id": qid,
                "redo": True,
                "average": None if score is None else int(score + 0.5) if score >= 0 else 0,
                "pass_average": PASS_AVERAGE,
            }
        return self.complete(question_id, average=average, grade=grade)

    def _present_plan(self, plan: list, by_id: dict, closed_set: set[str]) -> list[dict]:
        items: list[dict] = []
        for entry in plan:
            if not isinstance(entry, dict):
                continue
            qid = str(entry.get("id") or "").strip()
            if not qid or qid not in closed_set:
                continue
            question = by_id[qid]
            done_at = str(entry.get("done_at") or "").strip()
            items.append(
                {
                    "id": qid,
                    "en": question.get("en") or "",
                    "zh": question.get("zh") or "",
                    "category": question.get("category") or "",
                    "kind": str(entry.get("kind") or "due"),
                    "done": bool(done_at),
                    "done_at": done_at,
                }
            )
        return items

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"cards": {}, "history": [], "daily": {}}
        if not isinstance(raw, dict):
            return {"cards": {}, "history": [], "daily": {}}
        cards = raw.get("cards")
        history = raw.get("history")
        daily = raw.get("daily")
        return {
            "cards": cards if isinstance(cards, dict) else {},
            "history": history if isinstance(history, list) else [],
            "daily": daily if isinstance(daily, dict) else {},
        }

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)
