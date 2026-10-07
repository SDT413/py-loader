from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import yt_dlp
from mutagen import File as MutagenFile


SUPPORTED_FORMATS = {"mp3", "m4a", "opus", "mp4", "mkv"}
AUDIO_FORMATS = {"mp3", "m4a", "opus"}
VIDEO_FORMATS = {"mp4", "mkv"}
TERMINAL_STATES = {"completed", "skipped", "failed", "cancelled"}
ACTIVE_STATES = {"extracting", "downloading", "processing"}
LIBRARY_SORTS = {"date", "name", "size", "duration", "artist", "playlist"}
THUMBNAIL_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
BROWSER_NAMES = {"none", "chrome", "edge", "firefox", "brave", "opera", "vivaldi"}
YOUTUBE_ID_RE = re.compile(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})")
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_bytes(size: int | float | None) -> str:
    value = float(size or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def ensure_http_url(value: str) -> str:
    url = (value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Нужна корректная ссылка http:// или https://")
    return url


def safe_component(value: str, fallback: str = "media", max_length: int = 180) -> str:
    replacements = {
        "<": "＜",
        ">": "＞",
        ":": "：",
        '"': "＂",
        "/": "⧸",
        "\\": "⧹",
        "|": "｜",
        "?": "？",
        "*": "＊",
    }
    cleaned = "".join(replacements.get(char, char) for char in (value or ""))
    cleaned = INVALID_FILENAME_RE.sub("_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in {
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
        "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
        "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }:
        cleaned = f"_{cleaned}"
    return cleaned[:max_length].rstrip(". ") or fallback


def glob_escape(value: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", value)


def extract_video_id_from_values(values: Iterable[Any]) -> str | None:
    for value in values:
        match = YOUTUBE_ID_RE.search(str(value))
        if match:
            return match.group(1)
    return None


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def new_media_id() -> str:
    return uuid.uuid4().hex[:12]


def _tag_text(value: Any) -> str | None:
    if hasattr(value, "text") and value.text:
        return str(value.text[0])
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else None
    return str(value) if value is not None else None


def media_details(path: Path) -> dict[str, Any]:
    """Read the embedded YouTube ID, title, artist and duration of a media file."""
    details: dict[str, Any] = {"video_id": None, "title": None, "artist": None, "duration": None}
    try:
        media = MutagenFile(path, easy=False)
        if not media:
            return details
        length = getattr(getattr(media, "info", None), "length", None)
        if isinstance(length, (int, float)) and length > 0:
            details["duration"] = round(float(length), 1)
        if not media.tags:
            return details
        preferred_values: list[Any] = []
        fallback_values: list[Any] = []
        for key, value in media.tags.items():
            key_text = str(key).lower()
            fallback_values.append(value)
            if any(marker in key_text for marker in ("purl", "comment", "website", "url")):
                preferred_values.append(value)
            if details["title"] is None and (key_text in {"tit2", "\xa9nam"} or "title" in key_text):
                details["title"] = _tag_text(value)
            if details["artist"] is None and key_text in {"tpe1", "\xa9art", "artist"}:
                details["artist"] = _tag_text(value)
        video_id = extract_video_id_from_values(preferred_values)
        details["video_id"] = video_id or extract_video_id_from_values(fallback_values)
    except Exception:
        pass
    return details


def media_tags(path: Path) -> tuple[str | None, str | None]:
    details = media_details(path)
    return details["video_id"], details["title"]


def ydl_base_options(cookies_browser: str = "none", proxy: str = "") -> dict[str, Any]:
    browser = cookies_browser if cookies_browser in BROWSER_NAMES else "none"
    options: dict[str, Any] = {
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "socket_timeout": 30,
    }
    if browser != "none":
        options["cookiesfrombrowser"] = (browser,)
    if proxy and proxy.strip():
        options["proxy"] = proxy.strip()
    return options


def _entry_to_item(entry: dict[str, Any], source: dict[str, Any], index: int) -> dict[str, Any] | None:
    video_id = entry.get("id")
    webpage_url = entry.get("webpage_url") or entry.get("url")
    if video_id and (not webpage_url or not str(webpage_url).startswith("http")):
        webpage_url = f"https://www.youtube.com/watch?v={video_id}"
    if not webpage_url:
        return None
    title = entry.get("title") or "Недоступное видео"
    availability = entry.get("availability") or "unknown"
    return {
        "key": f"{entry.get('extractor_key') or 'youtube'}:{video_id or webpage_url}",
        "id": video_id,
        "url": webpage_url,
        "title": title,
        "duration": entry.get("duration") or 0,
        "thumbnail": entry.get("thumbnail"),
        "availability": availability,
        "playlist_title": source.get("title"),
        "playlist_id": source.get("id"),
        "playlist_index": entry.get("playlist_index") or index,
        "source_type": source.get("type", "video"),
    }


def inspect_urls(
    urls: Iterable[str], cookies_browser: str = "none", proxy: str = ""
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    unique_items: dict[str, dict[str, Any]] = {}
    raw_count = 0
    errors: list[dict[str, str]] = []

    for raw_url in urls:
        try:
            url = ensure_http_url(raw_url)
            options = ydl_base_options(cookies_browser, proxy)
            options.update({
                "extract_flat": "in_playlist",
                "ignoreerrors": True,
                "noplaylist": False,
                "yes_playlist": True,
            })
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info:
                raise RuntimeError("YouTube не вернул данные")

            entries = info.get("entries")
            source_type = "playlist" if entries is not None else "video"
            source = {
                "url": url,
                "id": info.get("id"),
                "title": info.get("title") or "Без названия",
                "uploader": info.get("uploader") or info.get("channel") or "Неизвестно",
                "type": source_type,
                "items": [],
            }
            raw_entries = list(entries or [info])
            for index, entry in enumerate(raw_entries, start=1):
                if not entry:
                    continue
                item = _entry_to_item(entry, source, index)
                if not item:
                    continue
                raw_count += 1
                source["items"].append(item)
                unique_items.setdefault(item["key"], item)
            source["count"] = len(source["items"])
            source["duration"] = sum(item.get("duration") or 0 for item in source["items"])
            sources.append(source)
        except Exception as exc:
            errors.append({"url": str(raw_url), "error": clean_error(exc)})

    items = list(unique_items.values())
    return {
        "sources": sources,
        "items": items,
        "raw_count": raw_count,
        "unique_count": len(items),
        "duplicate_count": max(raw_count - len(items), 0),
        "total_duration": sum(item.get("duration") or 0 for item in items),
        "errors": errors,
    }


def build_format_selector(output_format: str, quality: str = "max") -> str:
    height = None
    if quality and quality != "max":
        try:
            height = max(144, min(int(quality), 4320))
        except (TypeError, ValueError):
            height = None
    limit = f"[height<={height}]" if height else ""
    if output_format == "mp4":
        return (
            f"bestvideo{limit}[ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo{limit}+bestaudio/best{limit}/best"
        )
    if output_format == "mkv":
        return f"bestvideo{limit}+bestaudio/best{limit}/best"
    if output_format == "m4a":
        return "bestaudio[ext=m4a]/bestaudio/best"
    if output_format == "opus":
        return "bestaudio[acodec^=opus]/bestaudio/best"
    return "bestaudio/best"


def clean_error(error: Exception | str) -> str:
    text = str(error).strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text[:900] or "Неизвестная ошибка"


class DownloadCancelled(Exception):
    pass


class JobLogger:
    def __init__(self) -> None:
        self.last_warning = ""

    def debug(self, _message: str) -> None:
        return

    def info(self, _message: str) -> None:
        return

    def warning(self, message: str) -> None:
        self.last_warning = clean_error(message)

    def error(self, message: str) -> None:
        self.last_warning = clean_error(message)


class MediaLibrary:
    def __init__(self, download_root: Path, thumbnails_root: Path) -> None:
        self.download_root = download_root.resolve()
        self.thumbnails_root = thumbnails_root.resolve()
        self.state_dir = self.download_root / ".pyloader"
        self.state_file = self.state_dir / "library.json"
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._by_id: dict[str, str] = {}
        self.revision = 0
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._load()
        self.sync()

    def _load(self) -> None:
        data = read_json(self.state_file, {})
        if not isinstance(data, dict):
            return
        self._records = {
            item["filename"]: item
            for item in data.get("items", [])
            if isinstance(item, dict) and item.get("filename")
        }

    def _reindex_ids(self) -> None:
        self._by_id = {}
        for filename, record in self._records.items():
            if not record.get("id") or record["id"] in self._by_id:
                record["id"] = new_media_id()
            self._by_id[record["id"]] = filename

    def _save(self) -> None:
        self._reindex_ids()
        self.revision += 1
        payload = {"version": 3, "updated_at": utc_now(), "items": list(self._records.values())}
        write_json_atomic(self.state_file, payload)

    def _media_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.download_root.rglob("*"):
            if not path.is_file() or self.state_dir in path.parents or self.thumbnails_root in path.parents:
                continue
            if path.suffix.lower().lstrip(".") in SUPPORTED_FORMATS and ".temp." not in path.name.lower():
                files.append(path)
        return files

    def _thumbnail_for(self, path: Path, video_id: str | None) -> str | None:
        candidates: list[Path] = []
        if video_id:
            candidates.extend(self.thumbnails_root.glob(f"{glob_escape(video_id)}.*"))
        candidates.extend(self.thumbnails_root.glob(f"{glob_escape(path.stem)}.*"))
        for candidate in candidates:
            if candidate.suffix.lower() in THUMBNAIL_SUFFIXES:
                return candidate.relative_to(self.thumbnails_root).as_posix()
        return None

    def sync(self) -> dict[str, int]:
        with self._lock:
            paths = {path.relative_to(self.download_root).as_posix(): path for path in self._media_files()}
            # Files that were moved or renamed outside PyLoader keep their id, favorite
            # flag and playlist membership when the YouTube ID and format still match.
            orphans = {
                (record.get("video_id"), record.get("format")): record
                for filename, record in self._records.items()
                if filename not in paths and record.get("video_id")
            }
            current: dict[str, dict[str, Any]] = {}
            for relative, path in paths.items():
                stat = path.stat()
                previous = self._records.get(relative)
                output_format = path.suffix.lower().lstrip(".")
                unchanged = (
                    previous is not None
                    and previous.get("scanned")
                    and previous.get("size") == stat.st_size
                    and previous.get("modified") == stat.st_mtime
                )
                if unchanged:
                    if not previous.get("thumbnail"):
                        previous["thumbnail"] = self._thumbnail_for(path, previous.get("video_id"))
                    current[relative] = previous
                    continue
                details = media_details(path)
                if previous is None:
                    previous = orphans.pop((details["video_id"], output_format), None) or {}
                video_id = details["video_id"] or previous.get("video_id")
                current[relative] = {
                    **previous,
                    "id": previous.get("id") or new_media_id(),
                    "filename": relative,
                    "title": details["title"] or previous.get("title") or path.stem,
                    "artist": details["artist"] or previous.get("artist"),
                    "video_id": video_id,
                    "format": output_format,
                    "media_type": "audio" if output_format in AUDIO_FORMATS else "video",
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "duration": details["duration"] or previous.get("duration"),
                    "thumbnail": previous.get("thumbnail") or self._thumbnail_for(path, video_id),
                    "added_at": previous.get("added_at") or utc_now(),
                    "scanned": True,
                }
            self._records = current
            self._save()
            return {"indexed": len(current)}

    def register(
        self, path: Path, info: dict[str, Any], options: dict[str, Any], thumbnail: str | None
    ) -> dict[str, Any]:
        resolved = path.resolve()
        if self.download_root not in resolved.parents:
            raise ValueError("Файл находится вне папки загрузок")
        relative = resolved.relative_to(self.download_root).as_posix()
        stat = resolved.stat()
        details = media_details(resolved)
        with self._lock:
            previous = self._records.get(relative) or {}
            self._records[relative] = {
                "id": previous.get("id") or new_media_id(),
                "filename": relative,
                "title": info.get("title") or details["title"] or path.stem,
                "artist": (
                    info.get("artist") or info.get("creator") or info.get("uploader")
                    or info.get("channel") or details["artist"]
                ),
                "video_id": info.get("id"),
                "source_url": info.get("webpage_url") or info.get("original_url"),
                "format": options["format"],
                "media_type": "audio" if options["format"] in AUDIO_FORMATS else "video",
                "quality": options.get("quality"),
                "playlist_title": options.get("playlist_title"),
                "playlist_index": options.get("playlist_index"),
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "duration": info.get("duration") or details["duration"],
                "thumbnail": thumbnail,
                "added_at": utc_now(),
                "favorite": bool(previous.get("favorite")),
                "scanned": True,
            }
            self._save()
            return copy.deepcopy(self._records[relative])

    def has(self, video_id: str | None, output_format: str) -> bool:
        return output_format in self.formats_for(video_id)

    def formats_for(self, video_id: str | None) -> list[str]:
        if not video_id:
            return []
        with self._lock:
            return sorted({
                str(record.get("format"))
                for record in self._records.values()
                if record.get("video_id") == video_id and (self.download_root / record["filename"]).is_file()
            })

    def by_filename(self, filename: str) -> dict[str, Any] | None:
        key = Path(filename).as_posix()
        with self._lock:
            record = self._records.get(key)
            return copy.deepcopy(record) if record else None

    def by_id(self, media_id: str) -> dict[str, Any] | None:
        with self._lock:
            filename = self._by_id.get(str(media_id))
            record = self._records.get(filename) if filename else None
            return copy.deepcopy(record) if record else None

    def path_of(self, record: dict[str, Any]) -> Path:
        candidate = (self.download_root / record["filename"]).resolve()
        if self.download_root not in candidate.parents:
            raise ValueError("Недопустимый путь")
        return candidate

    def remove(self, filename: str) -> dict[str, Any] | None:
        key = Path(filename).as_posix()
        with self._lock:
            record = self._records.pop(key, None)
            self._save()
            return record

    def set_favorite(self, media_ids: Iterable[str], favorite: bool) -> int:
        changed = 0
        with self._lock:
            for media_id in media_ids:
                filename = self._by_id.get(str(media_id))
                if filename and filename in self._records:
                    self._records[filename]["favorite"] = bool(favorite)
                    changed += 1
            if changed:
                self._save()
        return changed

    def associate(self, filename: str, video_id: str, title: str | None = None) -> None:
        """Attach a YouTube ID to a legacy file whose embedded metadata is incomplete."""
        key = Path(filename).as_posix()
        with self._lock:
            if key not in self._records:
                raise KeyError(f"Файл не найден в индексе: {filename}")
            self._records[key]["video_id"] = video_id
            self._records[key]["source_url"] = f"https://www.youtube.com/watch?v={video_id}"
            if title:
                self._records[key]["title"] = title
            self._save()

    @staticmethod
    def public(record: dict[str, Any]) -> dict[str, Any]:
        public = copy.deepcopy(record)
        public.pop("scanned", None)
        public["favorite"] = bool(public.get("favorite"))
        public["size_text"] = format_bytes(public.get("size"))
        return public

    def list(
        self,
        media_filter: str = "all",
        search: str = "",
        sort: str = "date",
        collection: str | None = None,
    ) -> list[dict[str, Any]]:
        search_folded = search.casefold().strip()
        with self._lock:
            rows = []
            for record in self._records.values():
                if media_filter == "favorites":
                    if not record.get("favorite"):
                        continue
                elif media_filter != "all" and record.get("media_type") != media_filter and record.get("format") != media_filter:
                    continue
                if collection is not None and (record.get("playlist_title") or "") != collection:
                    continue
                haystack = " ".join(
                    str(record.get(key) or "") for key in ("title", "artist", "filename", "playlist_title")
                )
                if search_folded and search_folded not in haystack.casefold():
                    continue
                rows.append(self.public(record))
        if sort == "name":
            rows.sort(key=lambda row: str(row.get("title") or "").casefold())
        elif sort == "artist":
            rows.sort(key=lambda row: (str(row.get("artist") or "￿").casefold(), str(row.get("title") or "").casefold()))
        elif sort == "size":
            rows.sort(key=lambda row: row.get("size") or 0, reverse=True)
        elif sort == "duration":
            rows.sort(key=lambda row: row.get("duration") or 0, reverse=True)
        elif sort == "playlist":
            rows.sort(key=lambda row: (row.get("playlist_index") or 10**9, str(row.get("title") or "").casefold()))
        else:
            rows.sort(key=lambda row: row.get("modified") or 0, reverse=True)
        return rows

    def resolve(self, media_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Return public records for ids in the given order, silently dropping unknown ids."""
        with self._lock:
            rows = []
            for media_id in media_ids:
                filename = self._by_id.get(str(media_id))
                record = self._records.get(filename) if filename else None
                if record:
                    rows.append(self.public(record))
            return rows

    def collections(self) -> list[dict[str, Any]]:
        """Group files by the YouTube playlist they were downloaded from."""
        groups: dict[str, dict[str, Any]] = {}
        with self._lock:
            for record in self._records.values():
                name = record.get("playlist_title")
                if not name:
                    continue
                group = groups.setdefault(name, {
                    "name": name, "count": 0, "audio_count": 0, "video_count": 0,
                    "duration": 0.0, "size": 0, "thumbnails": [], "modified": 0,
                })
                group["count"] += 1
                group["audio_count" if record.get("media_type") == "audio" else "video_count"] += 1
                group["duration"] += float(record.get("duration") or 0)
                group["size"] += int(record.get("size") or 0)
                group["modified"] = max(group["modified"], record.get("modified") or 0)
                if record.get("thumbnail") and len(group["thumbnails"]) < 4:
                    group["thumbnails"].append(record["thumbnail"])
        rows = sorted(groups.values(), key=lambda group: group["modified"], reverse=True)
        for row in rows:
            row["size_text"] = format_bytes(row["size"])
        return rows

    def stats(self) -> dict[str, Any]:
        with self._lock:
            rows = list(self._records.values())
        total_size = sum(int(row.get("size") or 0) for row in rows)
        return {
            "total_count": len(rows),
            "audio_count": sum(row.get("media_type") == "audio" for row in rows),
            "video_count": sum(row.get("media_type") == "video" for row in rows),
            "favorite_count": sum(bool(row.get("favorite")) for row in rows),
            "total_size": total_size,
            "total_size_text": format_bytes(total_size),
            "total_duration": round(sum(float(row.get("duration") or 0) for row in rows)),
            "revision": self.revision,
        }


PUBLIC_JOB_FIELDS = (
    "id", "batch", "seq", "url", "video_id", "title", "format", "state", "status", "progress",
    "speed_text", "eta", "downloaded_bytes", "total_bytes", "filename", "media_id", "error",
    "created_at", "updated_at",
)


class JobStore:
    """Thread-safe job registry with cheap incremental snapshots for polling clients."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancelled: set[str] = set()
        self._version = 0
        self._seq = 0
        self._batch = 0
        # Changes whenever jobs disappear, so clients know a delta is not enough.
        self._epoch = uuid.uuid4().hex[:8]

    def _touch(self, job: dict[str, Any]) -> None:
        self._version += 1
        job["_v"] = self._version
        job["updated_at"] = utc_now()

    def next_batch(self) -> int:
        with self._lock:
            self._batch += 1
            return self._batch

    def add(self, job: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            job.setdefault("seq", self._seq)
            job.setdefault("batch", self._batch)
            self._jobs[job["id"]] = job
            self._touch(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            if job_id not in self._jobs:
                return
            self._jobs[job_id].update(changes)
            self._touch(self._jobs[job_id])

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = copy.deepcopy(list(self._jobs.values()))
        jobs.sort(key=lambda job: (-int(job.get("batch") or 0), int(job.get("seq") or 0)))
        return jobs

    @staticmethod
    def public(job: dict[str, Any]) -> dict[str, Any]:
        view = {field: job.get(field) for field in PUBLIC_JOB_FIELDS}
        item = job.get("item") or {}
        view["thumbnail"] = item.get("thumbnail")
        view["playlist_title"] = item.get("playlist_title")
        return view

    def changes(self, since: int = 0, epoch: str | None = None) -> dict[str, Any]:
        with self._lock:
            full = since <= 0 or epoch != self._epoch
            jobs = [
                self.public(job) for job in self._jobs.values()
                if full or int(job.get("_v") or 0) > since
            ]
            return {
                "epoch": self._epoch,
                "version": self._version,
                "full": full,
                "jobs": jobs,
                "summary": self.summary(),
            }

    def summary(self) -> dict[str, int]:
        with self._lock:
            states = [job.get("state") for job in self._jobs.values()]
        return {
            "total": len(states),
            "queued": states.count("queued"),
            "running": sum(state in ACTIVE_STATES for state in states),
            "active": sum(state not in TERMINAL_STATES for state in states),
            "completed": states.count("completed"),
            "skipped": states.count("skipped"),
            "failed": states.count("failed"),
            "cancelled": states.count("cancelled"),
        }

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                return False
            if self._jobs[job_id].get("state") in TERMINAL_STATES:
                return True
            self._cancelled.add(job_id)
            if self._jobs[job_id].get("state") == "queued":
                self._jobs[job_id].update(state="cancelled", status="Отменено")
                self._touch(self._jobs[job_id])
            return True

    def cancel_all(self) -> int:
        with self._lock:
            ids = [job_id for job_id, job in self._jobs.items() if job.get("state") not in TERMINAL_STATES]
        for job_id in ids:
            self.cancel(job_id)
        return len(ids)

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    def remove(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("state") not in TERMINAL_STATES:
                return False
            self._jobs.pop(job_id)
            self._cancelled.discard(job_id)
            self._epoch = uuid.uuid4().hex[:8]
            return True

    def ids_in_state(self, state: str) -> list[str]:
        with self._lock:
            return [job_id for job_id, job in self._jobs.items() if job.get("state") == state]

    def clear_terminal(self) -> int:
        with self._lock:
            ids = [job_id for job_id, job in self._jobs.items() if job.get("state") in TERMINAL_STATES]
            for job_id in ids:
                self._jobs.pop(job_id, None)
                self._cancelled.discard(job_id)
            if ids:
                self._epoch = uuid.uuid4().hex[:8]
            return len(ids)


def stream_phase(info: dict[str, Any], output_format: str) -> tuple[str, float, float]:
    """Map one stream of a merged download onto the overall progress bar.

    yt-dlp downloads video and audio of a merged format one after another and
    reports progress for each separately; video is usually ~90% of the bytes.
    """
    vcodec = str(info.get("vcodec") or "")
    acodec = str(info.get("acodec") or "")
    if output_format in VIDEO_FORMATS:
        if vcodec and vcodec != "none" and acodec == "none":
            return "видео", 0.0, 0.9
        if vcodec == "none" and acodec and acodec != "none":
            return "аудио", 0.9, 0.1
    return "", 0.0, 1.0


@dataclass
class DownloadConfig:
    download_root: Path
    thumbnails_root: Path
    ffmpeg_dir: Path | None
    workers: int = 3


class DownloadManager:
    def __init__(self, config: DownloadConfig, library: MediaLibrary) -> None:
        self.config = config
        self.library = library
        self.jobs = JobStore()
        self.executor = ThreadPoolExecutor(max_workers=max(1, min(config.workers, 8)), thread_name_prefix="pyloader")
        self._active_lock = threading.RLock()
        self._active_keys: set[tuple[str, str]] = set()

    def _job_key(self, item: dict[str, Any], output_format: str) -> tuple[str, str]:
        return (str(item.get("id") or item.get("url")), output_format)

    def enqueue(self, items: Iterable[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
        output_format = str(options.get("format") or "mp3").lower()
        if output_format not in SUPPORTED_FORMATS:
            raise ValueError("Неподдерживаемый формат")
        normalized_options = {
            "format": output_format,
            "quality": str(options.get("quality") or "max"),
            "bitrate": str(options.get("bitrate") or "192"),
            "skip_duplicates": bool(options.get("skip_duplicates", True)),
            "organize": bool(options.get("organize", False)),
            "numerate": bool(options.get("numerate", False)),
            "subtitles": bool(options.get("subtitles", False)),
            "auto_subtitles": bool(options.get("auto_subtitles", False)),
            "subtitle_languages": str(options.get("subtitle_languages") or "ru,en"),
            "cookies_browser": str(options.get("cookies_browser") or "none"),
            "proxy": str(options.get("proxy") or ""),
        }
        added = skipped_active = skipped_library = skipped_batch = 0
        job_ids: list[str] = []
        seen: set[tuple[str, str]] = set()
        batch = self.jobs.next_batch()
        for raw_item in items:
            item = copy.deepcopy(raw_item)
            item["url"] = ensure_http_url(str(item.get("url") or ""))
            key = self._job_key(item, output_format)
            if key in seen:
                skipped_batch += 1
                continue
            seen.add(key)
            if normalized_options["skip_duplicates"] and self.library.has(item.get("id"), output_format):
                skipped_library += 1
                continue
            with self._active_lock:
                if key in self._active_keys:
                    skipped_active += 1
                    continue
                self._active_keys.add(key)
            job_id = str(uuid.uuid4())
            job = {
                "id": job_id,
                "batch": batch,
                "item": item,
                "options": normalized_options,
                "url": item["url"],
                "video_id": item.get("id"),
                "title": item.get("title") or "Получение данных…",
                "format": output_format,
                "state": "queued",
                "status": "В очереди",
                "progress": 0.0,
                "speed": None,
                "speed_text": "",
                "eta": None,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "filename": None,
                "media_id": None,
                "error": None,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            self.jobs.add(job)
            self.executor.submit(self._run, job_id, key)
            added += 1
            job_ids.append(job_id)
        return {
            "added": added,
            "skipped_active": skipped_active,
            "skipped_library": skipped_library,
            "skipped_batch": skipped_batch,
            "job_ids": job_ids,
        }

    def _progress_hook(self, job_id: str, data: dict[str, Any]) -> None:
        if self.jobs.is_cancelled(job_id):
            raise DownloadCancelled("Загрузка отменена")
        status = data.get("status")
        if status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            job = self.jobs.get(job_id) or {}
            phase, start, weight = stream_phase(data.get("info_dict") or {}, str(job.get("format") or ""))
            fraction = min(downloaded / total, 1.0) if total else 0.0
            progress = (start + fraction * weight) * 100
            speed = data.get("speed")
            self.jobs.update(
                job_id,
                state="downloading",
                status=f"Скачивание · {phase}" if phase else "Скачивание",
                progress=round(min(max(progress, float(job.get("progress") or 0)), 99.5), 1),
                speed=speed,
                speed_text=f"{format_bytes(speed)}/с" if speed else "",
                eta=data.get("eta"),
                downloaded_bytes=downloaded,
                total_bytes=total,
            )
        elif status == "finished":
            job = self.jobs.get(job_id) or {}
            phase, start, weight = stream_phase(data.get("info_dict") or {}, str(job.get("format") or ""))
            if phase == "видео":
                self.jobs.update(job_id, progress=round((start + weight) * 100, 1))
            else:
                self.jobs.update(job_id, state="processing", status="Обработка", progress=99.5)

    def _postprocessor_hook(self, job_id: str, data: dict[str, Any]) -> None:
        if self.jobs.is_cancelled(job_id):
            raise DownloadCancelled("Загрузка отменена")
        if data.get("status") == "started":
            name = data.get("postprocessor") or "FFmpeg"
            self.jobs.update(job_id, state="processing", status=f"Обработка · {name}", progress=99.5)

    def _destination(self, item: dict[str, Any], options: dict[str, Any]) -> tuple[Path, str]:
        folder = self.config.download_root
        if options.get("organize") and item.get("playlist_title"):
            folder = folder / safe_component(str(item["playlist_title"]), "playlist", 100)
        folder.mkdir(parents=True, exist_ok=True)
        prefix = ""
        if options.get("numerate") and item.get("playlist_index"):
            prefix = f"{int(item['playlist_index']):03d} - "
        return folder, prefix

    def _postprocessors(self, options: dict[str, Any]) -> list[dict[str, Any]]:
        output_format = options["format"]
        processors: list[dict[str, Any]] = []
        if output_format == "mp3":
            processors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": options.get("bitrate", "192"),
            })
        elif output_format == "m4a":
            processors.append({"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "0"})
        elif output_format == "opus":
            processors.append({"key": "FFmpegExtractAudio", "preferredcodec": "opus", "preferredquality": "0"})
        if options.get("subtitles") and output_format in VIDEO_FORMATS:
            processors.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
        processors.append({"key": "FFmpegMetadata", "add_metadata": True})
        if output_format in AUDIO_FORMATS:
            processors.extend([
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ])
        return processors

    def _download_options(
        self,
        job_id: str,
        item: dict[str, Any],
        options: dict[str, Any],
        media_template: str,
        thumbnail_template: str,
    ) -> dict[str, Any]:
        logger = JobLogger()
        ydl_options = ydl_base_options(options.get("cookies_browser", "none"), options.get("proxy", ""))
        ydl_options.update({
            "logger": logger,
            "noplaylist": True,
            "format": build_format_selector(options["format"], options.get("quality", "max")),
            "outtmpl": {"default": media_template, "thumbnail": thumbnail_template},
            "windowsfilenames": True,
            "continuedl": True,
            "overwrites": False,
            "concurrent_fragment_downloads": 4,
            "writethumbnail": True,
            "postprocessors": self._postprocessors(options),
            "progress_hooks": [lambda data: self._progress_hook(job_id, data)],
            "postprocessor_hooks": [lambda data: self._postprocessor_hook(job_id, data)],
        })
        if self.config.ffmpeg_dir:
            ydl_options["ffmpeg_location"] = str(self.config.ffmpeg_dir)
        if options.get("subtitles") and options["format"] in VIDEO_FORMATS:
            languages = [part.strip() for part in options.get("subtitle_languages", "ru,en").split(",") if part.strip()]
            ydl_options.update({
                "writesubtitles": True,
                "writeautomaticsub": bool(options.get("auto_subtitles")),
                "subtitleslangs": languages or ["ru", "en"],
                "subtitlesformat": "srt/best",
            })
        if options["format"] in VIDEO_FORMATS:
            ydl_options["merge_output_format"] = options["format"]
        return ydl_options

    def _metadata(self, item: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        metadata_options = ydl_base_options(options.get("cookies_browser", "none"), options.get("proxy", ""))
        metadata_options.update({"noplaylist": True, "skip_download": True})
        with yt_dlp.YoutubeDL(metadata_options) as ydl:
            info = ydl.extract_info(item["url"], download=False)
        if not info:
            raise RuntimeError("YouTube не вернул метаданные")
        return info

    def _run(self, job_id: str, key: tuple[str, str]) -> None:
        job = self.jobs.get(job_id)
        if not job:
            return
        item = job["item"]
        options = job["options"]
        output_format = options["format"]
        try:
            if self.jobs.is_cancelled(job_id):
                raise DownloadCancelled("Загрузка отменена")
            self.jobs.update(job_id, state="extracting", status="Получение данных")
            info = self._metadata(item, options)
            if self.jobs.is_cancelled(job_id):
                raise DownloadCancelled("Загрузка отменена")
            video_id = info.get("id") or item.get("id")
            title = info.get("title") or item.get("title") or video_id or "Без названия"
            self.jobs.update(job_id, video_id=video_id, title=title)
            if options.get("skip_duplicates") and self.library.has(video_id, output_format):
                self.jobs.update(job_id, state="skipped", status="Уже в медиатеке", progress=100.0)
                return

            item = {**item, "id": video_id, "title": title}
            folder, prefix = self._destination(item, options)
            stem = prefix + safe_component(title, video_id or "media")
            expected = folder / f"{stem}.{output_format}"
            if expected.exists() and not self.library.has(video_id, output_format):
                stem = f"{stem} [{video_id or uuid.uuid4().hex[:8]}]"
                expected = folder / f"{stem}.{output_format}"

            escaped_folder = str(folder).replace("%", "%%")
            escaped_stem = stem.replace("%", "%%")
            media_template = str(Path(escaped_folder) / f"{escaped_stem}.%(ext)s")
            thumbnail_template = str(self.config.thumbnails_root / f"{video_id or job_id}.%(ext)s")
            ydl_options = self._download_options(job_id, item, options, media_template, thumbnail_template)
            with yt_dlp.YoutubeDL(ydl_options) as ydl:
                result = ydl.extract_info(item["url"], download=True)
            if self.jobs.is_cancelled(job_id):
                raise DownloadCancelled("Загрузка отменена")

            if not expected.exists():
                candidates = [
                    path for path in folder.glob(f"{glob_escape(stem)}.*")
                    if path.suffix.lower().lstrip(".") == output_format and ".temp." not in path.name.lower()
                ]
                if candidates:
                    expected = max(candidates, key=lambda path: path.stat().st_mtime)
            if not expected.exists() or expected.stat().st_size == 0:
                raise RuntimeError("Файл не появился после обработки FFmpeg")

            thumbnail = None
            for candidate in self.config.thumbnails_root.glob(f"{glob_escape(video_id or job_id)}.*"):
                if candidate.suffix.lower() in THUMBNAIL_SUFFIXES:
                    thumbnail = candidate.relative_to(self.config.thumbnails_root).as_posix()
                    break
            register_options = {**options, **{
                "playlist_title": item.get("playlist_title"),
                "playlist_index": item.get("playlist_index"),
            }}
            record = self.library.register(expected, result or info, register_options, thumbnail)
            self.jobs.update(
                job_id,
                state="completed",
                status="Готово",
                progress=100.0,
                filename=record["filename"],
                media_id=record["id"],
                speed=None,
                speed_text="",
                eta=0,
            )
        except DownloadCancelled:
            self.jobs.update(job_id, state="cancelled", status="Отменено", error=None)
        except Exception as exc:
            if self.jobs.is_cancelled(job_id):
                self.jobs.update(job_id, state="cancelled", status="Отменено", error=None)
            else:
                message = clean_error(exc)
                self.jobs.update(job_id, state="failed", status="Ошибка", error=message)
        finally:
            with self._active_lock:
                self._active_keys.discard(key)

    def retry(self, job_id: str) -> dict[str, Any]:
        return self.retry_many([job_id])

    def retry_many(self, job_ids: Iterable[str]) -> dict[str, Any]:
        jobs = [job for job in (self.jobs.get(job_id) for job_id in job_ids) if job]
        if not jobs:
            raise KeyError("Задача не найдена")
        totals = {"added": 0, "skipped_active": 0, "skipped_library": 0, "skipped_batch": 0, "job_ids": []}
        # Jobs with identical options are re-queued together so they share one batch.
        groups: dict[str, list[dict[str, Any]]] = {}
        for job in jobs:
            groups.setdefault(json.dumps(job["options"], sort_keys=True), []).append(job)
        for group in groups.values():
            result = self.enqueue([job["item"] for job in group], group[0]["options"])
            for key, value in result.items():
                totals[key] += value
            for job in group:
                # The new attempt replaces the old card instead of duplicating it.
                self.jobs.remove(job["id"])
        return totals

    def retry_failed(self) -> dict[str, Any]:
        failed = self.jobs.ids_in_state("failed")
        if not failed:
            return {"added": 0, "skipped_active": 0, "skipped_library": 0, "skipped_batch": 0, "job_ids": []}
        return self.retry_many(failed)

    def active_count(self) -> int:
        return self.jobs.summary()["active"]


def find_ffmpeg(base_dir: Path) -> Path | None:
    local = base_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if local.exists():
        return base_dir
    discovered = shutil.which("ffmpeg")
    return Path(discovered).parent if discovered else None


def open_in_file_manager(path: Path, select: bool = False) -> None:
    resolved = path.resolve()
    if os.name == "nt":
        args = ["explorer.exe"]
        if select and resolved.is_file():
            args.append(f"/select,{resolved}")
        else:
            args.append(str(resolved if resolved.is_dir() else resolved.parent))
        subprocess.Popen(args)
    elif sys.platform == "darwin":  # pragma: no cover - Windows workspace
        args = ["open"]
        if select:
            args.append("-R")
        args.append(str(resolved))
        subprocess.Popen(args)
    else:  # pragma: no cover - Windows workspace
        subprocess.Popen(["xdg-open", str(resolved if resolved.is_dir() else resolved.parent)])
