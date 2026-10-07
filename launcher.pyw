"""Desktop launcher for PyLoader.

Starts the local server in the background (no console window) unless it is already
running, then opens PyLoader as a standalone app window (Edge/Chrome --app mode) or,
as a fallback, in the default browser. The desktop shortcut created by
scripts/create_shortcut.py points here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PYLOADER_PORT", "5000"))
URL = f"http://127.0.0.1:{PORT}/"
LOG = ROOT / "downloads" / ".pyloader" / "server.log"


def server_running() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}api/system", timeout=1.5) as response:
            return response.status == 200 and b"yt_dlp_version" in response.read()
    except OSError:
        return False


def console_python() -> str:
    """python.exe next to pythonw.exe, so the server gets working stdout/stderr."""
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        candidate = executable.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return str(executable)


def start_server() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")  # noqa: SIM115 - handed to the child process
    log.write(f"\n=== PyLoader launcher {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    log.flush()
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    subprocess.Popen(
        [console_python(), str(ROOT / "main.py")],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=os.name != "nt",
        env=environment,
    )


def app_browser() -> str | None:
    """Edge ships with Windows 10/11; Chrome or Chromium are used when present."""
    candidates: list[str] = []
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                ]
    elif sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        candidates += [shutil.which(name) or "" for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    return next((path for path in candidates if path and os.path.exists(path)), None)


def open_window() -> None:
    if os.environ.get("PYLOADER_BROWSER") == "default":
        webbrowser.open(URL)
        return
    browser = app_browser()
    if browser:
        try:
            subprocess.Popen([browser, f"--app={URL}", "--window-size=1360,880"])
            return
        except OSError:
            pass
    webbrowser.open(URL)


def notify_failure() -> None:
    message = f"PyLoader не запустился за 40 секунд.\nПодробности в журнале:\n{LOG}"
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "PyLoader", 0x10)
    else:
        print(message, file=sys.stderr)


def main() -> int:
    if not server_running():
        start_server()
        deadline = time.monotonic() + 40
        while not server_running():
            if time.monotonic() > deadline:
                notify_failure()
                return 1
            time.sleep(0.4)
    open_window()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
