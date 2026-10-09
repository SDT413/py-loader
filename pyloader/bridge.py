"""Phone bridge: an opt-in, token-protected, read-only LAN server.

The desktop UI stays bound to 127.0.0.1. When the user enables the bridge, a
second, much smaller Flask app is served on the local network so a phone can
pair by scanning a QR code, then stream or download files. The same `/api/v1`
endpoints are meant to be used by the future PyLoader mobile app; see
docs/mobile-bridge.md.
"""

from __future__ import annotations

import copy
import hmac
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from flask import Flask, jsonify, redirect, render_template, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

from .core import MediaLibrary, read_json, utc_now, write_json_atomic
from .playlists import PlaylistStore
from .version import __version__


API_VERSION = 1
TOKEN_COOKIE = "pyloader_token"
TOKEN_MAX_AGE = 365 * 24 * 3600


def lan_addresses() -> list[str]:
    """IPv4 addresses a phone on the same network can most likely reach, best first."""
    found: list[str] = []
    try:
        # Connecting a UDP socket sends nothing; it only selects the outgoing interface.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = str(info[4][0])
            if address not in found:
                found.append(address)
    except OSError:
        pass
    usable = [address for address in found if not address.startswith(("127.", "169.254."))]
    return usable or ["127.0.0.1"]


