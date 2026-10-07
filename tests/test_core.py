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
    glob_escape,
    media_details,
    media_tags,
    safe_component,
    stream_phase,
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


    def test_media_details_reads_artist_and_duration(self):
        class Tag:
            def __init__(self, text):
                self.text = [text]

        fake_media = type("Media", (), {
            "info": type("Info", (), {"length": 187.26})(),
            "tags": OrderedDict([("TIT2", Tag("Song")), ("TPE1", Tag("Band")), ("TPE2", Tag("Other"))]),
        })()
        with patch("pyloader.core.MutagenFile", return_value=fake_media):
            details = media_details(Path("ignored.mp3"))
        self.assertEqual(details["title"], "Song")
        self.assertEqual(details["artist"], "Band")
        self.assertEqual(details["duration"], 187.3)

    def test_glob_escape_keeps_brackets_literal(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        (folder / "Song [abcdefghijk].mp3").write_bytes(b"x")
        self.assertEqual(list(folder.glob("Song [abcdefghijk].*")), [])
        self.assertEqual(len(list(folder.glob(f"{glob_escape('Song [abcdefghijk]')}.*"))), 1)

    def test_stream_phase_splits_merged_video_downloads(self):
        self.assertEqual(stream_phase({"vcodec": "avc1", "acodec": "none"}, "mp4"), ("видео", 0.0, 0.9))
        self.assertEqual(stream_phase({"vcodec": "none", "acodec": "mp4a"}, "mp4"), ("аудио", 0.9, 0.1))
        self.assertEqual(stream_phase({"vcodec": "none", "acodec": "opus"}, "mp3"), ("", 0.0, 1.0))


class LibraryFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "downloads"
        self.thumbs = self.root / "thumbnails"
        self.thumbs.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def details(self, video_id=None, title=None, artist=None, duration=None):
        return {"video_id": video_id, "title": title, "artist": artist, "duration": duration}

    def test_ids_are_stable_and_favorites_persist(self):
        (self.root / "a.mp3").write_bytes(b"a")
        library = MediaLibrary(self.root, self.thumbs)
        record = library.list()[0]
        self.assertTrue(record["id"])
        self.assertEqual(library.set_favorite([record["id"]], True), 1)
        reloaded = MediaLibrary(self.root, self.thumbs)
        again = reloaded.by_id(record["id"])
        self.assertIsNotNone(again)
        self.assertTrue(again["favorite"])
        self.assertEqual(reloaded.list("favorites")[0]["id"], record["id"])
        self.assertEqual(reloaded.stats()["favorite_count"], 1)

    def test_renamed_file_keeps_id_by_youtube_id(self):
        with patch("pyloader.core.media_details", return_value=self.details("abcdefghijk", "Song")):
            (self.root / "old.mp3").write_bytes(b"a")
            library = MediaLibrary(self.root, self.thumbs)
            original = library.list()[0]
            library.set_favorite([original["id"]], True)
            (self.root / "old.mp3").rename(self.root / "new.mp3")
            library.sync()
        moved = library.list()[0]
        self.assertEqual(moved["filename"], "new.mp3")
        self.assertEqual(moved["id"], original["id"])
        self.assertTrue(moved["favorite"])

    def test_unchanged_files_are_not_rescanned(self):
        (self.root / "a.mp3").write_bytes(b"a")
        with patch("pyloader.core.media_details", return_value=self.details()) as mocked:
            library = MediaLibrary(self.root, self.thumbs)
            library.sync()
            library.sync()
        self.assertEqual(mocked.call_count, 1)

    def test_collections_resolve_and_search_by_artist(self):
        library = MediaLibrary(self.root, self.thumbs)
        for index, name in enumerate(("one", "two")):
            path = self.root / f"{name}.mp3"
            path.write_bytes(b"audio")
            library.register(
                path,
                {"id": f"abcdefghij{index}", "title": name.title(), "uploader": "Some Artist", "duration": 60},
                {"format": "mp3", "playlist_title": "Mix", "playlist_index": index + 1},
                None,
            )
        collections = library.collections()
        self.assertEqual(collections[0]["name"], "Mix")
        self.assertEqual(collections[0]["count"], 2)
        self.assertEqual(collections[0]["duration"], 120)
        rows = library.list("all", "some artist", "playlist", "Mix")
        self.assertEqual([row["title"] for row in rows], ["One", "Two"])
        ids = [row["id"] for row in rows]
        self.assertEqual([row["id"] for row in library.resolve(reversed(ids + ["missing"]))], ids[::-1])
        self.assertEqual(library.formats_for("abcdefghij0"), ["mp3"])


class JobStoreDeltaTests(unittest.TestCase):
    def test_changes_returns_only_updated_jobs(self):
        store = JobStore()
        store.add({"id": "one", "state": "queued", "item": {"thumbnail": "t.jpg"}})
        store.add({"id": "two", "state": "queued", "item": {}})
        first = store.changes()
        self.assertTrue(first["full"])
        self.assertEqual({job["id"] for job in first["jobs"]}, {"one", "two"})
        self.assertNotIn("item", first["jobs"][0])
        self.assertEqual(first["summary"]["queued"], 2)

        store.update("two", state="downloading", progress=10)
        delta = store.changes(first["version"], first["epoch"])
        self.assertFalse(delta["full"])
        self.assertEqual([job["id"] for job in delta["jobs"]], ["two"])
        self.assertEqual(delta["summary"]["running"], 1)

        store.update("one", state="failed")
        self.assertTrue(store.remove("one"))
        after_remove = store.changes(delta["version"], delta["epoch"])
        self.assertTrue(after_remove["full"])
        self.assertEqual([job["id"] for job in after_remove["jobs"]], ["two"])

    def test_remove_refuses_active_jobs(self):
        store = JobStore()
        store.add({"id": "one", "state": "downloading"})
        self.assertFalse(store.remove("one"))


class DownloadManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "downloads"
        thumbs = root / "thumbnails"
        thumbs.mkdir(parents=True)
        self.manager = DownloadManager(DownloadConfig(root, thumbs, None, workers=1), MediaLibrary(root, thumbs))
        self.manager.executor.submit = lambda *args, **kwargs: None

    def tearDown(self):
        self.temp.cleanup()

    def enqueue(self, video_id="abcdefghijk", output_format="mp4"):
        url = f"https://www.youtube.com/watch?v={video_id}"
        return self.manager.enqueue([{"id": video_id, "url": url, "title": "Clip"}], {"format": output_format})

    def test_merged_progress_is_monotonic_across_streams(self):
        job_id = self.enqueue()["job_ids"][0]
        hook = self.manager._progress_hook
        hook(job_id, {"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100,
                      "info_dict": {"vcodec": "avc1", "acodec": "none"}})
        self.assertAlmostEqual(self.manager.jobs.get(job_id)["progress"], 45.0)
        hook(job_id, {"status": "finished", "info_dict": {"vcodec": "avc1", "acodec": "none"}})
        self.assertAlmostEqual(self.manager.jobs.get(job_id)["progress"], 90.0)
        self.assertEqual(self.manager.jobs.get(job_id)["state"], "downloading")
        hook(job_id, {"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100,
                      "info_dict": {"vcodec": "none", "acodec": "mp4a"}})
        job = self.manager.jobs.get(job_id)
        self.assertAlmostEqual(job["progress"], 95.0)
        self.assertEqual(job["status"], "Скачивание · аудио")

    def test_retry_failed_replaces_old_cards(self):
        first = self.enqueue("abcdefghij1")["job_ids"][0]
        second = self.enqueue("abcdefghij2")["job_ids"][0]
        for job_id in (first, second):
            self.manager.jobs.update(job_id, state="failed", error="boom")
            with self.manager._active_lock:
                self.manager._active_keys.clear()
        result = self.manager.retry_failed()
        self.assertEqual(result["added"], 2)
        ids = {job["id"] for job in self.manager.jobs.snapshot()}
        self.assertNotIn(first, ids)
        self.assertNotIn(second, ids)
        self.assertEqual(len(ids), 2)
        self.assertEqual(self.manager.active_count(), 2)

    def test_cancel_all_marks_queued_jobs(self):
        self.enqueue("abcdefghij1")
        self.enqueue("abcdefghij2")
        self.assertEqual(self.manager.jobs.cancel_all(), 2)
        self.assertEqual(self.manager.jobs.summary()["cancelled"], 2)


if __name__ == "__main__":
    unittest.main()
