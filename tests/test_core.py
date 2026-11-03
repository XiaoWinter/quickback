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
from app.questions import create_question, delete_question, load_questions, next_question, update_question, QuestionError
from app.segments import split_transcript
from app.storage import SessionStore


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
        self.assertEqual(len(data["extensions"]), 1)
        self.assertNotIn("once a week", data["natural"])

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
                "changes": [{"spoken": "I like hot pot", "better": "I am quite fond of hot pot", "note": "再润色"}],
                "review": "还可以把 fond 换成更高级的说法。",
            },
            suggested,
            suggested,
        )
        self.assertEqual(feedback["natural"], suggested)
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
            path, mime = store.speech_path(session["id"], 1)
            self.assertEqual(mime, "audio/wav")
            self.assertEqual(path.read_bytes(), b"RIFFspeech")
            self.assertEqual(spoken["turns"][0]["speech_file"], "turn-01-speech.wav")


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
            updated = update_question(created["id"], created["en"], "你最喜欢什么季节？", path)
            self.assertEqual(updated["zh"], "你最喜欢什么季节？")
            delete_question(created["id"], path)
            ids = [item["id"] for item in load_questions(path)]
            self.assertEqual(ids, ["only-one"])


if __name__ == "__main__":
    unittest.main()
