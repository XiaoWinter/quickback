import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.tasks import TaskStore, grade_from_average, next_schedule


class TaskScheduleTests(unittest.TestCase):
    def test_grade_from_average(self):
        self.assertEqual(grade_from_average(90), "easy")
        self.assertEqual(grade_from_average(75), "good")
        self.assertEqual(grade_from_average(55), "hard")
        self.assertEqual(grade_from_average(20), "again")

    def test_today_plan_stays_fixed_and_marks_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.json")
            now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
            closed = [
                {"id": "q1", "en": "One?", "zh": "一？", "category": ""},
                {"id": "q2", "en": "Two?", "zh": "二？", "category": ""},
                {"id": "q3", "en": "Three?", "zh": "三？", "category": ""},
                {"id": "q4", "en": "Four?", "zh": "四？", "category": ""},
            ]
            first = store.today(closed, now=now)
            self.assertEqual(len(first["items"]), 3)
            first_ids = [item["id"] for item in first["items"]]
            self.assertNotIn("q4", first_ids)
            done_id = first_ids[0]
            store.complete(done_id, average=80)
            second = store.today(closed, now=now + timedelta(hours=1))
            second_ids = [item["id"] for item in second["items"]]
            self.assertEqual(second_ids, first_ids)
            marked = next(item for item in second["items"] if item["id"] == done_id)
            self.assertTrue(marked["done"])
            self.assertEqual(second["done_count"], 1)
            self.assertEqual(second["pending_count"], 2)
            self.assertNotIn("q4", second_ids)

    def test_good_review_pushes_due(self):
        card = {"ease": 2.5, "interval_days": 0, "reps": 0, "lapses": 0}
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        updated = next_schedule(card, "good", now=now)
        self.assertEqual(updated["interval_days"], 1.0)
        self.assertGreater(updated["due_at"], now.isoformat())
