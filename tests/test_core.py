from __future__ import annotations

import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest.mock import patch

from pyloader.core import (
    DownloadConfig,
    DownloadManager,
    JobStore,
    MediaLibrary,
    build_format_selector,
    ensure_http_url,
    media_tags,
    safe_component,
)


class CoreHelpersTests(unittest.TestCase):
    def test_safe_component_handles_windows_characters(self):
        value = safe_component('A/B:C*D?E"F<G>H|I')
        self.assertNotRegex(value, r'[<>:"/\\|?*]')
        self.assertIn("A", value)
        self.assertIn("I", value)

    def test_safe_component_handles_reserved_name(self):
        self.assertEqual(safe_component("CON"), "_CON")

    def test_url_validation(self):
        self.assertEqual(ensure_http_url(" https://youtu.be/abcdefghijk "), "https://youtu.be/abcdefghijk")
        with self.assertRaises(ValueError):
            ensure_http_url("javascript:alert(1)")

    def test_format_selectors_include_requested_limits(self):
        self.assertIn("height<=2160", build_format_selector("mp4", "2160"))
        self.assertIn("bestaudio", build_format_selector("mp3", "max"))
        self.assertNotIn("height<=", build_format_selector("mkv", "max"))

    def test_media_tags_prefers_canonical_purl_over_description_links(self):
        class Tag:
            def __init__(self, text):
                self.text = [text]

            def __str__(self):
                return self.text[0]

        fake_media = type("Media", (), {"tags": OrderedDict([
            ("TXXX:description", Tag("Reference https://youtu.be/WrongId1234")),
            ("TXXX:purl", Tag("https://www.youtube.com/watch?v=RightId1234")),
            ("TIT2", Tag("Title")),
        ])})()
        with patch("pyloader.core.MutagenFile", return_value=fake_media):
            video_id, title = media_tags(Path("ignored.mp3"))
        self.assertEqual(video_id, "RightId1234")
        self.assertEqual(title, "Title")


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "downloads"
        self.thumbs = self.root / "thumbnails"
        self.root.mkdir()
        self.thumbs.mkdir()
        self.library = MediaLibrary(self.root, self.thumbs)

    def tearDown(self):
        self.temp.cleanup()

    def test_register_and_duplicate_lookup(self):
        path = self.root / "track.mp3"
        path.write_bytes(b"fake audio for an index-only test")
        self.library.register(
            path,
            {"id": "abcdefghijk", "title": "Track", "webpage_url": "https://youtu.be/abcdefghijk"},
            {"format": "mp3", "quality": "max"},
            None,
        )
        self.assertTrue(self.library.has("abcdefghijk", "mp3"))
        self.assertFalse(self.library.has("abcdefghijk", "mp4"))
        self.assertEqual(self.library.stats()["audio_count"], 1)

    def test_job_store_cancellation_and_cleanup(self):
        store = JobStore()
        store.add({"id": "one", "state": "queued", "created_at": "1"})
        self.assertTrue(store.cancel("one"))
        self.assertTrue(store.is_cancelled("one"))
        self.assertEqual(store.clear_terminal(), 1)

    def test_manager_skips_existing_id_and_format(self):
        path = self.root / "track.mp3"
        path.write_bytes(b"fake audio")
        self.library.register(
            path,
            {"id": "abcdefghijk", "title": "Track"},
            {"format": "mp3", "quality": "max"},
            None,
        )
        manager = DownloadManager(DownloadConfig(self.root, self.thumbs, None, workers=1), self.library)
        result = manager.enqueue(
            [{"id": "abcdefghijk", "url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "Track"}],
            {"format": "mp3", "skip_duplicates": True},
        )
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["skipped_library"], 1)


if __name__ == "__main__":
    unittest.main()
