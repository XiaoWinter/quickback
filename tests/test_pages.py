import unittest

from fastapi.testclient import TestClient

from app.main import app


class PageRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_known_pages_serve_the_app(self):
        for path in (
            "/",
            "/practice",
            "/history",
            "/questions",
            "/progress",
            "/progress/stats",
            "/progress/run",
            "/progresses",
            "/tasks",
            "/memos",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("练习", response.text)
            self.assertIn('href="/questions"', response.text)
            self.assertIn('href="/progresses"', response.text)
            self.assertIn('href="/tasks"', response.text)
            self.assertIn('href="/memos"', response.text)
        questions = self.client.get("/questions")
        self.assertEqual(questions.status_code, 200)
        self.assertIn('id="question-open-history"', questions.text)
        self.assertIn("记录", questions.text)
        self.assertNotIn('id="question-open-tasks"', questions.text)
        self.assertNotIn('id="tab-history"', questions.text)
        self.assertNotIn('id="tab-progress"', questions.text)
        self.assertIn('id="tab-progresses"', questions.text)
        self.assertIn(">进展统计<", questions.text)
        self.assertIn('id="progresses-root"', questions.text)
        self.assertIn('id="tab-tasks"', questions.text)
        progress = self.client.get("/progress")
        self.assertEqual(progress.status_code, 200)
        self.assertIn('id="progress-back"', progress.text)
        self.assertIn("← 题目", progress.text)
        from_tasks = self.client.get("/progress", params={"question": "q1", "from": "tasks"})
        self.assertEqual(from_tasks.status_code, 200)
        self.assertIn('id="progress-back"', from_tasks.text)

    def test_questions_page_has_ielts_generate_controls(self):
        response = self.client.get("/questions")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="toast"', response.text)
        self.assertIn('id="question-open-ielts"', response.text)
        self.assertIn('id="question-ielts-panel"', response.text)
        self.assertIn('hidden', response.text)
        self.assertNotIn('id="question-close-ielts"', response.text)
        self.assertIn('id="question-open-ielts"', response.text)
        self.assertIn('id="question-ielts-list"', response.text)
        self.assertIn('id="question-ielts-gen"', response.text)
        self.assertIn('id="question-ielts-footer"', response.text)
        self.assertIn('id="question-ielts-reshuffle"', response.text)
        self.assertIn('id="question-ielts-commit"', response.text)
        self.assertIn("生成口语123", response.text)
        self.assertIn("换一批", response.text)
        self.assertIn("加入已有题目", response.text)
        self.assertIn("已有题目", response.text)
        self.assertIn('id="question-closed-filter"', response.text)
        self.assertIn('data-closed-filter="open"', response.text)
        self.assertIn('data-closed-filter="closed"', response.text)
        self.assertRegex(response.text, r'id="question-ielts-panel"[^>]*\bhidden\b')
        self.assertIn('name="category"', response.text)
        self.assertIn('id="question-category-picks"', response.text)

    def test_progress_keeps_the_question_query(self):
        response = self.client.get("/progress", params={"question": "q1"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="progress-back"', response.text)
        self.assertIn('id="progress-root"', response.text)

    def test_progress_run_page_exists(self):
        response = self.client.get("/progress/run", params={"question": "q1", "run": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="progress-run-back"', response.text)
        self.assertIn('id="progress-run-root"', response.text)

    def test_unknown_page_is_not_the_app(self):
        response = self.client.get("/missing")
        self.assertEqual(response.status_code, 404)
