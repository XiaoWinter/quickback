import json
import tempfile
import unittest
from pathlib import Path

from app.gemini_client import (
    GeminiError,
    apply_review_policy,
    parse_feedback,
    transcript_from_response,
    wav_from_response,
)
from app.practice import PracticeStore
from app.providers import ProviderStore
from app.prompts import build_feedback_prompt
from app.qwen_client import audio_url_from_qwen, chat_url, contains_cjk, transcript_from_qwen, usable_audio_url
from app.questions import (
    QuestionError,
    backfill_timestamps,
    create_question,
    delete_question,
    load_questions,
    next_question,
    update_question,
)
from app.segments import split_transcript
from app.storage import SessionStore, StorageError


class SegmentTests(unittest.TestCase):
    def test_mixed_speech_keeps_both_languages(self):
        parts = split_transcript("I like 火锅, especially 牛肉.")
        text = "".join(part["text"] for part in parts)
        self.assertEqual(text, "I like 火锅, especially 牛肉.")
        langs = [part["lang"] for part in parts]
        self.assertEqual(langs[0], "en")
        self.assertIn("zh", langs)
        self.assertIn("en", langs[1:])

    def test_chinese_comma_stays_with_chinese(self):
        parts = split_transcript("Hello，世界")
        self.assertEqual(parts[0]["lang"], "en")
        self.assertEqual(parts[0]["text"], "Hello")
        self.assertEqual(parts[1]["lang"], "zh")
        self.assertEqual(parts[1]["text"], "，世界")

    def test_empty(self):
        self.assertEqual(split_transcript(""), [])


class FeedbackTests(unittest.TestCase):
    def test_parse_keeps_separate_extensions(self):
        raw = """
        {
          "natural": "I like noodles, but they're a bit expensive.",
          "changes": [{"spoken": "但是有点贵", "better": "but they're a bit expensive", "note": "卡住了"}],
          "extensions": [{"line": "I have them about once a week.", "note": "补频率"}],
          "follow_up": "Where do you usually get them?",
          "follow_up_zh": "你一般在哪吃？"
        }
        """
        data = parse_feedback(raw)
        self.assertTrue(data["natural"].startswith("I like noodles"))
        self.assertEqual(data["natural_zh"], "")
        self.assertEqual(data["spoken_zh"], "")
        self.assertEqual(len(data["extensions"]), 1)
        self.assertNotIn("once a week", data["natural"])

    def test_parse_keeps_the_chinese_under_the_answer(self):
        raw = """
        {
          "natural": "Yesterday I ate instant noodles.",
          "natural_zh": "昨天我吃了方便面。",
          "spoken_zh": "昨天我吃了快速方便面。",
          "changes": [],
          "extensions": [],
          "follow_up": "Did you add anything?",
          "follow_up_zh": "你加了什么吗？"
        }
        """
        data = parse_feedback(raw)
        self.assertEqual(data["natural_zh"], "昨天我吃了方便面。")
        self.assertEqual(data["spoken_zh"], "昨天我吃了快速方便面。")

    def test_transcript_comes_from_audio_transcription(self):
        class Transcription:
            text = "我想练习口语"
            words = None

        class Part:
            audio_transcription = Transcription()
            text = None

        class Content:
            parts = [Part()]

        class Candidate:
            content = Content()

        class Response:
            candidates = [Candidate()]
            text = ""

        self.assertEqual(transcript_from_response(Response()), "我想练习口语")

    def test_wav_comes_from_audio_part(self):
        class Inline:
            data = b"RIFF-demo"
            mime_type = "audio/wav"

        class Part:
            inline_data = Inline()

        class Content:
            parts = [Part()]

        class Candidate:
            content = Content()

        class Response:
            candidates = [Candidate()]

        self.assertEqual(wav_from_response(Response()), b"RIFF-demo")

        class Empty:
            candidates = []

        with self.assertRaises(GeminiError):
            wav_from_response(Empty())

    def test_pasted_suggested_answer_is_not_rewritten(self):
        suggested = "I like hot pot, but it's a bit expensive."
        feedback = apply_review_policy(
            {
                "natural": "I am quite fond of hot pot, although it is somewhat pricey.",
                "natural_zh": "我相当喜欢火锅，尽管它有点贵。",
                "spoken_zh": "我喜欢火锅，但是有点贵。",
                "changes": [{"spoken": "I like hot pot", "better": "I am quite fond of hot pot", "note": "再润色"}],
                "review": "还可以把 fond 换成更高级的说法。",
            },
            suggested,
            suggested,
            "我喜欢火锅，但是有点贵。",
        )
        self.assertEqual(feedback["natural"], suggested)
        self.assertEqual(feedback["natural_zh"], "我喜欢火锅，但是有点贵。")
        self.assertEqual(feedback["spoken_zh"], "我喜欢火锅，但是有点贵。")
        self.assertEqual(feedback["changes"], [])
        self.assertEqual(feedback["review"], "")

    def test_punctuation_only_difference_is_still_the_suggested_answer(self):
        suggested = "I like hot pot, but it's a bit expensive."
        feedback = apply_review_policy(
            {
                "natural": "I really enjoy hot pot, though it costs a bit.",
                "changes": [{"spoken": "I like", "better": "I really enjoy", "note": "再润色"}],
                "review": "还可以再改得更书面。",
            },
            "I like hot pot, but it's a bit expensive",
            suggested,
        )
        self.assertEqual(feedback["natural"], suggested)
        self.assertEqual(feedback["review"], "")

    def test_real_gap_keeps_the_review(self):
        feedback = apply_review_policy(
            {
                "natural": "I like sunny weather.",
                "changes": [],
                "review": "问的是这座城市里喜欢的食物，这句没答到问题上。",
            },
            "I like sunny weather.",
            "",
        )
        self.assertIn("没答到问题", feedback["review"])

    def test_parse_rejects_missing_natural(self):
        with self.assertRaises(GeminiError):
            parse_feedback('{"changes": []}')


