import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import questions as questions_mod
from app.main import app


class IeltsQuestionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "questions.json"
        self.path.write_text(
            '[{"id":"seed","en":"Seed?","zh":"种子？"}]\n',
            encoding="utf-8",
        )
        self.original = questions_mod.QUESTIONS_PATH
        questions_mod.QUESTIONS_PATH = self.path
        self.client = TestClient(app)

    def tearDown(self):
        questions_mod.QUESTIONS_PATH = self.original
        self.tmp.cleanup()

    def test_create_with_category(self):
        created = self.client.post(
            "/api/questions",
            json={"en": "Do you cook at home?", "zh": "你在家做饭吗？", "category": "口语1"},
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["category"], "口语1")

    def test_update_category(self):
        created = self.client.post(
            "/api/questions",
            json={"en": "Do you cook at home?", "zh": "你在家做饭吗？"},
        ).json()
        self.assertEqual(created["category"], "")
        updated = self.client.put(
            f"/api/questions/{created['id']}",
            json={"en": created["en"], "zh": created["zh"], "category": "口语2"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["category"], "口语2")
        listed = self.client.get("/api/questions").json()
        match = next(item for item in listed if item["id"] == created["id"])
        self.assertEqual(match["category"], "口语2")

    def test_legacy_list_has_empty_category(self):
        listed = self.client.get("/api/questions")
        self.assertEqual(listed.status_code, 200)
        seed = next(item for item in listed.json() if item["id"] == "seed")
        self.assertEqual(seed["category"], "")

    def test_ielts_speaking_generates_three_tagged(self):
        fake = MagicMock()
        fake.json_object.return_value = {
            "part1": {"en": "What do you usually do on weekends?", "zh": "你周末通常做什么？"},
            "part2": {"en": "Describe a place you like to visit.", "zh": "描述一个你喜欢去的地方。"},
            "part3": {"en": "Why do people travel?", "zh": "人们为什么旅行？"},
        }
        with patch("app.main._client", return_value=fake):
            response = self.client.post("/api/questions/ielts-speaking", json={})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body), 3)
        self.assertEqual([item["category"] for item in body], ["口语1", "口语2", "口语3"])
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        cats = {item["id"]: item.get("category", "") for item in saved}
        for item in body:
            self.assertEqual(cats[item["id"]], item["category"])

    def test_ielts_speaking_refresh_one_part(self):
        first = MagicMock()
        first.json_object.return_value = {
            "part1": {"en": "Do you like tea?", "zh": "你喜欢喝茶吗？"},
            "part2": {"en": "Describe your favorite drink.", "zh": "描述你最喜欢的饮料。"},
            "part3": {"en": "Why do drinks matter socially?", "zh": "饮品为何有社交意义？"},
        }
        one = MagicMock()
        one.json_object.return_value = {
            "en": "Do you prefer coffee in the morning?",
            "zh": "你早上更喜欢喝咖啡吗？",
        }
        with patch("app.main._client", return_value=first):
            created = self.client.post("/api/questions/ielts-speaking", json={}).json()
        old_part1 = created[0]["id"]
        with patch("app.main._client", return_value=one):
            refreshed = self.client.post(
                "/api/questions/ielts-speaking",
                json={"part": "part1", "replace_id": old_part1},
            )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        body = refreshed.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["category"], "口语1")
        self.assertNotEqual(body[0]["id"], old_part1)
        saved_ids = {item["id"] for item in json.loads(self.path.read_text(encoding="utf-8"))}
        self.assertNotIn(old_part1, saved_ids)
        self.assertIn(body[0]["id"], saved_ids)
        self.assertIn(created[1]["id"], saved_ids)
        self.assertIn(created[2]["id"], saved_ids)

    def test_ielts_speaking_replace_swaps_batch(self):
        first = MagicMock()
        first.json_object.return_value = {
            "part1": {"en": "Do you like tea?", "zh": "你喜欢喝茶吗？"},
            "part2": {"en": "Describe your favorite drink.", "zh": "描述你最喜欢的饮料。"},
            "part3": {"en": "Why do drinks matter socially?", "zh": "饮品为何有社交意义？"},
        }
        second = MagicMock()
        second.json_object.return_value = {
            "part1": {"en": "Do you prefer coffee?", "zh": "你更喜欢咖啡吗？"},
            "part2": {"en": "Describe a cafe you enjoy.", "zh": "描述一家你喜欢的咖啡馆。"},
            "part3": {"en": "How has cafe culture changed?", "zh": "咖啡馆文化如何变化？"},
        }
        with patch("app.main._client", return_value=first):
            created = self.client.post("/api/questions/ielts-speaking", json={}).json()
        old_ids = [item["id"] for item in created]
        with patch("app.main._client", return_value=second):
            replaced = self.client.post(
                "/api/questions/ielts-speaking",
                json={"replace_ids": old_ids},
            )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        new_ids = [item["id"] for item in replaced.json()]
        self.assertEqual(len(new_ids), 3)
        saved_ids = {item["id"] for item in json.loads(self.path.read_text(encoding="utf-8"))}
        for qid in old_ids:
            self.assertNotIn(qid, saved_ids)
        for qid in new_ids:
            self.assertIn(qid, saved_ids)
