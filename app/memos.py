import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.storage import now_iso

MEMOS_PATH = settings.data_dir.parent / "memos.json"
TITLE_LIMIT = 80
TEXT_LIMIT = 2000
_ID = re.compile(r"^[a-f0-9]{32}$")
_lock = threading.Lock()


class MemoError(Exception):
    pass


def _path(path: Path | None) -> Path:
    return path or MEMOS_PATH


def _time(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return ""
    return text


def _title(value) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > TITLE_LIMIT:
        raise MemoError(f"标题最多 {TITLE_LIMIT} 字。")
    return text


def _text(value) -> str:
    text = str(value or "").strip()
    if not text:
        raise MemoError("先写备忘。")
    if len(text) > TEXT_LIMIT:
        raise MemoError(f"备忘最多 {TEXT_LIMIT} 字。")
    return text


def _save(memos: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(memos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoError(f"备忘录读不了：{path}") from exc
    if not isinstance(raw, list):
        raise MemoError("备忘录文件格式不对。")
    seen: set[str] = set()
    memos: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise MemoError("备忘录里有一条不是对象。")
        memo_id = str(item.get("id") or "").strip()
        text = str(item.get("text") or "").strip()
        if not _ID.match(memo_id):
            raise MemoError("备忘 id 不对。")
        if memo_id in seen:
            raise MemoError(f"备忘 id 重复：{memo_id}")
        if not text:
            raise MemoError("有一条备忘是空的。")
        seen.add(memo_id)
        created = _time(item.get("created_at")) or now_iso()
        updated = _time(item.get("updated_at")) or created
        memos.append({
            "id": memo_id,
            "title": _title(item.get("title")),
            "text": text,
            "created_at": created,
            "updated_at": updated,
        })
    memos.sort(key=lambda item: item["created_at"], reverse=True)
    return memos


def list_memos(path: Path | None = None) -> list[dict]:
    return _load(_path(path))


def create_memo(text: str, path: Path | None = None, title: str = "") -> dict:
    heading = _title(title)
    body = _text(text)
    file = _path(path)
    with _lock:
        memos = _load(file)
        now = now_iso()
        item = {
            "id": uuid.uuid4().hex,
            "title": heading,
            "text": body,
            "created_at": now,
            "updated_at": now,
        }
        memos.insert(0, item)
        _save(memos, file)
        return item


def update_memo(memo_id: str, text: str, path: Path | None = None, title: str = "") -> dict:
    heading = _title(title)
    body = _text(text)
    file = _path(path)
    with _lock:
        memos = _load(file)
        for item in memos:
            if item["id"] == memo_id:
                item["title"] = heading
                item["text"] = body
                item["updated_at"] = now_iso()
                _save(memos, file)
                return item
    raise MemoError("没有这条备忘。")


def delete_memo(memo_id: str, path: Path | None = None) -> None:
    file = _path(path)
    with _lock:
        memos = _load(file)
        kept = [item for item in memos if item["id"] != memo_id]
        if len(kept) == len(memos):
            raise MemoError("没有这条备忘。")
        _save(kept, file)
