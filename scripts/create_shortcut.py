"""Create a "PyLoader" shortcut on the desktop (and in the Start menu on Windows).

Usage:  .venv\\Scripts\\python.exe scripts\\create_shortcut.py [--no-start-menu]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON_ICO = ROOT / "static" / "icons" / "pyloader.ico"
ICON_PNG = ROOT / "static" / "icons" / "icon-512.png"
LAUNCHER = ROOT / "launcher.pyw"


def windowless_python() -> Path:
    executable = Path(sys.executable)
    candidate = executable.with_name("pythonw.exe")
    return candidate if candidate.exists() else executable


def ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_windows(start_menu: bool) -> list[str]:
    # WScript.Shell resolves the real Desktop folder, including OneDrive-redirected ones.
    script = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$shell = New-Object -ComObject WScript.Shell
$targets = @([Environment]::GetFolderPath('Desktop'))
if (${'true' if start_menu else 'false'}) {{ $targets += [Environment]::GetFolderPath('Programs') }}
foreach ($folder in $targets) {{
    $link = $shell.CreateShortcut((Join-Path $folder 'PyLoader.lnk'))
    $link.TargetPath = {ps_quote(windowless_python())}
    $link.Arguments = '"' + {ps_quote(LAUNCHER)} + '"'
    $link.WorkingDirectory = {ps_quote(ROOT)}
    $link.IconLocation = {ps_quote(ICON_ICO)} + ',0'
    $link.Description = 'PyLoader - скачивание и медиатека'
    $link.Save()
    Write-Output (Join-Path $folder 'PyLoader.lnk')
}}
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def create_linux() -> list[str]:
    entry = "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=PyLoader",
        "Comment=Скачивание и медиатека",
        f'Exec="{sys.executable}" "{LAUNCHER}"',
        f"Path={ROOT}",
        f"Icon={ICON_PNG}",
        "Terminal=false",
        "Categories=AudioVideo;Network;",
        "",
    ])
    created = []
    folders = [Path(os.environ.get("XDG_DESKTOP_DIR", Path.home() / "Desktop")), Path.home() / ".local" / "share" / "applications"]
    for folder in folders:
        if not folder.exists():
            continue
        path = folder / "pyloader.desktop"
        path.write_text(entry, encoding="utf-8")
        path.chmod(0o755)
        created.append(str(path))
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Ярлык PyLoader на рабочем столе")
    parser.add_argument("--no-start-menu", action="store_true", help="Не добавлять ярлык в меню «Пуск»")
    args = parser.parse_args()
    if os.name == "nt":
        created = create_windows(start_menu=not args.no_start_menu)
    elif sys.platform == "darwin":
        print("На macOS перетащите launcher.pyw в Dock или создайте Automator-приложение, запускающее его.")
        return 0
    else:
        created = create_linux()
    for path in created:
        print(f"Ярлык создан: {path}")
    if not created:
        print("Папка рабочего стола не найдена — ярлык не создан.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
