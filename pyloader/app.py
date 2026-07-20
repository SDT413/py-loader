from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yt_dlp
from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .core import (
    AUDIO_FORMATS,
    BROWSER_NAMES,
    SUPPORTED_FORMATS,
    VIDEO_FORMATS,
    DownloadConfig,
    DownloadManager,
    MediaLibrary,
    find_ffmpeg,
    inspect_urls,
    open_in_file_manager,
)


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Недопустимый путь")
    return candidate


def create_app(config: dict[str, Any] | None = None) -> Flask:
    base_dir = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(base_dir / "templates"),
        static_folder=str(base_dir / "static"),
    )
    app.config.update(
        JSON_AS_ASCII=False,
        DOWNLOAD_ROOT=str(base_dir / "downloads"),
        THUMBNAILS_ROOT=str(base_dir / "downloads" / "thumbnails"),
        MAX_WORKERS=int(os.environ.get("PYLOADER_WORKERS", "3")),
        TESTING=False,
    )
    if config:
        app.config.update(config)

    download_root = Path(app.config["DOWNLOAD_ROOT"]).resolve()
    thumbnails_root = Path(app.config["THUMBNAILS_ROOT"]).resolve()
    download_root.mkdir(parents=True, exist_ok=True)
    thumbnails_root.mkdir(parents=True, exist_ok=True)
    library = MediaLibrary(download_root, thumbnails_root)
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

    @app.after_request
    def no_cache_api(response):
        # The app is local and updated in place. Prevent stale HTML/JS combinations
        # after an upgrade; they are more harmful here than a tiny cache saving.
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    @app.errorhandler(Exception)
    def handle_error(error: Exception):
        if isinstance(error, HTTPException):
            status = error.code or 500
            message = error.description
        elif isinstance(error, (ValueError, KeyError)):
            status = 400
            message = str(error).strip("'")
        else:
            status = 500
            message = str(error) or "Внутренняя ошибка"
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": message}), status
        return f"Ошибка: {message}", status

    @app.get("/")
    def index():
        return render_template("index.html")

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
        return jsonify({"ok": True, "jobs": manager.jobs.snapshot(), "active": manager.active_count()})

    @app.post("/api/jobs/<job_id>/cancel")
    def api_cancel_job(job_id: str):
        if not manager.jobs.cancel(job_id):
            return jsonify({"ok": False, "error": "Задача не найдена"}), 404
        return jsonify({"ok": True})

    @app.post("/api/jobs/<job_id>/retry")
    def api_retry_job(job_id: str):
        return jsonify({"ok": True, **manager.retry(job_id)})

    @app.delete("/api/jobs/completed")
    def api_clear_jobs():
        return jsonify({"ok": True, "cleared": manager.jobs.clear_terminal()})

    @app.get("/api/library")
    def api_library():
        media_filter = request.args.get("filter", "all")
        search = request.args.get("search", "")
        sort = request.args.get("sort", "date")
        limit = max(1, min(request.args.get("limit", 120, type=int), 500))
        offset = max(0, request.args.get("offset", 0, type=int))
        rows = library.list(media_filter, search, sort)
        return jsonify({"ok": True, "items": rows[offset:offset + limit], "total": len(rows)})

    @app.get("/api/stats")
    def api_stats():
        return jsonify({"ok": True, **library.stats(), "active": manager.active_count()})

    @app.post("/api/library/reindex")
    def api_reindex():
        return jsonify({"ok": True, **library.sync(), **library.stats()})

    @app.delete("/api/library")
    def api_delete_media():
        payload = request.get_json(silent=True) or {}
        filename = str(payload.get("filename") or "")
        if not filename:
            raise ValueError("Не указан файл")
        path = _inside(download_root, filename)
        if not path.is_file():
            return jsonify({"ok": False, "error": "Файл не найден"}), 404
        record = library.by_filename(filename)
        path.unlink()
        removed = library.remove(filename)
        thumb_name = (record or removed or {}).get("thumbnail")
        thumbnail_still_used = thumb_name and any(row.get("thumbnail") == thumb_name for row in library.list())
        if thumb_name and not thumbnail_still_used:
            thumbnail = _inside(thumbnails_root, thumb_name)
            if thumbnail.is_file():
                thumbnail.unlink()
        return jsonify({"ok": True})

    @app.post("/api/open-folder")
    def api_open_folder():
        payload = request.get_json(silent=True) or {}
        filename = str(payload.get("filename") or "")
        target = _inside(download_root, filename) if filename else download_root
        open_in_file_manager(target, select=bool(filename))
        return jsonify({"ok": True})

    @app.get("/api/system")
    def api_system():
        return jsonify({
            "ok": True,
            "yt_dlp_version": yt_dlp.version.__version__,
            "ffmpeg": bool(manager.config.ffmpeg_dir),
            "node": bool(os.environ.get("PATH") and any(
                (Path(part) / ("node.exe" if os.name == "nt" else "node")).exists()
                for part in os.environ["PATH"].split(os.pathsep)
                if part
            )),
            "download_root": str(download_root),
            "workers": manager.config.workers,
            "formats": sorted(SUPPORTED_FORMATS),
            "audio_formats": sorted(AUDIO_FORMATS),
            "video_formats": sorted(VIDEO_FORMATS),
        })

    @app.get("/media/<path:filename>")
    def media_file(filename: str):
        _inside(download_root, filename)
        return send_from_directory(download_root, filename, as_attachment=True)

    @app.get("/thumbs/<path:filename>")
    def thumbnail_file(filename: str):
        _inside(thumbnails_root, filename)
        return send_from_directory(thumbnails_root, filename)

    return app
