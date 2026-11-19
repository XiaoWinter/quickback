import io
import json
import re
import shutil
import threading
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from app.config import settings
from app.storage import now_iso

MEMOS_PATH = settings.data_dir.parent / "memos.json"
TITLE_LIMIT = 80
TEXT_LIMIT = 2000
FILE_LIMIT = 8
_ID = re.compile(r"^[a-f0-9]{32}$")
_FILE_ID = re.compile(r"^[a-f0-9]{16}$")
_SAFE_NAME = re.compile(r"[^\w.\u4e00-\u9fff\-()\[\] ]+", re.UNICODE)
_lock = threading.Lock()

_EXT_BY_MIME = {
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/aac": ".aac",
    "audio/x-m4a": ".m4a",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/epub+zip": ".epub",
    "application/x-mobipocket-ebook": ".mobi",
    "application/vnd.amazon.ebook": ".azw",
    "application/zip": ".zip",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/rtf": ".rtf",
    "application/vnd.oasis.opendocument.text": ".odt",
}

_MIME_BY_EXT = {
    ".webm": "audio/webm",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".epub": "application/epub+zip",
    ".mobi": "application/x-mobipocket-ebook",
    ".azw": "application/vnd.amazon.ebook",
    ".azw3": "application/vnd.amazon.ebook",
    ".zip": "application/zip",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
}

_EBOOK_MIME = frozenset({
    "application/pdf",
    "application/epub+zip",
    "application/x-mobipocket-ebook",
    "application/vnd.amazon.ebook",
})
_DOC_MIME = frozenset({
    "text/plain",
    "text/markdown",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/rtf",
    "application/vnd.oasis.opendocument.text",
})
_COVER_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


class MemoError(Exception):
    pass


def _path(path: Path | None) -> Path:
    return path or MEMOS_PATH


def _files_root(path: Path | None = None) -> Path:
    return _path(path).parent / "memos"


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