class StorageTests(unittest.TestCase):
    def test_audio_and_follow_up_are_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            session = store.create({"id": "city-food", "en": "What food?", "zh": "吃什么？"})
            session, index = store.append_audio(session["id"], b"RIFFfake-audio-bytes", "audio/wav", 1800)
            self.assertEqual(index, 1)
            audio, mime = store.audio_path(session["id"], 1)
            self.assertEqual(mime, "audio/wav")
            self.assertTrue(audio.read_bytes().startswith(b"RIFF"))
            updated = store.update_turn(
                session["id"],
                1,
                status="ready",
                transcript="I like 火锅",
                segments=split_transcript("I like 火锅"),
                feedback={
                    "natural": "I like hot pot.",
                    "changes": [],
                    "extensions": [],
                    "follow_up": "Which place do you go to?",
                    "follow_up_zh": "你去哪家？",
                },
                error=None,
            )
            self.assertEqual(updated["pending_prompt"]["en"], "Which place do you go to?")
            again = store.get(session["id"])
            self.assertEqual(again["turns"][0]["transcript"], "I like 火锅")
            spoken = store.save_speech(session["id"], 1, b"RIFFspeech")
            kept = store.keep_existing_speech(session["id"], 1)
            self.assertEqual(kept["turns"][0]["speech_file"], "turn-01-speech.wav")
            raw_path = Path(tmp) / session["id"] / "session.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            raw["turns"][0].pop("speech_file", None)
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            restored = store.keep_existing_speech(session["id"], 1)
            self.assertEqual(restored["turns"][0]["speech_file"], "turn-01-speech.wav")
            self.assertEqual((Path(tmp) / session["id"] / "turn-01-speech.wav").read_bytes(), b"RIFFspeech")
            path, mime = store.speech_path(session["id"], 1)
            self.assertEqual(mime, "audio/wav")
            self.assertEqual(path.read_bytes(), b"RIFFspeech")
            self.assertEqual(spoken["turns"][0]["speech_file"], "turn-01-speech.wav")
            edited = store.update_question_text(session["id"], "What food do you like?", "你喜欢吃什么？")
            self.assertEqual(edited["question"]["zh"], "你喜欢吃什么？")
            self.assertEqual(edited["turns"][0]["prompt"]["en"], "What food?")
            follow = store.update_follow_up(session["id"], "Do you cook it at home?", "你在家做吗？")
            self.assertEqual(follow["pending_prompt"]["en"], "Do you cook it at home?")
            self.assertEqual(follow["turns"][0]["feedback"]["follow_up_zh"], "你在家做吗？")
            cleared = store.clear_follow_up(session["id"])
            self.assertEqual(cleared["pending_prompt"]["en"], "What food?")
            self.assertEqual(cleared["turns"][0]["feedback"]["follow_up"], "")
            with self.assertRaises(StorageError):
                store.update_follow_up(session["id"], "", "没有英文")
            session, second = store.append_audio(session["id"], b"RIFFsecond-audio", "audio/wav", 900)
            self.assertEqual(second, 2)
            store.save_speech(session["id"], 2, b"RIFFspeech-2")
            edited_turn = store.update_answered_turn(
                session["id"],
                1,
                "What did you eat?",
                "你吃了什么？",
                "I like hot pot.",
                "我喜欢火锅。",
            )
            self.assertEqual(edited_turn["turns"][0]["prompt"]["zh"], "你吃了什么？")
            self.assertEqual(edited_turn["turns"][0]["feedback"]["natural_zh"], "我喜欢火锅。")
            removed = store.delete_turn(session["id"], 1)
            self.assertEqual([item["index"] for item in removed["turns"]], [1])
            self.assertEqual(removed["turns"][0]["audio_file"], "turn-01.wav")
            self.assertEqual(removed["turns"][0]["speech_file"], "turn-01-speech.wav")
            kept = store.audio_path(session["id"], 1)[0]
            self.assertEqual(kept.read_bytes(), b"RIFFsecond-audio")
            self.assertFalse((kept.parent / "turn-02.wav").exists())

    def test_deleting_a_question_removes_follow_ups_and_recordings(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            other = store.create({"id": "other", "en": "Other?", "zh": "另一题？"})
            store.append_audio(other["id"], b"RIFFkeep", "audio/wav", 800)
            session = store.create({"id": "animals", "en": "What animals do you like?", "zh": "你喜欢什么动物？"})
            session, _ = store.append_audio(session["id"], b"RIFFanswer", "audio/wav", 1200)
            store.update_turn(
                session["id"],
                1,
                status="ready",
                transcript="I like dogs.",
                feedback={
                    "natural": "I like dogs.",
                    "natural_zh": "我喜欢狗。",
                    "follow_up": "Do you have one?",
                    "follow_up_zh": "你养了吗？",
                },
            )
            store.save_speech(session["id"], 1, b"RIFFspeech")
            store.save_retell(session["id"], 1, b"RIFFretell", "audio/webm")
            folder = Path(tmp) / session["id"]
            self.assertTrue((folder / "turn-01.wav").is_file())
            self.assertTrue((folder / "turn-01-speech.wav").is_file())
            self.assertTrue((folder / "turn-01-retell.webm").is_file())
            removed = store.delete_for_question("animals")
            self.assertEqual(removed, [session["id"]])
            self.assertFalse(folder.exists())
            self.assertTrue((Path(tmp) / other["id"] / "turn-01.wav").is_file())

    def test_retake_replaces_one_turn_and_drops_its_old_speech(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            session = store.create({"id": "city-food", "en": "What food?", "zh": "吃什么？"})
            session, _ = store.append_audio(session["id"], b"RIFFfirst", "audio/wav", 1000)
            store.update_turn(
                session["id"],
                1,
                status="ready",
                transcript="I like noodles",
                feedback={"natural": "I like noodles.", "follow_up": "Spicy?", "follow_up_zh": "辣吗？"},
            )
            session, _ = store.append_audio(session["id"], b"RIFFsecond", "audio/wav", 1000)
            session = store.update_turn(
                session["id"],
                2,
                status="ready",
                transcript="Yes a bit",
                feedback={"natural": "Yes, a bit.", "follow_up": "At home?", "follow_up_zh": "在家吗？"},
            )
            store.save_speech(session["id"], 1, b"RIFFspeech-1", "I like noodles.")
            store.save_speech(session["id"], 2, b"RIFFspeech-2", "Yes, a bit.")
            prompt = session["turns"][0]["prompt"]

            replaced = store.replace_audio(session["id"], 1, b"webm-new-answer", "audio/webm", 1500)
            first = replaced["turns"][0]
            self.assertEqual(first["prompt"], prompt)
            self.assertEqual(first["audio_file"], "turn-01.webm")
            self.assertIsNone(first["feedback"])
            self.assertNotIn("speech_file", first)
            self.assertEqual(first["status"], "saved")
            self.assertEqual(replaced["turns"][1]["transcript"], "Yes a bit")
            self.assertEqual(replaced["pending_prompt"]["en"], "At home?")
            folder = Path(tmp) / session["id"]
            self.assertEqual((folder / "turn-01.webm").read_bytes(), b"webm-new-answer")
            self.assertFalse((folder / "turn-01.wav").exists())
            self.assertFalse((folder / "turn-01-speech.wav").exists())
            self.assertTrue((folder / "turn-02-speech.wav").exists())

            last = store.replace_audio(session["id"], 2, b"RIFFlast", "audio/wav", 1200)
            self.assertEqual(last["pending_prompt"]["en"], last["turns"][1]["prompt"]["en"])
            self.assertFalse((folder / "turn-02-speech.wav").exists())
            stale = store.save_speech(session["id"], 1, b"RIFFold", "I like noodles.")
            self.assertNotIn("speech_file", stale["turns"][0])
            self.assertFalse((folder / "turn-01-speech.wav").exists())


class RetellTests(unittest.TestCase):
    def test_retell_is_kept_and_listed_under_the_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            session = store.create({"id": "city-food", "en": "What food?", "zh": "吃什么？"})
            session, index = store.append_audio(session["id"], b"RIFFanswer", "audio/wav", 1000)
            store.update_turn(
                session["id"],
                index,
                status="ready",
                feedback={"natural": "I like noodles.", "natural_zh": "我喜欢面条。"},
            )
            saved = store.save_retell(session["id"], index, b"webm-retell", "audio/webm")
            self.assertEqual(saved["turns"][0]["retell_file"], "turn-01-retell.webm")
            path, mime = store.retell_path(session["id"], index)
            self.assertEqual(mime, "audio/webm")
            self.assertEqual(path.read_bytes(), b"webm-retell")
            session, second = store.append_audio(session["id"], b"RIFFnext", "audio/wav", 900)
            store.update_turn(
                session["id"],
                second,
                status="ready",
                feedback={"natural": "A little spicy.", "natural_zh": "有一点辣。"},
            )
            lines = store.echoes_for_question("city-food")
            self.assertEqual([item["index"] for item in lines], [1, 2])
            self.assertEqual(lines[0]["natural"], "I like noodles.")
            self.assertTrue(lines[0]["has_retell"])
            replaced = store.update_turn(
                session["id"],
                index,
                feedback={"natural": "I like rice.", "natural_zh": "我喜欢米饭。"},
            )
            self.assertNotIn("retell_file", replaced["turns"][0])
            self.assertFalse(path.exists())


class QuestionTests(unittest.TestCase):
    def test_questions_have_unique_ids(self):
        questions = load_questions()
        ids = [item["id"] for item in questions]
        self.assertGreaterEqual(len(ids), 8)
        self.assertEqual(len(ids), len(set(ids)))
        nxt = next_question(exclude=ids[0])
        self.assertNotEqual(nxt["id"], ids[0])

    def test_question_crud_uses_its_own_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "questions.json"
            path.write_text('[{"id":"only-one","en":"Only one?","zh":"只剩一道？"}]\n', encoding="utf-8")
            with self.assertRaises(QuestionError):
                delete_question("only-one", path)
            created = create_question("What season do you like?", "你喜欢哪个季节？", path)
            self.assertEqual(created["id"], "what-season-do-you-like")
            self.assertEqual(load_questions(path)[0]["id"], created["id"])
            self.assertTrue(created["created_at"])
            self.assertEqual(created["created_at"], created["updated_at"])
            updated = update_question(created["id"], created["en"], "你最喜欢什么季节？", path)
            self.assertEqual(updated["zh"], "你最喜欢什么季节？")
            self.assertEqual(updated["created_at"], created["created_at"])
            self.assertNotEqual(updated["updated_at"], "")
            delete_question(created["id"], path)
            ids = [item["id"] for item in load_questions(path)]
            self.assertEqual(ids, ["only-one"])
            kept = load_questions(path)[0]
            self.assertTrue(kept["created_at"])
            self.assertTrue(kept["updated_at"])

    def test_backfill_adds_missing_times_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "questions.json"
            path.write_text('[{"id":"only-one","en":"Only one?","zh":"只剩一道？"}]\n', encoding="utf-8")
            first = backfill_timestamps(path)
            second = backfill_timestamps(path)
            self.assertEqual(first[0]["created_at"], second[0]["created_at"])
            self.assertEqual(first[0]["created_at"], first[0]["updated_at"])
            self.assertIn("T", first[0]["created_at"])

    def test_practice_remembers_the_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PracticeStore(Path(tmp) / "state.json")
            self.assertEqual(store.read(), {"question_id": "", "session_id": ""})
            store.write(question_id="city-food")
            self.assertEqual(store.read()["question_id"], "city-food")
            store.write(question_id="city-food", session_id="abc")
            store.forget_question("other")
            self.assertEqual(store.read()["session_id"], "abc")
            store.forget_question("city-food")
            self.assertEqual(store.read(), {"question_id": "", "session_id": ""})


class ProviderTests(unittest.TestCase):
    def test_switch_is_remembered_and_qwen_text_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProviderStore(Path(tmp) / "provider.json", default="gemini")
            self.assertEqual(store.read(), "gemini")
            store.write("qwen")
            self.assertEqual(store.read(), "qwen")
        self.assertEqual(
            chat_url("https://dashscope.aliyuncs.com/api/v1"),
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        )
        text = transcript_from_qwen(
            {"output": {"choices": [{"message": {"content": [{"text": "I like noodles"}]}}]}}
        )
        self.assertEqual(text, "I like noodles")
        self.assertEqual(audio_url_from_qwen({"output": {"audio": {"url": "https://example.com/a.wav"}}}), "https://example.com/a.wav")
        oss = "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.wav"
        self.assertEqual(usable_audio_url(oss), oss)
        self.assertEqual(usable_audio_url("https://example.com/a.wav"), "")
        self.assertTrue(contains_cjk("好像是这样"))
        self.assertFalse(contains_cjk("Something like that."))
        prompt = build_feedback_prompt(
            topic_en="School",
            topic_zh="上学",
            prompt_en="Did you like it?",
            prompt_zh="你喜欢吗？",
            transcript="好像是这样。",
        )
        self.assertIn("Do not leave any Chinese characters in natural", prompt)


if __name__ == "__main__":
    unittest.main()
