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


    def add_track(self, name="track", video_id="abcdefghijk", playlist=None):
        library = self.app.extensions["pyloader_library"]
        path = Path(library.download_root) / f"{name}.mp3"
        path.write_bytes(b"0123456789" * 10)
        return library.register(
            path,
            {"id": video_id, "title": name.title(), "duration": 10},
            {"format": "mp3", "playlist_title": playlist},
            None,
        )

    def test_rejects_foreign_host_header(self):
        response = self.client.get("/api/system", headers={"Host": "evil.example:5000"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "127.0.0.1:5000"}).status_code, 200)

    def test_stream_supports_range_requests(self):
        record = self.add_track()
        response = self.client.get(f"/stream/{record['filename']}", headers={"Range": "bytes=0-9"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.get_data(), b"0123456789")
        self.assertNotIn("attachment", response.headers.get("Content-Disposition", ""))
        response.close()

    def test_favorites_and_bulk_delete_clean_playlists_and_outbox(self):
        one = self.add_track("one", "abcdefghij1")
        two = self.add_track("two", "abcdefghij2")
        favorite = self.client.post("/api/library/favorite", json={"ids": [one["id"]], "favorite": True}).get_json()
        self.assertEqual(favorite["changed"], 1)
        rows = self.client.get("/api/library?filter=favorites").get_json()["items"]
        self.assertEqual([row["id"] for row in rows], [one["id"]])

        playlist = self.client.post("/api/playlists", json={"name": "Mix", "ids": [one["id"], two["id"], "nope"]}).get_json()["playlist"]
        self.assertEqual(playlist["ids"], [one["id"], two["id"]])
        self.client.post("/api/outbox", json={"ids": [one["id"], two["id"]]})

        deleted = self.client.delete("/api/library", json={"ids": [one["id"]]}).get_json()
        self.assertEqual(deleted["deleted"], 1)
        self.assertEqual(deleted["deleted_ids"], [one["id"]])
        playlist = self.client.get(f"/api/playlists/{playlist['id']}").get_json()["playlist"]
        self.assertEqual(playlist["ids"], [two["id"]])
        outbox = self.client.get("/api/bridge").get_json()["outbox"]
        self.assertEqual([row["id"] for row in outbox], [two["id"]])

    def test_playlist_crud_and_reorder(self):
        one = self.add_track("one", "abcdefghij1")
        two = self.add_track("two", "abcdefghij2")
        created = self.client.post("/api/playlists", json={"name": "  Road   trip "}).get_json()["playlist"]
        self.assertEqual(created["name"], "Road trip")
        pid = created["id"]
        added = self.client.post(f"/api/playlists/{pid}/items", json={"ids": [one["id"], two["id"], one["id"]]}).get_json()
        self.assertEqual(added["added"], 2)
        reordered = self.client.patch(f"/api/playlists/{pid}", json={"ids": [two["id"], one["id"]], "name": "Trip"}).get_json()
        self.assertEqual(reordered["playlist"]["ids"], [two["id"], one["id"]])
        self.assertEqual(reordered["playlist"]["name"], "Trip")
        bad = self.client.patch(f"/api/playlists/{pid}", json={"ids": [one["id"]]})
        self.assertEqual(bad.status_code, 400)
        removed = self.client.delete(f"/api/playlists/{pid}/items", json={"ids": [two["id"]]}).get_json()
        self.assertEqual(removed["removed"], 1)
        self.assertEqual(self.client.delete(f"/api/playlists/{pid}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/playlists/{pid}").status_code, 404)
        self.assertEqual(self.client.post("/api/playlists", json={"name": " "}).status_code, 400)

    def test_jobs_endpoint_returns_deltas(self):
        manager = self.app.extensions["pyloader_manager"]
        manager.executor.submit = lambda *args, **kwargs: None
        manager.enqueue([{"id": "abcdefghijk", "url": "https://youtu.be/abcdefghijk"}], {"format": "mp3"})
        first = self.client.get("/api/jobs").get_json()
        self.assertTrue(first["full"])
        self.assertEqual(first["active"], 1)
        delta = self.client.get(f"/api/jobs?since={first['version']}&epoch={first['epoch']}").get_json()
        self.assertFalse(delta["full"])
        self.assertEqual(delta["jobs"], [])
        cancelled = self.client.post("/api/jobs/cancel-all").get_json()
        self.assertEqual(cancelled["cancelled"], 1)
        cleared = self.client.delete("/api/jobs/completed").get_json()
        self.assertEqual(cleared["cleared"], 1)

    @patch("pyloader.app.inspect_urls")
    def test_inspect_marks_items_already_in_library(self, mocked):
        self.add_track("one", "abcdefghij1")
        mocked.return_value = {
            "sources": [], "raw_count": 2, "unique_count": 2, "duplicate_count": 0, "total_duration": 0, "errors": [],
            "items": [{"key": "a", "id": "abcdefghij1"}, {"key": "b", "id": "abcdefghij2"}],
        }
        items = self.client.post("/api/inspect", json={"urls": ["https://youtu.be/abcdefghij1"]}).get_json()["items"]
        self.assertEqual(items[0]["library_formats"], ["mp3"])
        self.assertEqual(items[1]["library_formats"], [])

    def test_bridge_qr_and_token_rotation(self):
        before = self.client.get("/api/bridge").get_json()
        self.assertFalse(before["enabled"])
        self.assertIn("/connect?token=", before["connect_url"])
        qr = self.client.get("/api/bridge/qr.svg")
        self.assertEqual(qr.mimetype, "image/svg+xml")
        self.assertIn(b'xmlns="http://www.w3.org/2000/svg"', qr.get_data())
        after = self.client.post("/api/bridge/token").get_json()
        self.assertNotEqual(before["connect_url"], after["connect_url"])

    def test_bulk_delete_keeps_shared_thumbnails_and_removes_orphans(self):
        library = self.app.extensions["pyloader_library"]
        thumbs = Path(library.thumbnails_root)
        (thumbs / "shared.jpg").write_bytes(b"x")
        (thumbs / "own.jpg").write_bytes(b"x")
        records = []
        for name, thumb in (("a", "shared.jpg"), ("b", "shared.jpg"), ("c", "own.jpg")):
            path = Path(library.download_root) / f"{name}.mp3"
            path.write_bytes(b"audio")
            records.append(library.register(path, {"id": f"abcdefghij{name}", "title": name}, {"format": "mp3"}, thumb))
        result = self.client.delete("/api/library", json={"ids": [records[0]["id"], records[2]["id"]]}).get_json()
        self.assertEqual(result["deleted"], 2)
        self.assertTrue((thumbs / "shared.jpg").exists())
        self.assertFalse((thumbs / "own.jpg").exists())
        self.assertEqual([row["id"] for row in library.list()], [records[1]["id"]])

    def test_shutdown_requires_confirmation(self):
        self.assertEqual(self.client.post("/api/shutdown", json={}).status_code, 400)
        self.assertTrue(self.client.post("/api/shutdown", json={"confirm": True}).get_json()["testing"])

    def test_manifest_and_icons_are_served(self):
        manifest = self.client.get("/static/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        manifest.close()
        icon = self.client.get("/static/icons/pyloader.ico")
        self.assertEqual(icon.status_code, 200)
        icon.close()

    def test_system_reports_disk_and_version(self):
        system = self.client.get("/api/system").get_json()
        self.assertIn("version", system)
        self.assertGreater(system["disk_total"], 0)


if __name__ == "__main__":
    unittest.main()
