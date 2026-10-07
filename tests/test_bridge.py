from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pyloader import create_app
from pyloader.bridge import TOKEN_COOKIE, create_bridge_app


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "downloads"
        self.app = create_app({
            "TESTING": True,
            "DOWNLOAD_ROOT": str(root),
            "THUMBNAILS_ROOT": str(root / "thumbnails"),
            "MAX_WORKERS": 1,
        })
        self.bridge = self.app.extensions["pyloader_bridge"]
        library = self.app.extensions["pyloader_library"]
        track = root / "song.mp3"
        track.write_bytes(b"abcdefghij" * 20)
        (root / "thumbnails" / "abcdefghijk.jpg").write_bytes(b"jpeg")
        self.record = library.register(
            track, {"id": "abcdefghijk", "title": "Song", "uploader": "Artist", "duration": 42},
            {"format": "mp3"}, "abcdefghijk.jpg",
        )
        self.phone = create_bridge_app(self.bridge).test_client()
        self.token = self.bridge._token

    def tearDown(self):
        self.bridge.stop()
        self.temp.cleanup()

    def auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_api_requires_token(self):
        self.assertEqual(self.phone.get("/api/v1/library").status_code, 401)
        self.assertEqual(self.phone.get("/api/v1/library?token=wrong").status_code, 401)
        self.assertEqual(self.phone.get("/api/v1/media/x/stream").status_code, 401)
        ping = self.phone.get("/api/v1/ping", headers=self.auth()).get_json()
        self.assertEqual(ping["api"], 1)

    def test_unpaired_index_reveals_nothing(self):
        page = self.phone.get("/").get_data(as_text=True)
        self.assertNotIn("Song", page)
        self.assertIn("QR", page)

    def test_connect_sets_cookie_and_cookie_authorizes(self):
        response = self.phone.get(f"/connect?token={self.token}")
        self.assertEqual(response.status_code, 302)
        self.assertIn(TOKEN_COOKIE, response.headers["Set-Cookie"])
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        library = self.phone.get("/api/v1/library").get_json()
        self.assertEqual(library["items"][0]["title"], "Song")
        self.assertEqual(library["items"][0]["artist"], "Artist")
        self.assertEqual(self.phone.get("/connect?token=nope").status_code, 401)

    def test_rotating_token_revokes_access(self):
        self.phone.get(f"/connect?token={self.token}")
        self.bridge.rotate_token()
        self.assertEqual(self.phone.get("/api/v1/library").status_code, 401)

    def test_stream_cover_and_download_mark_outbox_delivered(self):
        media_id = self.record["id"]
        self.assertEqual(self.bridge.outbox_add([media_id, "unknown"]), 1)
        outbox = self.phone.get("/api/v1/outbox", headers=self.auth()).get_json()["items"]
        self.assertIsNone(outbox[0]["delivered_at"])

        stream = self.phone.get(f"/api/v1/media/{media_id}/stream", headers={**self.auth(), "Range": "bytes=0-4"})
        self.assertEqual(stream.status_code, 206)
        self.assertEqual(stream.get_data(), b"abcde")
        stream.close()
        cover = self.phone.get(f"/api/v1/media/{media_id}/cover", headers=self.auth())
        self.assertEqual(cover.get_data(), b"jpeg")
        cover.close()

        download = self.phone.get(f"/api/v1/media/{media_id}/download", headers=self.auth())
        self.assertIn("attachment", download.headers["Content-Disposition"])
        download.close()
        outbox = self.phone.get("/api/v1/outbox", headers=self.auth()).get_json()["items"]
        self.assertIsNotNone(outbox[0]["delivered_at"])
        self.assertEqual(self.phone.get("/api/v1/media/missing", headers=self.auth()).status_code, 404)

    def test_playlists_are_exposed(self):
        playlists = self.app.extensions["pyloader_playlists"]
        playlist = playlists.create("Favourites", [self.record["id"]])
        listing = self.phone.get("/api/v1/playlists", headers=self.auth()).get_json()
        self.assertEqual(listing["playlists"][0]["count"], 1)
        detail = self.phone.get(f"/api/v1/playlists/{playlist['id']}", headers=self.auth()).get_json()
        self.assertEqual(detail["items"][0]["id"], self.record["id"])

    def test_server_starts_and_stops(self):
        self.bridge.port = 0  # let the OS pick a free port
        self.bridge.host = "127.0.0.1"
        self.bridge.start()
        self.assertTrue(self.bridge.running)
        self.bridge.stop()
        self.assertFalse(self.bridge.running)


if __name__ == "__main__":
    unittest.main()
