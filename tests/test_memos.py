import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import memos
from app.main import app
from app.memos import MemoError, create_memo, delete_memo, list_memos, update_memo


class MemoStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "memos.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_an_empty_list(self):
        self.assertEqual(list_memos(self.path), [])

    def test_create_update_and_delete(self):
        made = create_memo("  先记一句  ", self.path, "  口语  ")
        self.assertEqual(made["title"], "口语")
        self.assertEqual(made["text"], "先记一句")
        self.assertEqual(len(made["id"]), 32)
        changed = update_memo(made["id"], "改过了", self.path)
        self.assertEqual(changed["text"], "改过了")
        self.assertNotEqual(changed["updated_at"], "")
        self.assertEqual([item["id"] for item in list_memos(self.path)], [made["id"]])
        delete_memo(made["id"], self.path)
        self.assertEqual(list_memos(self.path), [])

    def test_old_memo_without_title_still_loads(self):
        self.path.write_text(
            json.dumps([{
                "id": "a" * 32,
                "text": "第一行\n后面",
                "created_at": "2026-11-18T23:53:00+08:00",
                "updated_at": "2026-11-18T23:53:00+08:00",
            }]),
            encoding="utf-8",
        )
        found = list_memos(self.path)
        self.assertEqual(found[0]["title"], "")
        self.assertEqual(found[0]["text"], "第一行\n后面")

    def test_title_cannot_be_too_long(self):
        with self.assertRaises(MemoError):
            create_memo("正文", self.path, "题" * 81)

    def test_blank_text_is_rejected(self):
        with self.assertRaises(MemoError):
            create_memo("   ", self.path)

    def test_newest_stays_first(self):
        first = create_memo("第一条", self.path)
        second = create_memo("第二条", self.path)
        self.assertEqual([item["id"] for item in list_memos(self.path)], [second["id"], first["id"]])

    def test_broken_file_is_reported(self):
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaises(MemoError):
            list_memos(self.path)


class MemoApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "memos.json"
        self.original = memos.MEMOS_PATH
        memos.MEMOS_PATH = self.path
        self.client = TestClient(app)

    def tearDown(self):
        memos.MEMOS_PATH = self.original
        self.tmp.cleanup()

    def test_round_trip(self):
        created = self.client.post("/api/memos", json={"title": "本机", "text": "只留在这台电脑"})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["title"], "本机")
        memo_id = created.json()["id"]
        listed = self.client.get("/api/memos")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["text"], "只留在这台电脑")
        updated = self.client.put(f"/api/memos/{memo_id}", json={"title": "", "text": "改一句"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["title"], "")
        self.assertEqual(updated.json()["text"], "改一句")
        removed = self.client.delete(f"/api/memos/{memo_id}")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/memos").json(), [])
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved, [])
