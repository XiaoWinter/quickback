import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import memos
from app.main import app
from app.memos import MemoError, create_memo, delete_file, delete_memo, file_path, list_memos, update_memo


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
        self.assertEqual(made["files"], [])
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
        self.assertEqual(found[0]["files"], [])

    def test_title_cannot_be_too_long(self):
        with self.assertRaises(MemoError):
            create_memo("正文", self.path, "题" * 81)

    def test_blank_text_is_rejected(self):
        with self.assertRaises(MemoError):
            create_memo("   ", self.path)

    def test_file_only_memo(self):
        made = create_memo("", self.path, "", uploads=[("note.txt", "text/plain", b"hello")])
        self.assertEqual(made["title"], "note.txt")
        self.assertEqual(made["text"], "")
        self.assertEqual(len(made["files"]), 1)
        self.assertEqual(made["files"][0]["kind"], "doc")
        path, entry = file_path(made["id"], made["files"][0]["id"], self.path)
        self.assertEqual(path.read_bytes(), b"hello")
        self.assertEqual(entry["name"], "note.txt")
        delete_memo(made["id"], self.path)
        self.assertFalse((self.path.parent / "memos" / made["id"]).exists())

    def test_removing_an_attachment_deletes_the_file(self):
        made = create_memo("留着文字", self.path, "测试", uploads=[("book.pdf", "application/pdf", b"%PDF-1.4")])
        file_id = made["files"][0]["id"]
        stored = self.path.parent / "memos" / made["id"] / f"{file_id}.pdf"
        self.assertTrue(stored.is_file())
        cleared = delete_file(made["id"], file_id, self.path)
        self.assertEqual(cleared["files"], [])
        self.assertFalse(stored.exists())
        self.assertFalse((self.path.parent / "memos" / made["id"]).exists())

    def test_list_cleans_orphan_files(self):
        made = create_memo("文字", self.path, "测试")
        folder = self.path.parent / "memos" / made["id"]
        folder.mkdir(parents=True)
        orphan = folder / "deadbeefdeadbeef.pdf"
        orphan.write_bytes(b"%PDF-orphan")
        listed = list_memos(self.path)
        self.assertEqual(listed[0]["files"], [])
        self.assertFalse(orphan.exists())
        self.assertFalse(folder.exists())

    def test_audio_video_image_and_ebook_kinds(self):
        made = create_memo(
            "带附件",
            self.path,
            "媒体",
            uploads=[
                ("a.mp3", "audio/mpeg", b"audio-bytes"),
                ("b.mp4", "video/mp4", b"video-bytes"),
                ("c.jpg", "image/jpeg", b"jpeg-bytes"),
                ("book.pdf", "application/pdf", b"%PDF-1.4"),
                ("note.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK"),
            ],
        )
        kinds = [item["kind"] for item in made["files"]]
        self.assertEqual(kinds, ["audio", "video", "image", "ebook", "doc"])

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

    def test_upload_and_download(self):
        created = self.client.post(
            "/api/memos",
            data={"title": "录音", "text": "听这段"},
            files=[("files", ("clip.mp3", b"mp3-data", "audio/mpeg"))],
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertEqual(len(body["files"]), 1)
        file_id = body["files"][0]["id"]
        downloaded = self.client.get(f"/api/memos/{body['id']}/files/{file_id}")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b"mp3-data")
        self.assertEqual(downloaded.headers.get("accept-ranges"), "bytes")
        more = self.client.post(
            f"/api/memos/{body['id']}/files",
            files=[("files", ("clip.mp4", b"0123456789abcdef", "video/mp4"))],
        )
        self.assertEqual(more.status_code, 200)
        self.assertEqual(len(more.json()["files"]), 2)
        video_id = more.json()["files"][1]["id"]
        partial = self.client.get(
            f"/api/memos/{body['id']}/files/{video_id}",
            headers={"Range": "bytes=0-3"},
        )
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b"0123")
        self.assertEqual(partial.headers.get("accept-ranges"), "bytes")
        self.assertTrue((partial.headers.get("content-range") or "").startswith("bytes 0-3/"))
        dropped = self.client.delete(f"/api/memos/{body['id']}/files/{file_id}")
        self.assertEqual(dropped.status_code, 200)
        self.assertEqual(len(dropped.json()["files"]), 1)
        self.assertFalse((self.path.parent / "memos" / body["id"] / f"{file_id}.mp3").exists())
        self.client.delete(f"/api/memos/{body['id']}")
        self.assertFalse((self.path.parent / "memos" / body["id"]).exists())
