from __future__ import annotations

import copy
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable

from .core import read_json, utc_now, write_json_atomic


MAX_PLAYLIST_NAME = 80


def _clean_name(name: Any) -> str:
    value = " ".join(str(name or "").split())
    if not value:
        raise ValueError("Название плейлиста не может быть пустым")
    return value[:MAX_PLAYLIST_NAME]


class PlaylistStore:
    """User playlists that reference library items by their stable media id."""

    def __init__(self, state_dir: Path) -> None:
        self.state_file = state_dir / "playlists.json"
        self._lock = threading.RLock()
        data = read_json(self.state_file, {})
        items = data.get("playlists", []) if isinstance(data, dict) else []
        self._playlists: dict[str, dict[str, Any]] = {
            row["id"]: {**row, "items": [str(item) for item in row.get("items", [])]}
            for row in items
            if isinstance(row, dict) and row.get("id") and row.get("name")
        }

    def _save(self) -> None:
        write_json_atomic(self.state_file, {
            "version": 1,
            "updated_at": utc_now(),
            "playlists": list(self._playlists.values()),
        })

    def _require(self, playlist_id: str) -> dict[str, Any]:
        playlist = self._playlists.get(playlist_id)
        if not playlist:
            raise KeyError("Плейлист не найден")
        return playlist

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = copy.deepcopy(list(self._playlists.values()))
        rows.sort(key=lambda row: row.get("created_at") or "")
        return rows

    def get(self, playlist_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._require(playlist_id))

    def create(self, name: Any, media_ids: Iterable[str] = ()) -> dict[str, Any]:
        with self._lock:
            now = utc_now()
            playlist = {
                "id": uuid.uuid4().hex[:10],
                "name": _clean_name(name),
                "items": list(dict.fromkeys(str(media_id) for media_id in media_ids)),
                "created_at": now,
                "updated_at": now,
            }
            self._playlists[playlist["id"]] = playlist
            self._save()
            return copy.deepcopy(playlist)

    def rename(self, playlist_id: str, name: Any) -> dict[str, Any]:
        with self._lock:
            playlist = self._require(playlist_id)
            playlist["name"] = _clean_name(name)
            playlist["updated_at"] = utc_now()
            self._save()
            return copy.deepcopy(playlist)

    def delete(self, playlist_id: str) -> None:
        with self._lock:
            self._require(playlist_id)
            self._playlists.pop(playlist_id)
            self._save()

    def add_items(self, playlist_id: str, media_ids: Iterable[str]) -> int:
        with self._lock:
            playlist = self._require(playlist_id)
            existing = set(playlist["items"])
            added = [media_id for media_id in dict.fromkeys(str(value) for value in media_ids) if media_id not in existing]
            if added:
                playlist["items"].extend(added)
                playlist["updated_at"] = utc_now()
                self._save()
            return len(added)

    def remove_items(self, playlist_id: str, media_ids: Iterable[str]) -> int:
        with self._lock:
            playlist = self._require(playlist_id)
            drop = {str(media_id) for media_id in media_ids}
            before = len(playlist["items"])
            playlist["items"] = [media_id for media_id in playlist["items"] if media_id not in drop]
            removed = before - len(playlist["items"])
            if removed:
                playlist["updated_at"] = utc_now()
                self._save()
            return removed

    def reorder(self, playlist_id: str, media_ids: Iterable[str]) -> dict[str, Any]:
        with self._lock:
            playlist = self._require(playlist_id)
            order = [str(media_id) for media_id in media_ids]
            if sorted(order) != sorted(playlist["items"]):
                raise ValueError("Новый порядок должен содержать те же треки")
            playlist["items"] = order
            playlist["updated_at"] = utc_now()
            self._save()
            return copy.deepcopy(playlist)

    def forget_media(self, media_ids: Iterable[str]) -> None:
        """Drop deleted files from every playlist."""
        drop = {str(media_id) for media_id in media_ids}
        with self._lock:
            changed = False
            for playlist in self._playlists.values():
                kept = [media_id for media_id in playlist["items"] if media_id not in drop]
                if len(kept) != len(playlist["items"]):
                    playlist["items"] = kept
                    playlist["updated_at"] = utc_now()
                    changed = True
            if changed:
                self._save()
