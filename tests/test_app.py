from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pyloader import create_app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "downloads"
        thumbs = root / "thumbnails"
        self.app = create_app({
            "TESTING": True,
            "DOWNLOAD_ROOT": str(root),
            "THUMBNAILS_ROOT": str(thumbs),
            "MAX_WORKERS": 1,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_home_and_system_endpoints(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("PyLoader", response.get_data(as_text=True))
        self.assertIn("no-store", response.headers["Cache-Control"])
        system = self.client.get("/api/system").get_json()
        self.assertTrue(system["ok"])
        self.assertIn("mp3", system["formats"])

    def test_library_pagination_reports_total(self):
        library = self.app.extensions["pyloader_library"]
        library.list = lambda *_args: [{"filename": str(index)} for index in range(3)]

        first = self.client.get("/api/library?limit=2&offset=0").get_json()
        second = self.client.get("/api/library?limit=2&offset=2").get_json()

        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(second["items"], [{"filename": "2"}])

    def test_inspect_requires_urls(self):
        response = self.client.post("/api/inspect", json={"urls": []})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])

    @patch("pyloader.app.inspect_urls")
    def test_inspect_returns_batch_summary(self, mocked):
        mocked.return_value = {
            "sources": [], "items": [], "raw_count": 2, "unique_count": 1,
            "duplicate_count": 1, "total_duration": 10, "errors": [],
        }
        response = self.client.post("/api/inspect", json={"urls": ["https://youtu.be/abcdefghijk"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["duplicate_count"], 1)

    def test_delete_rejects_path_escape(self):
        response = self.client.delete("/api/library", json={"filename": "../outside.mp3"})
        self.assertEqual(response.status_code, 400)

    def test_empty_queue_is_valid(self):
        response = self.client.get("/api/jobs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["jobs"], [])


if __name__ == "__main__":
    unittest.main()