class PhoneBridge:
    def __init__(
        self,
        state_dir: Path,
        library: MediaLibrary,
        playlists: PlaylistStore,
        base_dir: Path,
        port: int = 5001,
        host: str = "0.0.0.0",
    ) -> None:
        self.state_file = state_dir / "bridge.json"
        self.library = library
        self.playlists = playlists
        self.base_dir = base_dir
        self.port = port
        self.host = host
        self.error: str | None = None
        self._lock = threading.RLock()
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._addresses: list[str] = []
        self._addresses_at = -1e9
        data = read_json(self.state_file, {})
        data = data if isinstance(data, dict) else {}
        self._token = str(data.get("token") or secrets.token_urlsafe(18))
        self._enabled = bool(data.get("enabled"))
        self._address = data.get("address")
        self._outbox: list[dict[str, Any]] = [
            row for row in data.get("outbox", []) if isinstance(row, dict) and row.get("id")
        ]
        self._save()

    # -- persistence -----------------------------------------------------
    def _save(self) -> None:
        with self._lock:
            write_json_atomic(self.state_file, {
                "version": 1,
                "token": self._token,
                "enabled": self._enabled,
                "address": self._address,
                "outbox": self._outbox,
            })

    # -- auth ------------------------------------------------------------
    def check_token(self, candidate: str | None) -> bool:
        if not candidate:
            return False
        return hmac.compare_digest(str(candidate).encode(), self._token.encode())

    def rotate_token(self) -> None:
        with self._lock:
            self._token = secrets.token_urlsafe(18)
            self._save()

    # -- addressing ------------------------------------------------------
    def addresses(self) -> list[str]:
        now = time.monotonic()
        if now - self._addresses_at > 15:
            self._addresses = lan_addresses()
            self._addresses_at = now
        return self._addresses

    def address(self) -> str:
        addresses = self.addresses()
        return self._address if self._address in addresses else addresses[0]

    def set_address(self, address: str) -> None:
        if address not in self.addresses():
            raise ValueError("Этот адрес не принадлежит компьютеру")
        with self._lock:
            self._address = address
            self._save()

    def base_url(self) -> str:
        return f"http://{self.address()}:{self.port}"

    def connect_url(self) -> str:
        return f"{self.base_url()}/connect?token={self._token}"

    # -- server lifecycle --------------------------------------------------
    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            try:
                server = make_server(self.host, self.port, create_bridge_app(self), threaded=True)
            except OSError as exc:
                self.error = f"Не удалось открыть порт {self.port}: {exc.strerror or exc}"
                raise RuntimeError(self.error) from exc
            self._server = server
            self._thread = threading.Thread(target=server.serve_forever, name="pyloader-bridge", daemon=True)
            self._thread.start()
            self.error = None
            self._enabled = True
            self._save()

    def stop(self) -> None:
        with self._lock:
            server, thread = self._server, self._thread
            self._server = self._thread = None
            self._enabled = False
            self._save()
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def autostart(self) -> None:
        """Re-open the bridge on launch if it was left enabled last time."""
        if self._enabled:
            try:
                self.start()
            except RuntimeError:
                pass

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.running,
            "port": self.port,
            "address": self.address(),
            "addresses": self.addresses(),
            "url": self.base_url(),
            "connect_url": self.connect_url(),
            "error": self.error,
            "outbox_count": len(self._outbox),
            "outbox_pending": sum(not row.get("delivered_at") for row in self._outbox),
        }

    # -- outbox ------------------------------------------------------------
    def outbox_add(self, media_ids: Iterable[str]) -> int:
        with self._lock:
            present = {row["id"] for row in self._outbox}
            added = 0
            for media_id in dict.fromkeys(str(value) for value in media_ids):
                if media_id in present or not self.library.by_id(media_id):
                    continue
                self._outbox.insert(0, {"id": media_id, "added_at": utc_now(), "delivered_at": None})
                added += 1
            if added:
                self._save()
            return added

    def outbox_remove(self, media_ids: Iterable[str]) -> int:
        drop = {str(media_id) for media_id in media_ids}
        with self._lock:
            before = len(self._outbox)
            self._outbox = [row for row in self._outbox if row["id"] not in drop]
            removed = before - len(self._outbox)
            if removed:
                self._save()
            return removed

    def outbox_clear(self, delivered_only: bool = False) -> int:
        with self._lock:
            before = len(self._outbox)
            self._outbox = [row for row in self._outbox if delivered_only and not row.get("delivered_at")]
            self._save()
            return before - len(self._outbox)

    def mark_delivered(self, media_id: str) -> bool:
        with self._lock:
            for row in self._outbox:
                if row["id"] == media_id:
                    if not row.get("delivered_at"):
                        row["delivered_at"] = utc_now()
                        self._save()
                    return True
            return False

    def outbox(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = copy.deepcopy(self._outbox)
        items = []
        for row in rows:
            record = self.library.by_id(row["id"])
            if record:
                items.append({**MediaLibrary.public(record), "sent_at": row["added_at"], "delivered_at": row.get("delivered_at")})
        return items


def media_view(record: dict[str, Any]) -> dict[str, Any]:
    """Shape of a media item in the public, versioned bridge API."""
    media_id = record["id"]
    return {
        "id": media_id,
        "title": record.get("title"),
        "artist": record.get("artist"),
        "media_type": record.get("media_type"),
        "format": record.get("format"),
        "duration": record.get("duration"),
        "size": record.get("size"),
        "modified": record.get("modified"),
        "video_id": record.get("video_id"),
        "playlist_title": record.get("playlist_title"),
        "favorite": bool(record.get("favorite")),
        "stream_url": f"/api/v1/media/{media_id}/stream",
        "download_url": f"/api/v1/media/{media_id}/download",
        "cover_url": f"/api/v1/media/{media_id}/cover" if record.get("thumbnail") else None,
        **({"sent_at": record["sent_at"], "delivered_at": record.get("delivered_at")} if "sent_at" in record else {}),
    }


def create_bridge_app(bridge: PhoneBridge) -> Flask:
    app = Flask(
        "pyloader.bridge",
        template_folder=str(bridge.base_dir / "templates"),
        static_folder=str(bridge.base_dir / "static" / "phone"),
        static_url_path="/static",
    )
    app.json.ensure_ascii = False
    library = bridge.library
    open_endpoints = {"index", "connect", "static"}

    def request_token() -> str:
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return request.args.get("token") or request.cookies.get(TOKEN_COOKIE) or ""

    def require_media(media_id: str) -> tuple[dict[str, Any], Path]:
        record = library.by_id(media_id)
        if not record:
            raise KeyError("Файл не найден")
        path = library.path_of(record)
        if not path.is_file():
            raise KeyError("Файл не найден на диске")
        return record, path

    @app.before_request
    def guard():
        if request.endpoint in open_endpoints:
            return None
        if not bridge.check_token(request_token()):
            return jsonify({"ok": False, "error": "Нужна привязка: отсканируйте QR-код в PyLoader на компьютере"}), 401
        return None

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.endpoint != "static" and not request.path.endswith(("/stream", "/cover")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(Exception)
    def handle_error(error: Exception):
        if isinstance(error, HTTPException):
            return jsonify({"ok": False, "error": error.description}), error.code or 500
        status = 404 if isinstance(error, KeyError) else 400 if isinstance(error, ValueError) else 500
        return jsonify({"ok": False, "error": str(error).strip("'") or "Ошибка"}), status

    @app.get("/")
    def index():
        paired = bridge.check_token(request.cookies.get(TOKEN_COOKIE))
        return render_template("phone.html", paired=paired, version=__version__)

    @app.get("/connect")
    def connect():
        token = request.args.get("token", "")
        if not bridge.check_token(token):
            return render_template("phone.html", paired=False, invalid=True, version=__version__), 401
        response = redirect("/")
        response.set_cookie(TOKEN_COOKIE, token, max_age=TOKEN_MAX_AGE, httponly=True, samesite="Lax")
        return response

    @app.get("/api/v1/ping")
    def ping():
        return jsonify({
            "ok": True, "name": "PyLoader", "version": __version__, "api": API_VERSION,
            "device": socket.gethostname(),
        })

    @app.get("/api/v1/library")
    def api_library():
        media_type = request.args.get("type", "all")
        sort = request.args.get("sort", "date")
        rows = library.list(media_type, request.args.get("search", ""), sort)
        return jsonify({"ok": True, "items": [media_view(row) for row in rows], "total": len(rows)})

    @app.get("/api/v1/outbox")
    def api_outbox():
        return jsonify({"ok": True, "items": [media_view(row) for row in bridge.outbox()]})

    @app.post("/api/v1/outbox/<media_id>/received")
    def api_outbox_received(media_id: str):
        if not bridge.mark_delivered(media_id):
            raise KeyError("Этого файла нет среди отправленных")
        return jsonify({"ok": True})

    @app.get("/api/v1/playlists")
    def api_playlists():
        playlists = []
        for playlist in bridge.playlists.list():
            rows = library.resolve(playlist["items"])
            playlists.append({
                "id": playlist["id"],
                "name": playlist["name"],
                "items": [row["id"] for row in rows],
                "count": len(rows),
                "duration": round(sum(float(row.get("duration") or 0) for row in rows)),
                "updated_at": playlist["updated_at"],
            })
        collections = [
            {key: row[key] for key in ("name", "count", "duration")} for row in library.collections()
        ]
        return jsonify({"ok": True, "playlists": playlists, "collections": collections})

    @app.get("/api/v1/playlists/<playlist_id>")
    def api_playlist(playlist_id: str):
        playlist = bridge.playlists.get(playlist_id)
        rows = library.resolve(playlist["items"])
        return jsonify({"ok": True, "playlist": {"id": playlist["id"], "name": playlist["name"]},
                        "items": [media_view(row) for row in rows]})

    @app.get("/api/v1/collections/items")
    def api_collection():
        rows = library.list("all", "", "playlist", request.args.get("name", ""))
        return jsonify({"ok": True, "items": [media_view(row) for row in rows]})

    @app.get("/api/v1/media/<media_id>")
    def api_media(media_id: str):
        record, _path = require_media(media_id)
        return jsonify({"ok": True, "item": media_view(record)})

    @app.get("/api/v1/media/<media_id>/stream")
    def api_stream(media_id: str):
        _record, path = require_media(media_id)
        return send_file(path, conditional=True, max_age=3600)

    @app.get("/api/v1/media/<media_id>/download")
    def api_download(media_id: str):
        _record, path = require_media(media_id)
        bridge.mark_delivered(media_id)
        return send_file(path, as_attachment=True, download_name=path.name, conditional=True)

    @app.get("/api/v1/media/<media_id>/cover")
    def api_cover(media_id: str):
        record, _path = require_media(media_id)
        if not record.get("thumbnail"):
            raise KeyError("Обложки нет")
        cover = (library.thumbnails_root / record["thumbnail"]).resolve()
        if library.thumbnails_root not in cover.parents or not cover.is_file():
            raise KeyError("Обложки нет")
        return send_file(cover, max_age=86400)

    return app
