import json
import tempfile
import unittest
from pathlib import Path

from app.gemini_client import ModelError, parse_progress_score
from app.progress import ProgressStore, summarize, weighted_raw
from app.storage import StorageError


def _scores(comp, rel, flu):
    return {
        "scores": {"comprehensibility": comp, "relevance": rel, "fluency": flu},
        "notes": {
            "comprehensibility": "听得懂。",
            "relevance": "答到了。",
            "fluency": "有一点磕绊。",
        },
        "follow_up": "Do you have one?",
        "follow_up_zh": "你养了吗？",
    }


class ScoreTests(unittest.TestCase):
    def test_weights(self):
        self.assertEqual(weighted_raw(80, 70, 60), 71)

    def test_summary_ignores_follow_up_bonus(self):
        answers = [
            {"raw": 80, "bonus": 0},
            {"raw": 100, "bonus": 20},
            {"raw": 100, "bonus": 20},
            {"raw": 100, "bonus": 20},
        ]
        stats = summarize(answers)
        self.assertEqual(stats["raw"], 380)
        self.assertEqual(stats["bonus"], 380)
        self.assertEqual(stats["average"], 95)
        self.assertEqual(stats["count"], 4)

    def test_parse_keeps_chinese_notes_and_clamps_scores(self):
        data = parse_progress_score(
            """
            {
              "comprehensibility": 140,
              "relevance": "80",
              "fluency": -4,
              "comprehensibility_note": "這個回答聽得懂。",
              "relevance_note": "Answered it.",
              "fluency_note": "有重复。",
              "follow_up": "What happened next?",
              "follow_up_zh": "後來呢？"
            }
            """
        )
        self.assertEqual(data["scores"]["comprehensibility"], 100)
        self.assertEqual(data["scores"]["relevance"], 80)
        self.assertEqual(data["scores"]["fluency"], 0)
        self.assertIn("这个", data["notes"]["comprehensibility"])
        self.assertEqual(data["notes"]["relevance"], "")
        self.assertEqual(data["follow_up"], "What happened next?")
        self.assertIn("后来", data["follow_up_zh"])

    def test_parse_drops_a_follow_up_that_is_not_english(self):
        data = parse_progress_score(
            '{"comprehensibility": 60, "relevance": 60, "fluency": 60, "follow_up": "你好"}'
        )
        self.assertEqual(data["follow_up"], "")

    def test_parse_rejects_a_missing_score(self):
        with self.assertRaises(ModelError):
            parse_progress_score('{"comprehensibility": 60, "fluency": 60}')

    def test_parse_keeps_a_real_fix_and_drops_invented_ones(self):
        said = "My grandpa he go to the village yesterday, and he rarely spoke to anyone."
        data = parse_progress_score(
            """
            {
              "comprehensibility": 70,
              "relevance": 80,
              "fluency": 60,
              "fixes": [
                {"spoken": "he go", "better": "he went", "note": "講過去的事，用 went。"},
                {"spoken": "he fly", "better": "he flew", "note": "这句里没有。"},
                {"spoken": "My grandpa he go to the village yesterday, and he rarely spoke to anyone.", "better": "My grandpa went to the village yesterday, and he rarely spoke to anyone.", "note": "整句重写。"},
                "not an object",
                {"spoken": "rarely spoke", "better": "rarely spoke", "note": "没改。"}
              ]
            }
            """,
            said,
        )
        self.assertEqual(len(data["fixes"]), 1)
        self.assertEqual(data["fixes"][0]["spoken"], "he go")
        self.assertEqual(data["fixes"][0]["better"], "he went")
        self.assertIn("讲过去", data["fixes"][0]["note"])

    def test_parse_without_fixes_is_an_empty_list(self):
        data = parse_progress_score(
            '{"comprehensibility": 60, "relevance": 60, "fluency": 60}',
            "I like cats.",
        )
        self.assertEqual(data["fixes"], [])


