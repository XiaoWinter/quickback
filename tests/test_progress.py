import json
import tempfile
import unittest
from pathlib import Path

from app.gemini_client import ModelError, parse_progress_score
from app.progress import (
    ProgressStore,
    chinese_score_scale,
    chinese_share,
    clamp_scores_for_chinese,
    score_payload,
    summarize,
    weighted_raw,
)
from app.prompts import build_progress_prompt, question_form
from app.qwen_client import QwenClient, progress_content
from app.storage import SessionStore, StorageError


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
        self.assertEqual(weighted_raw(80, 70, 60, 100), 80)

    def test_chinese_share_scales_scores_down_to_zero(self):
        self.assertEqual(chinese_score_scale(0), 1.0)
        self.assertEqual(chinese_score_scale(1), 0.0)
        self.assertGreater(chinese_score_scale(0.2), chinese_score_scale(0.6))
        all_zh = "那时候我喜欢听中文歌，后来风格一直在变。"
        self.assertEqual(chinese_share(all_zh), 1.0)
        zeroed = clamp_scores_for_chinese(
            {"comprehensibility": 80, "relevance": 90, "fluency": 70, "content": 85},
            all_zh,
        )
        self.assertEqual(zeroed, {"comprehensibility": 0, "relevance": 0, "fluency": 0, "content": 0})
        all_zh_scored = score_payload(
            {
                "scores": {"comprehensibility": 80, "relevance": 90, "fluency": 70, "content": 85},
                "notes": {
                    "comprehensibility": "听得懂。",
                    "relevance": "答到了。",
                    "fluency": "顺。",
                    "content": "有细节。",
                },
                "fixes": [],
            },
            all_zh,
        )
        self.assertEqual(all_zh_scored["raw"], 0)
        self.assertIn("全文中文", all_zh_scored["notes"]["fluency"])

        mixed = (
            "When I was young, I like pop music. I like流行音乐嗯。"
            "那时候我喜欢听听的是中文的一些歌手。然后后来的话，我就逐渐听了一些啊，"
            "国外的流行歌手，比如说像Sabrina Carpenter, Chaplin,Taylor Swift。"
            "我不知道他们的歌具体是什么类别。但问题嗯，在这个问题上，我的回答肯定是。"
            "不是。因为我的喜欢的风格一直在变。"
        )
        share = chinese_share(mixed)
        self.assertGreaterEqual(share, 0.5)
        scored = score_payload(
            {
                "scores": {"comprehensibility": 55, "relevance": 90, "fluency": 45, "content": 85},
                "notes": {
                    "comprehensibility": "听得懂。",
                    "relevance": "答到了。",
                    "fluency": "有磕绊。",
                    "content": "有细节。",
                },
                "fixes": [],
            },
            mixed,
        )
        self.assertLess(scored["raw"], 25)
        self.assertLess(scored["raw"], weighted_raw(55, 90, 45, 85))
        self.assertIn("按比例压低", scored["notes"]["fluency"])

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

    def test_parse_keeps_every_sentence_of_the_corrected_answer(self):
        said = "They love hay. And when I open the fridge, they squeak."
        data = parse_progress_score(
            """
            {
              "comprehensibility": 90,
              "relevance": 90,
              "fluency": 80,
              "natural": "They love hay. And when I open the fridge, they squeak.",
              "natural_zh": "它们喜欢干草。我打开冰箱时，它们会叫。"
            }
            """,
            said,
        )
        self.assertIn("fridge", data["natural"])
        self.assertIn("冰箱", data["natural_zh"])

    def test_parse_keeps_a_content_score_apart_from_fluency(self):
        data = parse_progress_score(
            """
            {
              "comprehensibility": 80,
              "relevance": 80,
              "fluency": 50,
              "content": 100,
              "content_note": "有一个能看见的场面，而且贴题。"
            }
            """
        )
        self.assertEqual(data["scores"]["content"], 100)
        self.assertIn("场面", data["notes"]["content"])
        self.assertEqual(data["scores"]["fluency"], 50)

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

    def test_rescore_updates_a_finished_run(self):
        question = {"id": "animals", "en": "What animals do you like?", "zh": "你喜欢什么动物？"}
        with tempfile.TemporaryDirectory() as tmp:
            store = ProgressStore(Path(tmp))
            opened = store.open_run(question)
            store.save_pending(opened["id"], b"x" * 500, "audio/webm", 4000)
            first = _scores(90, 90, 90)
            first["natural"] = "I like cats."
            first["native"] = "I like cats."
            store.commit_answer(opened["id"], "I like cats.", first)
            store.finish(opened["id"])
            before = next(item for item in store.page(question)["runs"] if item["id"] == opened["id"])
            self.assertEqual(before["stats"]["average"], 90)
            ctx = store.rescore_context(opened["id"], 1)
            self.assertEqual(ctx["transcript"], "I like cats.")
            self.assertEqual(ctx["audio"], b"x" * 500)
            self.assertEqual(ctx["earlier"], [])
            folder = Path(tmp) / "animals" / opened["id"]
            (folder / "01-corrected.wav").write_bytes(b"RIFF")
            (folder / "01-native.wav").write_bytes(b"RIFF")
            raw = json.loads((folder / "run.json").read_text(encoding="utf-8"))
            raw["answers"][0]["corrected_file"] = "01-corrected.wav"
            raw["answers"][0]["native_file"] = "01-native.wav"
            (folder / "run.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            lowered = _scores(60, 70, 50)
            lowered["natural"] = "I like cats."
            lowered["scores"]["content"] = 70
            lowered["notes"]["content"] = "有一点内容。"
            store.apply_rescore(opened["id"], 1, "I like cats.", lowered)
            after = next(item for item in store.page(question)["runs"] if item["id"] == opened["id"])
            expected = weighted_raw(60, 70, 50, 70)
            self.assertEqual(after["answers"][0]["raw"], expected)
            self.assertEqual(after["stats"]["average"], expected)
            self.assertFalse((folder / "01-corrected.wav").exists())
            self.assertFalse((folder / "01-native.wav").exists())
            self.assertEqual(store.answer_numbers(opened["id"]), [1])

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

    def test_corrected_line_keeps_the_later_sentence_and_its_audio(self):
        question = {"id": "animals", "en": "What animals do you like?", "zh": "你喜欢什么动物？"}
        with tempfile.TemporaryDirectory() as tmp:
            store = ProgressStore(Path(tmp))
            opened = store.open_run(question)
            store.save_pending(opened["id"], b"x" * 500, "audio/webm", 4000)
            parsed = _scores(80, 80, 80)
            parsed["natural"] = "They love hay. They squeak when I open the fridge."
            parsed["natural_zh"] = "它们喜欢干草。我打开冰箱时它们会叫。"
            store.commit_answer(opened["id"], "They love hay. They squeak.", parsed)
            store.save_corrected(opened["id"], 1, b"RIFFdemo")
            page = store.page(question)["open"]["answers"][0]
            self.assertIn("fridge", page["natural"])
            self.assertTrue(page["has_corrected"])
            self.assertTrue(store.corrected_path(opened["id"], 1).is_file())
            self.assertEqual(store.question_id_for(opened["id"]), "animals")
            self.assertIn("fridge", store.corrected_text(opened["id"], 1))


class CorrectedSpeechTests(unittest.TestCase):
    def _prompt(self, **extra):
        fields = dict(
            topic_en="animals",
            topic_zh="动物",
            prompt_en="What animals do you like?",
            prompt_zh="你喜欢什么动物？",
            transcript="I like cats.",
            duration_ms=4000,
            earlier=[],
        )
        fields.update(extra)
        return build_progress_prompt(**fields)

    def test_prompt_mentions_the_corrected_recording_only_when_attached(self):
        attached = self._prompt(reference=True)
        plain = self._prompt()
        self.assertIn("corrected version", attached)
        self.assertNotIn("corrected version", plain)
        self.assertIn("Do not drop a point", plain)
        self.assertIn("This question is open", plain)
        closed = self._prompt(prompt_en="Do you give them fresh hay every day?", prompt_zh="你每天都会给它们新鲜的干草吗？")
        self.assertIn("This question is closed", closed)
        self.assertNotIn("visible scene", closed)

    def test_prompt_mentions_the_learner_recording_only_when_attached(self):
        with_audio = self._prompt(answer_audio=True)
        plain = self._prompt()
        self.assertIn("learner's spoken recording", with_audio)
        self.assertNotIn("learner's spoken recording", plain)
        both = self._prompt(answer_audio=True, reference=True)
        self.assertIn("after the learner recording", both)
        self.assertIn("Prefer the attached learner recording", with_audio)

    def test_prompt_lowers_scores_when_chinese_is_used(self):
        plain = self._prompt()
        self.assertIn("Chinese penalty", plain)
        self.assertIn("Heavy Chinese use must lower fluency", plain)
        self.assertNotIn("Chinese mixed in can still score high", plain)
        self.assertIn("Chinese fragment may appear as spoken", plain)

    def test_prompt_calibrates_scores_to_ielts_bands(self):
        plain = self._prompt()
        self.assertIn("80-100 ≈ IELTS 7-8", plain)
        self.assertIn("60-80 ≈ IELTS 6-7", plain)
        self.assertIn("40-60 ≈ IELTS 5-6", plain)
        self.assertIn("Reserve 80-100", plain)
        self.assertIn("Do not cluster scores", plain)

    def test_question_form_keeps_a_direct_question_closed(self):
        self.assertEqual(question_form("Do you give them fresh hay every day?"), "closed")
        self.assertEqual(question_form("How often do you check on the hay supply?"), "closed")
        self.assertEqual(question_form("what animals do you like?"), "open")
        self.assertEqual(question_form("What do your guinea pigs like to eat the most?"), "open")

    def test_qwen_content_keeps_the_wav_beside_the_prompt(self):
        content, answer_ok, ref_ok = progress_content("score this", answer=b"RIFF", answer_mime="audio/wav")
        self.assertTrue(answer_ok)
        self.assertFalse(ref_ok)
        self.assertEqual(content[0]["text"], "score this")
        self.assertEqual(content[1]["type"], "input_audio")
        self.assertTrue(content[1]["input_audio"]["data"].startswith("data:audio/wav;base64,"))
        plain, no_answer, no_ref = progress_content("score this")
        self.assertEqual(plain, "score this")
        self.assertFalse(no_answer)
        self.assertFalse(no_ref)
        both, answer_ok, ref_ok = progress_content(
            "score this",
            answer=b"ANS",
            answer_mime="audio/wav",
            reference=b"REF",
            reference_mime="audio/wav",
        )
        self.assertTrue(answer_ok and ref_ok)
        self.assertEqual(len(both), 3)
        self.assertEqual(both[1]["type"], "input_audio")
        self.assertEqual(both[2]["type"], "input_audio")

    def test_qwen_retries_without_audio_when_the_model_refuses_it(self):
        score = (
            '{"comprehensibility": 80, "relevance": 80, "fluency": 80, '
            '"comprehensibility_note": "听得懂。", "relevance_note": "答到了。", "fluency_note": "顺。", "fixes": []}'
        )

        class Stub(QwenClient):
            def __init__(self):
                super().__init__("k", "qwen-flash", "asr", "tts", "Cherry", "https://example.invalid")
                self.calls = []

            def _post(self, url, payload):
                content = payload["messages"][0]["content"]
                self.calls.append(content if isinstance(content, str) else "audio")
                if not isinstance(content, str):
                    raise ModelError("千问没有接受这次请求。录音已留下，可以点重试。")
                return {"choices": [{"message": {"content": score}}]}

        client = Stub()
        parsed = client.progress_score(
            topic_en="animals",
            topic_zh="动物",
            prompt_en="What animals do you like?",
            prompt_zh="你喜欢什么动物？",
            transcript="I like cats.",
            duration_ms=4000,
            earlier=[],
            audio=b"ANS",
            audio_mime="audio/wav",
            reference=b"RIFF",
            reference_mime="audio/wav",
        )
        self.assertEqual(client.calls[0], "audio")
        self.assertIn("Transcript", client.calls[1])
        self.assertNotIn("learner's spoken recording", client.calls[1])
        self.assertNotIn("corrected version", client.calls[1])
        self.assertEqual(parsed["scores"]["fluency"], 80)
        self.assertTrue(client._skip_progress_audio)

    def test_lookup_uses_the_matching_practice_turn(self):
        session_id = "a" * 32
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / session_id
            folder.mkdir()
            (folder / "turn-01-speech.wav").write_bytes(b"RIFF")
            (folder / "session.json").write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "updated_at": "2026-11-08T00:00:00",
                        "question": {"id": "animals"},
                        "turns": [
                            {
                                "index": 1,
                                "prompt": {"en": "What animals do you like?", "zh": "你喜欢什么动物？"},
                                "speech_file": "turn-01-speech.wav",
                                "speech_mime": "audio/wav",
                                "feedback": {"natural": "I like guinea pigs."},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            found = SessionStore(root).corrected_speech("animals", "What animals do you like?")
            self.assertEqual(found["session_id"], session_id)
            self.assertEqual(found["index"], 1)
            self.assertTrue(found["has_speech"])
            self.assertEqual(found["audio"], b"RIFF")
            skipped = SessionStore(root).corrected_speech("animals", "What animals do you like?", load=False)
            self.assertEqual(skipped["audio"], b"")
            self.assertIsNone(SessionStore(root).corrected_speech("animals", "Something else?"))


if __name__ == "__main__":
    unittest.main()
