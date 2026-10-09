from __future__ import annotations

import logging
import os
from pathlib import Path

from pyloader import __version__, create_app


app = create_app()


if __name__ == "__main__":
    download_root = Path(app.config["DOWNLOAD_ROOT"])
    manager = app.extensions["pyloader_manager"]
    bridge = app.extensions["pyloader_bridge"]
    debug = os.environ.get("PYLOADER_DEBUG") == "1"
    if not debug:
        # The UI polls the queue every second; per-request lines would flood the console and log.
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
    bridge.autostart()
    port = int(os.environ.get("PYLOADER_PORT", "5000"))
    print("=" * 58)
    print(f"PyLoader {__version__} · local media studio")
    print("=" * 58)
    print(f"Downloads : {download_root}")
    print(f"FFmpeg    : {'OK' if manager.config.ffmpeg_dir else 'NOT FOUND'}")
    print(f"Workers   : {manager.config.workers}")
    print(f"Phone     : {bridge.base_url() if bridge.running else 'off (enable in the «На телефон» tab)'}")
    print(f"Browser   : http://127.0.0.1:{port}")
    print("=" * 58)
    app.run(
        host="127.0.0.1",
        port=port,
        debug=debug,
        use_reloader=False,
        threaded=True,
    )
