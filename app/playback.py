import logging
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("quickback.playback")

_CACHE_SUFFIX = ".m4a"
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


class PlaybackError(Exception):
    pass


def should_convert(user_agent: str, compat: str) -> bool:
    if (compat or "").strip().lower() == "m4a":
        return True
    ua = user_agent or ""
    if any(token in ua for token in ("iPhone", "iPad", "iPod")):
        return True
    if "Safari" in ua and not any(token in ua for token in ("Chrome", "Chromium", "Edg", "Android", "CriOS")):
        return True
    return False


def cache_path(path: Path) -> Path:
    return path.with_name(path.name + _CACHE_SUFFIX)


def _partial(target: Path) -> Path:
    return target.with_name(target.stem + ".writing.m4a")


def forget(path: Path) -> None:
    target = cache_path(path)
    for candidate in (target, _partial(target)):
        try:
            if candidate.is_file():
                candidate.unlink()
        except OSError:
            log.warning("could not remove %s", candidate.name)


def for_client(path: Path, mime: str, user_agent: str, compat: str, runner=None) -> tuple[Path, str]:
    if not should_convert(user_agent, compat) or not _is_webm(path, mime):
        return path, mime
    try:
        return _ensure(path, runner), "audio/mp4"
    except PlaybackError as exc:
        log.warning("%s", exc)
        return path, mime


def _is_webm(path: Path, mime: str) -> bool:
    return mime in {"audio/webm", "video/webm"} or path.suffix.lower() == ".webm"


def _lock(path: Path) -> threading.Lock:
    key = str(path)
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _fresh(source: Path, target: Path) -> bool:
    try:
        return target.is_file() and target.stat().st_size > 0 and target.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def _ensure(path: Path, runner) -> Path:
    target = cache_path(path)
    with _lock(path):
        if _fresh(path, target):
            return target
        partial = _partial(target)
        run = runner or _ffmpeg
        try:
            run(path, partial)
        except PlaybackError:
            partial.unlink(missing_ok=True)
            raise
        except Exception as exc:
            partial.unlink(missing_ok=True)
            raise PlaybackError(f"手机播放转码失败：{path.name}") from exc
        if not partial.is_file() or partial.stat().st_size < 32:
            partial.unlink(missing_ok=True)
            raise PlaybackError(f"手机播放转码失败：{path.name}")
        partial.replace(target)
        return target


def _ffmpeg(source: Path, target: Path) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-f",
        "mp4",
        "-movflags",
        "+faststart",
        str(target),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise PlaybackError("没有 ffmpeg，手机播不了这段录音。") from exc
    except subprocess.CalledProcessError as exc:
        lines = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        tail = lines[-1] if lines else "ffmpeg failed"
        raise PlaybackError(tail) from exc
