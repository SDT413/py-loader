from __future__ import annotations

import mimetypes
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import yt_dlp
from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .bridge import PhoneBridge
from .core import (
    AUDIO_FORMATS,
    BROWSER_NAMES,
    LIBRARY_SORTS,
    SUPPORTED_FORMATS,
    VIDEO_FORMATS,
    DownloadConfig,
    DownloadManager,
    MediaLibrary,
    find_ffmpeg,
    format_bytes,
    inspect_urls,
    open_in_file_manager,
)
from .playlists import PlaylistStore
from .version import __version__


# Windows sometimes maps .js to text/plain in the registry, which breaks ES modules,
# and older Pythons do not know the audio types the player needs.
for _type, _extension in (
    ("text/javascript", ".js"),
    ("text/css", ".css"),
    ("audio/ogg", ".opus"),
    ("audio/mp4", ".m4a"),
    ("video/x-matroska", ".mkv"),
    ("image/webp", ".webp"),
):
    mimetypes.add_type(_type, _extension)

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Недопустимый путь")
    return candidate


def _hostname(host: str) -> str:
    if host.startswith("["):
        return host[: host.find("]") + 1]
    return host.split(":", 1)[0]


def _ids(payload: dict[str, Any], key: str = "ids") -> list[str]:
    values = payload.get(key) or []
    if not isinstance(values, list) or not values:
        raise ValueError("Не выбраны файлы")
    if len(values) > 20_000:
        raise ValueError("Слишком много файлов за один раз")
    return [str(value) for value in values]


def _node_available() -> bool:
    path = os.environ.get("PATH") or ""
    executable = "node.exe" if os.name == "nt" else "node"
    return any((Path(part) / executable).exists() for part in path.split(os.pathsep) if part)


