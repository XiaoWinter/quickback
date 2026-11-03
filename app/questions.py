import json
import random
import re
import threading
from pathlib import Path

from app.config import ROOT

QUESTIONS_PATH = ROOT / "questions.json"
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_lock = threading.Lock()


class QuestionError(Exception):
    pass


def _path(path: Path | None) -> Path:
    return path or QUESTIONS_PATH


def _clean(value) -> str:
    return str(value or "").strip()


def _slug(en: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", en.lower()).strip("-")
    slug = slug[:40].strip("-")
    return slug or "question"


def _unique_id(en: str, taken: set[str]) -> str:
    base = _slug(en)
    candidate = base
    number = 2
    while candidate in taken or not _ID.match(candidate):
        suffix = f"-{number}"
        candidate = f"{base[:40 - len(suffix)].strip('-')}{suffix}"
        number += 1
    return candidate


def _save(questions: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(questions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_questions(path: Path | None = None) -> list[dict]:
    file = _path(path)
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QuestionError(f"题目文件读不了：{file}") from exc
    if not isinstance(raw, list) or not raw:
        raise QuestionError("questions.json 里还没有题目。")
    seen: set[str] = set()
    questions: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise QuestionError("questions.json 里有一条不是对象。")
        qid = _clean(item.get("id"))
        en = _clean(item.get("en"))
        zh = _clean(item.get("zh"))
        if not qid or not en or not zh:
            raise QuestionError("每道题都要有 id、en、zh。")
        if not _ID.match(qid):
            raise QuestionError(f"题目 id 只能用小写英文、数字和短横线：{qid}")
        if qid in seen:
            raise QuestionError(f"题目 id 重复：{qid}")
        seen.add(qid)
        questions.append({"id": qid, "en": en, "zh": zh})
    return questions


def next_question(exclude: str | None = None) -> dict:
    questions = load_questions()
    pool = [item for item in questions if item["id"] != exclude] or questions
    return random.choice(pool)


def get_question(question_id: str) -> dict:
    for item in load_questions():
        if item["id"] == question_id:
            return item
    raise QuestionError(f"没有这道题：{question_id}")


def create_question(en: str, zh: str, path: Path | None = None) -> dict:
    en, zh = _clean(en), _clean(zh)
    if not en or not zh:
        raise QuestionError("英文和中文都要写。")
    file = _path(path)
    with _lock:
        questions = load_questions(file)
        item = {"id": _unique_id(en, {q["id"] for q in questions}), "en": en, "zh": zh}
        questions.append(item)
        _save(questions, file)
        return item


def update_question(question_id: str, en: str, zh: str, path: Path | None = None) -> dict:
    en, zh = _clean(en), _clean(zh)
    if not en or not zh:
        raise QuestionError("英文和中文都要写。")
    file = _path(path)
    with _lock:
        questions = load_questions(file)
        for item in questions:
            if item["id"] == question_id:
                item["en"] = en
                item["zh"] = zh
                _save(questions, file)
                return item
    raise QuestionError(f"没有这道题：{question_id}")


def delete_question(question_id: str, path: Path | None = None) -> None:
    file = _path(path)
    with _lock:
        questions = load_questions(file)
        if len(questions) <= 1:
            raise QuestionError("至少留一道题，练习才有题目可抽。")
        kept = [item for item in questions if item["id"] != question_id]
        if len(kept) == len(questions):
            raise QuestionError(f"没有这道题：{question_id}")
        _save(kept, file)
