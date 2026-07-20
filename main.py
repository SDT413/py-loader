from __future__ import annotations

import os
from pathlib import Path

from pyloader import create_app


app = create_app()


if __name__ == "__main__":
    download_root = Path(app.config["DOWNLOAD_ROOT"])
    manager = app.extensions["pyloader_manager"]
    print("=" * 58)
    print("PyLoader · local YouTube media studio")
    print("=" * 58)
    print(f"Downloads : {download_root}")
    print(f"FFmpeg   : {'OK' if manager.config.ffmpeg_dir else 'NOT FOUND'}")
    print(f"Workers  : {manager.config.workers}")
    print("Browser  : http://127.0.0.1:5000")
    print("=" * 58)
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PYLOADER_PORT", "5000")),
        debug=os.environ.get("PYLOADER_DEBUG") == "1",
        use_reloader=False,
        threaded=True,
    )