def create_app(config: dict[str, Any] | None = None) -> Flask:
    base_dir = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(base_dir / "templates"),
        static_folder=str(base_dir / "static"),
    )
    app.json.ensure_ascii = False
    app.config.update(
        DOWNLOAD_ROOT=str(base_dir / "downloads"),
        THUMBNAILS_ROOT=str(base_dir / "downloads" / "thumbnails"),
        MAX_WORKERS=int(os.environ.get("PYLOADER_WORKERS", "3")),
        BRIDGE_PORT=int(os.environ.get("PYLOADER_BRIDGE_PORT", "5001")),
        TESTING=False,
    )
    if config:
        app.config.update(config)

    download_root = Path(app.config["DOWNLOAD_ROOT"]).resolve()
    thumbnails_root = Path(app.config["THUMBNAILS_ROOT"]).resolve()
    download_root.mkdir(parents=True, exist_ok=True)
    thumbnails_root.mkdir(parents=True, exist_ok=True)
    library = MediaLibrary(download_root, thumbnails_root)
    playlists = PlaylistStore(library.state_dir)
    bridge = PhoneBridge(library.state_dir, library, playlists, base_dir, port=int(app.config["BRIDGE_PORT"]))
    manager = DownloadManager(
        DownloadConfig(
            download_root=download_root,
            thumbnails_root=thumbnails_root,
            ffmpeg_dir=find_ffmpeg(base_dir),
            workers=int(app.config["MAX_WORKERS"]),
        ),
        library,
    )
    app.extensions["pyloader_library"] = library
    app.extensions["pyloader_manager"] = manager
    app.extensions["pyloader_playlists"] = playlists
    app.extensions["pyloader_bridge"] = bridge

    @app.before_request
    def local_only():
        # The desktop UI can delete files and open the LAN bridge, so refuse
        # requests addressed to any other host name (DNS-rebinding protection).
        if _hostname(request.host or "") not in LOCAL_HOSTS:
            return Response("PyLoader принимает запросы только по адресу 127.0.0.1", status=403)
        return None

    @app.after_request
    def no_cache(response):
        # The app is local and updated in place. Prevent stale HTML/JS combinations
        # after an upgrade; they are more harmful here than a tiny cache saving.
        if not request.path.startswith(("/stream/", "/thumbs/")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.errorhandler(Exception)
    def handle_error(error: Exception):
        if isinstance(error, HTTPException):
            status = error.code or 500
            message = error.description
        elif isinstance(error, KeyError):
            status = 404
            message = str(error).strip("'")
        elif isinstance(error, ValueError):
            status = 400
            message = str(error).strip("'")
        else:
            status = 500
            message = str(error) or "Внутренняя ошибка"
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": message}), status
        return f"Ошибка: {message}", status

    def unlink(path: Path) -> None:
        # On Windows a file that is still being streamed to the player is locked
        # for a moment after playback stops, so retry briefly before giving up.
        for attempt in range(6):
            try:
                path.unlink(missing_ok=True)
                return
            except PermissionError:
                if attempt == 5:
                    raise ValueError(f"Файл занят другой программой: {path.name}") from None
                time.sleep(0.25)

    def delete_media(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """Delete files one by one; a locked file does not stop the rest."""
        deleted, failed = [], []
        for record in records:
            try:
                unlink(_inside(download_root, record["filename"]))
                deleted.append(record)
            except (OSError, ValueError):
                failed.append(record["filename"])
        if deleted:
            library.remove_many(record["filename"] for record in deleted)
            in_use = library.thumbnails_in_use()
            for thumb_name in {record.get("thumbnail") for record in deleted} - in_use - {None}:
                try:
                    unlink(_inside(thumbnails_root, thumb_name))
                except (OSError, ValueError):
                    pass
        return deleted, failed

    # -- pages -----------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    # -- analysis and queue ----------------------------------------------
    @app.post("/api/inspect")
    def api_inspect():
        payload = request.get_json(silent=True) or {}
        urls = payload.get("urls") or []
        if not isinstance(urls, list) or not urls:
            raise ValueError("Добавьте хотя бы одну ссылку")
        if len(urls) > 100:
            raise ValueError("За один анализ можно добавить не больше 100 ссылок")
        browser = str(payload.get("cookies_browser") or "none")
        if browser not in BROWSER_NAMES:
            raise ValueError("Неизвестный браузер для cookies")
        result = inspect_urls(urls, browser, str(payload.get("proxy") or ""))
        for item in result["items"]:
            item["library_formats"] = library.formats_for(item.get("id"))
        return jsonify({"ok": True, **result})

    @app.post("/api/jobs")
    def api_add_jobs():
        payload = request.get_json(silent=True) or {}
        items = payload.get("items") or []
        options = payload.get("options") or {}
        if not isinstance(items, list) or not items:
            raise ValueError("Нет выбранных видео")
        if len(items) > 10_000:
            raise ValueError("Слишком много элементов в одной очереди")
        result = manager.enqueue(items, options)
        return jsonify({"ok": True, **result})

    @app.get("/api/jobs")
    def api_jobs():
        since = request.args.get("since", 0, type=int)
        epoch = request.args.get("epoch")
        changes = manager.jobs.changes(since, epoch)
        return jsonify({
            "ok": True,
            **changes,
            "active": changes["summary"]["active"],
            "library_revision": library.revision,
        })

    @app.post("/api/jobs/<job_id>/cancel")
    def api_cancel_job(job_id: str):
        if not manager.cancel(job_id):
            return jsonify({"ok": False, "error": "Задача не найдена"}), 404
        return jsonify({"ok": True})

    @app.post("/api/jobs/<job_id>/retry")
    def api_retry_job(job_id: str):
        return jsonify({"ok": True, **manager.retry(job_id)})

    @app.delete("/api/jobs/<job_id>")
    def api_remove_job(job_id: str):
        if not manager.jobs.remove(job_id):
            raise ValueError("Убрать можно только завершённую задачу")
        return jsonify({"ok": True})

    @app.post("/api/jobs/cancel-all")
    def api_cancel_all():
        return jsonify({"ok": True, "cancelled": manager.cancel_all()})

    @app.post("/api/jobs/retry-failed")
    def api_retry_failed():
        return jsonify({"ok": True, **manager.retry_failed()})

    @app.delete("/api/jobs/completed")
    def api_clear_jobs():
        return jsonify({"ok": True, "cleared": manager.jobs.clear_terminal()})

    # -- library ---------------------------------------------------------
    @app.get("/api/library")
    def api_library():
        media_filter = request.args.get("filter", "all")
        search = request.args.get("search", "")
        sort = request.args.get("sort", "date")
        if sort not in LIBRARY_SORTS:
            sort = "date"
        collection = request.args.get("collection")
        limit = max(1, min(request.args.get("limit", 120, type=int), 1000))
        offset = max(0, request.args.get("offset", 0, type=int))
        if collection is None:
            rows = library.list(media_filter, search, sort)
        else:
            rows = library.list(media_filter, search, sort, collection)
        return jsonify({
            "ok": True,
            "items": rows[offset:offset + limit],
            "total": len(rows),
            "revision": library.revision,
        })

    @app.get("/api/library/ids")
    def api_library_ids():
        """Ids of every match, so "play all" and "select all" work beyond the loaded page."""
        collection = request.args.get("collection")
        sort = request.args.get("sort", "date")
        args = [request.args.get("filter", "all"), request.args.get("search", ""), sort if sort in LIBRARY_SORTS else "date"]
        if collection is not None:
            args.append(collection)
        return jsonify({"ok": True, "ids": [row["id"] for row in library.list(*args)]})

    @app.post("/api/library/resolve")
    def api_library_resolve():
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "items": library.resolve(_ids(payload))})

    @app.get("/api/library/collections")
    def api_collections():
        return jsonify({"ok": True, "collections": library.collections()})

    @app.get("/api/stats")
    def api_stats():
        return jsonify({"ok": True, **library.stats(), "active": manager.active_count()})

    @app.post("/api/library/reindex")
    def api_reindex():
        return jsonify({"ok": True, **library.sync(), **library.stats()})

    @app.post("/api/library/favorite")
    def api_favorite():
        payload = request.get_json(silent=True) or {}
        changed = library.set_favorite(_ids(payload), bool(payload.get("favorite", True)))
        return jsonify({"ok": True, "changed": changed})

    @app.delete("/api/library")
    def api_delete_media():
        payload = request.get_json(silent=True) or {}
        if payload.get("ids"):
            records = [record for record in (library.by_id(media_id) for media_id in _ids(payload)) if record]
            if not records:
                return jsonify({"ok": False, "error": "Файлы не найдены"}), 404
        else:
            filename = str(payload.get("filename") or "")
            if not filename:
                raise ValueError("Не указан файл")
            path = _inside(download_root, filename)
            if not path.is_file():
                return jsonify({"ok": False, "error": "Файл не найден"}), 404
            records = [library.by_filename(filename) or {"filename": filename}]
        deleted, failed = delete_media(records)
        deleted_ids = [record["id"] for record in deleted if record.get("id")]
        playlists.forget_media(deleted_ids)
        bridge.outbox_remove(deleted_ids)
        if failed and not deleted:
            raise ValueError(f"Файл занят другой программой: {Path(failed[0]).name}")
        return jsonify({"ok": True, "deleted": len(deleted), "deleted_ids": deleted_ids, "failed": failed})

    @app.post("/api/open-folder")
    def api_open_folder():
        payload = request.get_json(silent=True) or {}
        filename = str(payload.get("filename") or "")
        if payload.get("id"):
            record = library.by_id(str(payload["id"]))
            if not record:
                raise KeyError("Файл не найден")
            filename = record["filename"]
        target = _inside(download_root, filename) if filename else download_root
        open_in_file_manager(target, select=bool(filename))
        return jsonify({"ok": True})

    # -- playlists -------------------------------------------------------
    def playlist_view(playlist: dict[str, Any], with_items: bool = False) -> dict[str, Any]:
        rows = library.resolve(playlist["items"])
        view = {
            "id": playlist["id"],
            "name": playlist["name"],
            "ids": [row["id"] for row in rows],
            "count": len(rows),
            "duration": round(sum(float(row.get("duration") or 0) for row in rows)),
            "size_text": format_bytes(sum(int(row.get("size") or 0) for row in rows)),
            "thumbnails": [row["thumbnail"] for row in rows if row.get("thumbnail")][:4],
            "created_at": playlist["created_at"],
            "updated_at": playlist["updated_at"],
        }
        if with_items:
            view["items"] = rows
        return view

    @app.get("/api/playlists")
    def api_playlists():
        return jsonify({"ok": True, "playlists": [playlist_view(row) for row in playlists.list()]})

    @app.post("/api/playlists")
    def api_create_playlist():
        payload = request.get_json(silent=True) or {}
        ids = [str(value) for value in payload.get("ids") or []]
        playlist = playlists.create(payload.get("name"), [row["id"] for row in library.resolve(ids)])
        return jsonify({"ok": True, "playlist": playlist_view(playlist)})

    @app.get("/api/playlists/<playlist_id>")
    def api_playlist(playlist_id: str):
        return jsonify({"ok": True, "playlist": playlist_view(playlists.get(playlist_id), with_items=True)})

    @app.patch("/api/playlists/<playlist_id>")
    def api_update_playlist(playlist_id: str):
        payload = request.get_json(silent=True) or {}
        if "name" in payload:
            playlists.rename(playlist_id, payload["name"])
        if "ids" in payload:
            playlists.reorder(playlist_id, [str(value) for value in payload["ids"] or []])
        return jsonify({"ok": True, "playlist": playlist_view(playlists.get(playlist_id))})

    @app.delete("/api/playlists/<playlist_id>")
    def api_delete_playlist(playlist_id: str):
        playlists.delete(playlist_id)
        return jsonify({"ok": True})

    @app.post("/api/playlists/<playlist_id>/items")
    def api_playlist_add(playlist_id: str):
        payload = request.get_json(silent=True) or {}
        known = [row["id"] for row in library.resolve(_ids(payload))]
        return jsonify({"ok": True, "added": playlists.add_items(playlist_id, known)})

    @app.delete("/api/playlists/<playlist_id>/items")
    def api_playlist_remove(playlist_id: str):
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "removed": playlists.remove_items(playlist_id, _ids(payload))})

    # -- phone bridge ----------------------------------------------------
    def bridge_status() -> dict[str, Any]:
        return {"ok": True, **bridge.status(), "outbox": bridge.outbox()}

    @app.get("/api/bridge")
    def api_bridge():
        return jsonify(bridge_status())

    @app.post("/api/bridge")
    def api_bridge_update():
        payload = request.get_json(silent=True) or {}
        if payload.get("address"):
            bridge.set_address(str(payload["address"]))
        if "enabled" in payload:
            if payload["enabled"]:
                try:
                    bridge.start()
                except RuntimeError as exc:
                    return jsonify({"ok": False, "error": str(exc)}), 409
            else:
                bridge.stop()
        return jsonify(bridge_status())

    @app.post("/api/bridge/token")
    def api_bridge_token():
        bridge.rotate_token()
        return jsonify(bridge_status())

    @app.get("/api/bridge/qr.svg")
    def api_bridge_qr():
        import io

        try:
            import segno
        except ImportError:
            return jsonify({"ok": False, "error": "Не установлен пакет segno: выполните pip install -r requirements.txt"}), 503

        # A standalone SVG (with xmlns) so it renders inside <img>; svg_inline() omits it.
        buffer = io.BytesIO()
        segno.make(bridge.connect_url(), error="m").save(
            buffer, kind="svg", scale=8, border=2, dark="#0b0f16", light="#ffffff", xmldecl=False, omitsize=True,
        )
        return Response(buffer.getvalue(), mimetype="image/svg+xml")

    @app.post("/api/outbox")
    def api_outbox_add():
        payload = request.get_json(silent=True) or {}
        added = bridge.outbox_add(_ids(payload))
        return jsonify({**bridge_status(), "added": added})

    @app.delete("/api/outbox")
    def api_outbox_remove():
        payload = request.get_json(silent=True) or {}
        if payload.get("all") or payload.get("delivered"):
            bridge.outbox_clear(delivered_only=bool(payload.get("delivered")))
        else:
            bridge.outbox_remove(_ids(payload))
        return jsonify(bridge_status())

    # -- system ----------------------------------------------------------
    @app.get("/api/system")
    def api_system():
        try:
            usage = shutil.disk_usage(download_root)
            disk = {"disk_total": usage.total, "disk_free": usage.free,
                    "disk_free_text": format_bytes(usage.free), "disk_total_text": format_bytes(usage.total)}
        except OSError:
            disk = {"disk_total": 0, "disk_free": 0, "disk_free_text": "—", "disk_total_text": "—"}
        return jsonify({
            "ok": True,
            "version": __version__,
            "yt_dlp_version": yt_dlp.version.__version__,
            "ffmpeg": bool(manager.config.ffmpeg_dir),
            "node": _node_available(),
            "download_root": str(download_root),
            "workers": manager.config.workers,
            "formats": sorted(SUPPORTED_FORMATS),
            "audio_formats": sorted(AUDIO_FORMATS),
            "video_formats": sorted(VIDEO_FORMATS),
            **disk,
        })

    @app.post("/api/shutdown")
    def api_shutdown():
        """Stop the local server; the desktop launcher runs it without a console window."""
        payload = request.get_json(silent=True) or {}
        if payload.get("confirm") is not True:
            raise ValueError("Нужно подтверждение")
        if app.config.get("TESTING"):
            return jsonify({"ok": True, "testing": True})

        def stop() -> None:
            # The bridge is not stopped explicitly: that would persist "disabled"
            # and it should come back on the next launch if the user left it on.
            manager.cancel_all()
            os._exit(0)

        threading.Timer(0.4, stop).start()
        return jsonify({"ok": True})

    # -- files -----------------------------------------------------------
    @app.get("/media/<path:filename>")
    def media_file(filename: str):
        _inside(download_root, filename)
        return send_from_directory(download_root, filename, as_attachment=True)

    @app.get("/stream/<path:filename>")
    def stream_file(filename: str):
        """Inline file with HTTP Range support, used by the built-in player."""
        _inside(download_root, filename)
        return send_from_directory(download_root, filename, conditional=True, max_age=0)

    @app.get("/thumbs/<path:filename>")
    def thumbnail_file(filename: str):
        _inside(thumbnails_root, filename)
        return send_from_directory(thumbnails_root, filename, max_age=86400)

    return app