class StoreTests(unittest.TestCase):
    def test_runs_keep_their_own_question_count(self):
        question = {"id": "animals", "en": "What animals do you like?", "zh": "你喜欢什么动物？"}
        with tempfile.TemporaryDirectory() as tmp:
            store = ProgressStore(Path(tmp))
            first = self._answer(store, question, [(90, "I like cats.")])
            store.finish(first)
            saved = json.loads((Path(tmp) / "animals" / first / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["stats"]["count"], 1)
            self.assertEqual(saved["stats"]["raw"], 90)
            self.assertEqual(saved["stats"]["bonus"], 90)
            self.assertEqual(saved["answers"][0]["bonus"], 0)

            second = store.open_run(question)
            store.save_pending(second["id"], b"x" * 500, "audio/webm", 4000)
            store.add_draft(second["id"], "先说猫")
            store.commit_answer(second["id"], "I have one cat.", _scores(80, 80, 80))
            self.assertIsNone(store.page(question)["open"]["pending_prompt"])
            self.assertEqual(store.page(question)["open"]["answers"][0]["drafts"], ["先说猫"])
            self.assertEqual(store.page(question)["open"]["drafts"], [])
            store.align_prompts(question["id"], [
                {"en": "What animals do you like?", "zh": "你喜欢什么动物？"},
                {"en": "How often do you check?", "zh": "你多久检查一次？"},
            ])
            asked = store.page(question)["open"]["pending_prompt"]
            self.assertEqual(asked["en"], "How often do you check?")
            store.add_draft(second["id"], "每天看一次")
            store.save_pending(second["id"], b"y" * 500, "audio/webm", 4000)
            extra = _scores(70, 70, 70)
            extra["follow_up"] = "And then?"
            extra["follow_up_zh"] = "然后呢？"
            store.commit_answer(second["id"], "Yes, a cat.", extra)
            self.assertIsNone(store.page(question)["open"]["pending_prompt"])
            with self.assertRaises(StorageError):
                store.save_pending(second["id"], b"z" * 500, "audio/webm", 4000)
            store.save_pending(second["id"], b"w" * 500, "audio/webm", 3000, 1)
            store.commit_answer(second["id"], "I like dogs.", _scores(60, 60, 60))
            replaced = store.page(question)["open"]
            self.assertEqual(replaced["answers"][0]["transcript"], "I like dogs.")
            self.assertEqual(replaced["answers"][0]["drafts"], ["先说猫"])
            self.assertEqual(replaced["answers"][1]["prompt"]["en"], "How often do you check?")
            self.assertIsNone(replaced["pending_prompt"])
            store.finish(second["id"])
            again = json.loads((Path(tmp) / "animals" / first / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(again["stats"], saved["stats"])
            later = json.loads((Path(tmp) / "animals" / second["id"] / "run.json").read_text(encoding="utf-8"))
            self.assertIsNone(later["pending_prompt"])
            self.assertEqual(later["stats"]["raw"], 130)
            self.assertEqual(later["stats"]["bonus"], 130)
            self.assertEqual(later["stats"]["average"], 65)
            self.assertEqual(later["stats"]["count"], 2)
            self.assertEqual(later["answers"][0]["bonus"], 0)
            self.assertEqual(later["answers"][0]["prompt"]["en"], "What animals do you like?")
            self.assertEqual(later["answers"][0]["drafts"], ["先说猫"])
            self.assertEqual(later["answers"][1]["bonus"], 0)
            self.assertEqual(later["answers"][1]["drafts"], ["每天看一次"])
            self.assertEqual(later["answers"][1]["prompt"]["en"], "How often do you check?")
            charts = store.charts()["animals"]
            self.assertEqual(sorted(item["count"] for item in charts), [1, 2])
            stale = json.loads((Path(tmp) / "animals" / second["id"] / "run.json").read_text(encoding="utf-8"))
            stale["stats"]["raw"] = 60
            stale["stats"]["bonus"] = 60
            (Path(tmp) / "animals" / second["id"] / "run.json").write_text(
                json.dumps(stale, ensure_ascii=False),
                encoding="utf-8",
            )
            shown = next(item["stats"] for item in store.page(question)["runs"] if item["id"] == second["id"])
            self.assertEqual(shown["raw"], 130)
            chart = next(item for item in store.charts()["animals"] if item["id"] == second["id"])
            self.assertEqual(chart["raw"], 130)
            self.assertFalse((Path(tmp) / "animals" / second["id"] / "pending.webm").exists())
            self.assertTrue((Path(tmp) / "animals" / second["id"] / "02.webm").is_file())

    def test_open_run_follows_answered_practice_turns(self):
        question = {"id": "animals", "en": "What animals do you like?", "zh": "你喜欢什么动物？"}
        prompts = [
            {"en": "What animals do you like?", "zh": "你喜欢什么动物？"},
            {"en": "What do they eat?", "zh": "它们吃什么？"},
            {"en": "Do you give hay?", "zh": "你给干草吗？"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = ProgressStore(Path(tmp))
            opened = store.open_run(question)
            store.add_draft(opened["id"], "frequency")
            store.align_prompts(question["id"], prompts)
            self.assertEqual(store.page(question)["open"]["drafts"], ["frequency"])
            store.save_pending(opened["id"], b"a" * 500, "audio/webm", 4000)
            store.align_prompts(question["id"], prompts + [{"en": "How often?", "zh": "多久？"}])
            store.commit_answer(opened["id"], "I like cats.", _scores(80, 80, 80))
            store.add_draft(opened["id"], "frequency")
            store.align_prompts(question["id"], prompts)
            page = store.page(question)["open"]
            self.assertEqual(page["pending_prompt"]["en"], "What do they eat?")
            self.assertEqual(page["drafts"], [])
            store.save_pending(opened["id"], b"b" * 500, "audio/webm", 4000)
            store.commit_answer(opened["id"], "Hay.", _scores(70, 70, 70))
            store.align_prompts(question["id"], prompts)
            self.assertEqual(store.page(question)["open"]["pending_prompt"]["en"], "Do you give hay?")
            store.save_pending(opened["id"], b"c" * 500, "audio/webm", 4000)
            store.commit_answer(opened["id"], "Every day.", _scores(70, 70, 70))
            store.align_prompts(question["id"], prompts)
            self.assertIsNone(store.page(question)["open"]["pending_prompt"])

    def test_draft_prompts_stay_on_the_open_run(self):
        question = {"id": "animals", "en": "What animals do you like?", "zh": "你喜欢什么动物？"}
        with tempfile.TemporaryDirectory() as tmp:
            store = ProgressStore(Path(tmp))
            opened = store.open_run(question)
            self.assertEqual(opened["drafts"], [])
            store.add_draft(opened["id"], "  爷爷很安静  ")
            with self.assertRaises(StorageError):
                store.add_draft(opened["id"], "爷爷很安静")
            with self.assertRaises(StorageError):
                store.add_draft(opened["id"], "   ")
            store.add_draft(opened["id"], "x" * 200)
            page = store.page(question)
            self.assertEqual(page["open"]["drafts"][0], "爷爷很安静")
            self.assertEqual(len(page["open"]["drafts"][1]), 120)
            store.remove_draft(opened["id"], 0)
            self.assertEqual(store.page(question)["open"]["drafts"], ["x" * 120])
            for number in range(7):
                store.add_draft(opened["id"], f"提示 {number}")
            with self.assertRaises(StorageError):
                store.add_draft(opened["id"], "再多一条")

    def test_finish_needs_an_answer_and_discard_removes_the_draft(self):
        question = {"id": "animals", "en": "What animals do you like?", "zh": "你喜欢什么动物？"}
        with tempfile.TemporaryDirectory() as tmp:
            store = ProgressStore(Path(tmp))
            opened = store.open_run(question)
            again = store.open_run(question)
            self.assertEqual(opened["id"], again["id"])
            with self.assertRaises(StorageError):
                store.finish(opened["id"])
            store.discard(opened["id"])
            self.assertFalse((Path(tmp) / "animals").exists())
            self.assertEqual(store.page(question)["open"], None)
            self.assertEqual(store.charts(), {})

    def _answer(self, store, question, turns):
        opened = store.open_run(question)
        for comp, transcript in turns:
            store.save_pending(opened["id"], b"x" * 500, "audio/webm", 4000)
            parsed = _scores(comp, comp, comp)
            if comp < 50:
                parsed["follow_up"] = ""
                parsed["follow_up_zh"] = ""
            store.commit_answer(opened["id"], transcript, parsed)
        return opened["id"]

    def test_fixes_stay_on_the_answer_and_old_answers_stay_blank(self):
        question = {"id": "animals", "en": "What animals do you like?", "zh": "你喜欢什么动物？"}
        with tempfile.TemporaryDirectory() as tmp:
            store = ProgressStore(Path(tmp))
            opened = store.open_run(question)
            store.save_pending(opened["id"], b"x" * 500, "audio/webm", 4000)
            parsed = _scores(80, 80, 80)
            parsed["fixes"] = [{"spoken": "like cats", "better": "like quiet cats", "note": "可以更具体。"}]
            store.commit_answer(opened["id"], "I like cats.", parsed)
            page = store.page(question)
            self.assertEqual(page["open"]["answers"][0]["fixes"][0]["better"], "like quiet cats")
            saved = json.loads((Path(tmp) / "animals" / opened["id"] / "run.json").read_text(encoding="utf-8"))
            saved["answers"][0].pop("fixes")
            (Path(tmp) / "animals" / opened["id"] / "run.json").write_text(
                json.dumps(saved, ensure_ascii=False),
                encoding="utf-8",
            )
            again = ProgressStore(Path(tmp)).page(question)
            self.assertIsNone(again["open"]["answers"][0]["fixes"])


if __name__ == "__main__":
    unittest.main()
