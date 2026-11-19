import unittest

from fastapi.testclient import TestClient

from app.main import app


class PageRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_known_pages_serve_the_app(self):
        for path in ("/", "/practice", "/history", "/questions", "/progress", "/progress/stats", "/memos"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("练习", response.text)
            self.assertIn('href="/history"', response.text)
            self.assertIn('href="/memos"', response.text)

    def test_progress_keeps_the_question_query(self):
        response = self.client.get("/progress", params={"question": "q1"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("进展", response.text)

    def test_unknown_page_is_not_the_app(self):
        response = self.client.get("/missing")
        self.assertEqual(response.status_code, 404)