def _text(value, *, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise MemoError("先写备忘，或选一个文件。")
    if len(text) > TEXT_LIMIT:
        raise MemoError(f"备忘最多 {TEXT_LIMIT} 字。")
    return text


def _safe_name(name: str) -> str:
    raw = Path(str(name or "").strip()).name or "file"
    cleaned = _SAFE_NAME.sub("_", raw).strip(" ._")
    return (cleaned or "file")[:120]


def _kind(mime: str) -> str:
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "image"
    if mime in _EBOOK_MIME:
        return "ebook"
    if mime in _DOC_MIME:
        return "doc"
    return "file"


def _epub_cover(data: bytes) -> tuple[bytes, str] | None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    names = archive.namelist()
    picked = None
    for name in names:
        lower = name.lower()
        if "cover" in Path(lower).stem and Path(lower).suffix in _COVER_EXT:
            picked = name
            break
    if picked is None:
        try:
            root = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
            opf_path = rootfile.get("full-path") if rootfile is not None else ""
            if opf_path:
                opf = ET.fromstring(archive.read(opf_path))
                cover_id = ""
                for meta in opf.findall(".//{http://www.idpf.org/2007/opf}meta"):
                    if (meta.get("name") or "").lower() == "cover":
                        cover_id = meta.get("content") or ""
                        break
                if cover_id:
                    for item in opf.findall(".//{http://www.idpf.org/2007/opf}item"):
                        if item.get("id") == cover_id:
                            href = item.get("href") or ""
                            base = str(Path(opf_path).parent).replace("\\", "/")
                            picked = href if base in ("", ".") else f"{base}/{href}".replace("//", "/")
                            break
        except (KeyError, ET.ParseError, OSError):
            picked = None
    if not picked or picked not in names:
        return None
    ext = Path(picked).suffix.lower()
    if ext not in _COVER_EXT:
        return None
    try:
        return archive.read(picked), ext
    except KeyError:
        return None


def _resolve_type(name: str, mime: str) -> tuple[str, str]:
    base = (mime or "").split(";", 1)[0].strip().lower()
    ext = Path(name or "").suffix.lower()
    if base in _EXT_BY_MIME:
        return base, _EXT_BY_MIME[base]
    if ext in _MIME_BY_EXT:
        guessed = _MIME_BY_EXT[ext]
        return guessed, ext
    raise MemoError("这个文件类型还不支持。")


def _read_files(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    found = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("id") or "").strip()
        name = _safe_name(item.get("name"))
        mime = str(item.get("mime") or "").strip().lower()
        stored = str(item.get("stored") or "").strip()
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if not _FILE_ID.match(file_id) or file_id in seen:
            continue
        if not mime or not stored or ".." in stored or "/" in stored or "\\" in stored:
            continue
        cover = str(item.get("cover") or "").strip()
        if cover and (".." in cover or "/" in cover or "\\" in cover):
            cover = ""
        seen.add(file_id)
        found.append({
            "id": file_id,
            "name": name,
            "mime": mime,
            "size": max(0, size),
            "kind": _kind(mime),
            "stored": stored,
            "cover": cover,
        })
        if len(found) >= FILE_LIMIT:
            break
    return found


def _save(memos: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(memos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _public(item: dict) -> dict:
    files = []
    for file in item.get("files") or []:
        entry = {
            "id": file["id"],
            "name": file["name"],
            "mime": file["mime"],
            "size": file["size"],
            "kind": file["kind"],
            "ext": Path(file.get("stored") or file["name"]).suffix.lower().lstrip(".") or "file",
            "url": f"/api/memos/{item['id']}/files/{file['id']}",
        }
        if file.get("cover"):
            entry["cover_url"] = f"/api/memos/{item['id']}/files/{file['id']}/cover"
        files.append(entry)
    return {
        "id": item["id"],
        "title": item.get("title") or "",
        "text": item.get("text") or "",
        "files": files,
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


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
        files = _read_files(item.get("files"))
        if not _ID.match(memo_id):
            raise MemoError("备忘 id 不对。")
        if memo_id in seen:
            raise MemoError(f"备忘 id 重复：{memo_id}")
        if not text and not files:
            raise MemoError("有一条备忘是空的。")
        seen.add(memo_id)
        created = _time(item.get("created_at")) or now_iso()
        updated = _time(item.get("updated_at")) or created
        memos.append({
            "id": memo_id,
            "title": _title(item.get("title")),
            "text": text,
            "files": files,
            "created_at": created,
            "updated_at": updated,
        })
    memos.sort(key=lambda item: item["created_at"], reverse=True)
    return memos


def list_memos(path: Path | None = None) -> list[dict]:
    file = _path(path)
    with _lock:
        items = _load(file)
        for item in items:
            _sync_folder(item["id"], item.get("files") or [], path)
        return [_public(item) for item in items]


def _memo_dir(memo_id: str, path: Path | None = None) -> Path:
    return _files_root(path) / memo_id


def _remove_path(target: Path) -> None:
    if not target.exists():
        return
    try:
        target.unlink()
        return
    except OSError:
        pass
    # Windows often keeps a PDF open for preview; rename first so it leaves the memo.
    trash = target.with_name(f"{target.name}.trash-{uuid.uuid4().hex[:8]}")
    try:
        target.replace(trash)
    except OSError:
        return
    try:
        trash.unlink()
    except OSError:
        pass


def _kept_names(files: list[dict]) -> set[str]:
    names: set[str] = set()
    for entry in files:
        stored = str(entry.get("stored") or "").strip()
        cover = str(entry.get("cover") or "").strip()
        if stored:
            names.add(stored)
        if cover:
            names.add(cover)
    return names


def _sync_folder(memo_id: str, files: list[dict], path: Path | None = None) -> None:
    folder = _memo_dir(memo_id, path)
    if not folder.is_dir():
        return
    kept = _kept_names(files)
    for child in list(folder.iterdir()):
        if not child.is_file():
            continue
        name = child.name
        if name in kept:
            continue
        if name.endswith(".tmp"):
            continue
        _remove_path(child)
    try:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass


def _store_bytes(memo_id: str, name: str, mime: str, data: bytes, path: Path | None = None) -> dict:
    if not data:
        raise MemoError("这个文件是空的。")
    mime, ext = _resolve_type(name, mime)
    file_id = uuid.uuid4().hex[:16]
    stored = f"{file_id}{ext}"
    folder = _memo_dir(memo_id, path)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / stored
    target.write_bytes(data)
    cover = ""
    if mime == "application/epub+zip":
        extracted = _epub_cover(data)
        if extracted:
            cover_data, cover_ext = extracted
            cover = f"{file_id}-cover{cover_ext}"
            (folder / cover).write_bytes(cover_data)
    return {
        "id": file_id,
        "name": _safe_name(name),
        "mime": mime,
        "size": len(data),
        "kind": _kind(mime),
        "stored": stored,
        "cover": cover,
    }


def create_memo(
    text: str,
    path: Path | None = None,
    title: str = "",
    uploads: list[tuple[str, str, bytes]] | None = None,
) -> dict:
    uploads = uploads or []
    body = _text(text, allow_empty=bool(uploads))
    heading = _title(title)
    if not body and not uploads and not heading:
        raise MemoError("先写备忘，或选一个文件。")
    if len(uploads) > FILE_LIMIT:
        raise MemoError(f"一条备忘最多 {FILE_LIMIT} 个文件。")
    file = _path(path)
    with _lock:
        memos = _load(file)
        now = now_iso()
        memo_id = uuid.uuid4().hex
        files = []
        try:
            for name, mime, data in uploads:
                files.append(_store_bytes(memo_id, name, mime, data, path))
        except Exception:
            shutil.rmtree(_memo_dir(memo_id, path), ignore_errors=True)
            raise
        if not heading and not body and files:
            heading = files[0]["name"][:TITLE_LIMIT]
        item = {
            "id": memo_id,
            "title": heading,
            "text": body,
            "files": files,
            "created_at": now,
            "updated_at": now,
        }
        memos.insert(0, item)
        _save(memos, file)
        return _public(item)


def update_memo(memo_id: str, text: str, path: Path | None = None, title: str = "") -> dict:
    file = _path(path)
    with _lock:
        memos = _load(file)
        for item in memos:
            if item["id"] == memo_id:
                body = _text(text, allow_empty=bool(item.get("files")))
                item["title"] = _title(title)
                item["text"] = body
                item["updated_at"] = now_iso()
                _save(memos, file)
                return _public(item)
    raise MemoError("没有这条备忘。")


def add_files(memo_id: str, uploads: list[tuple[str, str, bytes]], path: Path | None = None) -> dict:
    if not uploads:
        raise MemoError("先选文件。")
    file = _path(path)
    with _lock:
        memos = _load(file)
        for item in memos:
            if item["id"] != memo_id:
                continue
            current = list(item.get("files") or [])
            if len(current) + len(uploads) > FILE_LIMIT:
                raise MemoError(f"一条备忘最多 {FILE_LIMIT} 个文件。")
            added = []
            try:
                for name, mime, data in uploads:
                    saved = _store_bytes(memo_id, name, mime, data, path)
                    added.append(saved)
                    current.append(saved)
            except Exception:
                folder = _memo_dir(memo_id, path)
                for entry in added:
                    try:
                        (folder / entry["stored"]).unlink(missing_ok=True)
                        if entry.get("cover"):
                            (folder / entry["cover"]).unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
            item["files"] = current
            item["updated_at"] = now_iso()
            _save(memos, file)
            return _public(item)
    raise MemoError("没有这条备忘。")


def file_path(memo_id: str, file_id: str, path: Path | None = None) -> tuple[Path, dict]:
    file = _path(path)
    with _lock:
        for item in _load(file):
            if item["id"] != memo_id:
                continue
            for entry in item.get("files") or []:
                if entry["id"] != file_id:
                    continue
                target = _memo_dir(memo_id, path) / entry["stored"]
                if not target.is_file():
                    raise MemoError("找不到这个文件。")
                return target, entry
    raise MemoError("找不到这个文件。")


def cover_path(memo_id: str, file_id: str, path: Path | None = None) -> tuple[Path, str]:
    file = _path(path)
    with _lock:
        for item in _load(file):
            if item["id"] != memo_id:
                continue
            for entry in item.get("files") or []:
                if entry["id"] != file_id:
                    continue
                cover = entry.get("cover") or ""
                if not cover:
                    raise MemoError("没有封面。")
                target = _memo_dir(memo_id, path) / cover
                if not target.is_file():
                    raise MemoError("没有封面。")
                mime = _COVER_EXT.get(Path(cover).suffix.lower(), "image/jpeg")
                return target, mime
    raise MemoError("没有封面。")


def delete_file(memo_id: str, file_id: str, path: Path | None = None) -> dict:
    file = _path(path)
    with _lock:
        memos = _load(file)
        for item in memos:
            if item["id"] != memo_id:
                continue
            kept = []
            removed = None
            for entry in item.get("files") or []:
                if entry["id"] == file_id:
                    removed = entry
                else:
                    kept.append(entry)
            if removed is None:
                raise MemoError("找不到这个文件。")
            if not (item.get("text") or "").strip() and not kept:
                raise MemoError("至少留一句备忘或一个文件。")
            item["files"] = kept
            item["updated_at"] = now_iso()
            _save(memos, file)
            _sync_folder(memo_id, kept, path)
            return _public(item)
    raise MemoError("没有这条备忘。")


def delete_memo(memo_id: str, path: Path | None = None) -> None:
    file = _path(path)
    with _lock:
        memos = _load(file)
        kept = [item for item in memos if item["id"] != memo_id]
        if len(kept) == len(memos):
            raise MemoError("没有这条备忘。")
        _save(kept, file)
    folder = _memo_dir(memo_id, path)
    if folder.is_dir():
        for child in list(folder.iterdir()):
            if child.is_file():
                _remove_path(child)
        try:
            folder.rmdir()
        except OSError:
            shutil.rmtree(folder, ignore_errors=True)
