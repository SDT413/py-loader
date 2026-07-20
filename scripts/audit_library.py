from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyloader.core import MediaLibrary, inspect_urls  # noqa: E402


def normalize(value: str | None) -> str:
    return "".join(
        char.casefold()
        for char in unicodedata.normalize("NFKC", value or "")
        if char.isalnum()
    )


def has_audio_stream(path: Path, ffprobe: Path) -> bool:
    result = subprocess.run(
        [str(ffprobe), "-v", "quiet", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True,
        check=False,
    )
    try:
        streams = json.loads(result.stdout.decode("utf-8", "replace")).get("streams", [])
    except (ValueError, UnicodeDecodeError):
        return False
    return any(stream.get("codec_type") == "audio" for stream in streams)


def main() -> int:
    parser = argparse.ArgumentParser(description="Сверка локальной медиатеки с плейлистами")
    parser.add_argument("urls", nargs="+", help="Ссылки на видео, плейлисты или каналы")
    parser.add_argument("--downloads", default=str(ROOT / "downloads"))
    parser.add_argument("--cookies-browser", default="none")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--repair-untagged",
        action="store_true",
        help="Записать найденные по точному названию YouTube ID в локальный индекс",
    )
    args = parser.parse_args()

    download_root = Path(args.downloads).resolve()
    thumbnails = download_root / "thumbnails"
    library = MediaLibrary(download_root, thumbnails)
    inspected = inspect_urls(args.urls, args.cookies_browser, args.proxy)
    expected = {item.get("id"): item for item in inspected["items"] if item.get("id")}
    records = library.list()

    by_id: dict[str, list[dict]] = defaultdict(list)
    untagged = []
    for record in records:
        if record.get("video_id"):
            by_id[record["video_id"]].append(record)
        else:
            untagged.append(record)

    expected_by_title = {normalize(item.get("title")): video_id for video_id, item in expected.items() if item.get("title")}
    matched_untagged = {}
    for record in untagged:
        video_id = expected_by_title.get(normalize(record.get("title")))
        if video_id:
            matched_untagged[video_id] = record["filename"]
            if args.repair_untagged:
                library.associate(record["filename"], video_id, expected[video_id].get("title"))

    present = set(by_id) | set(matched_untagged)
    missing = sorted(set(expected) - present)
    duplicate_ids = {video_id: rows for video_id, rows in by_id.items() if len(rows) > 1}

    ffprobe = ROOT / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    audio_paths = [download_root / row["filename"] for row in records if row.get("media_type") == "audio"]
    invalid_audio: list[str] = []
    if ffprobe.exists():
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = pool.map(lambda path: (path, has_audio_stream(path, ffprobe)), audio_paths)
            invalid_audio = [str(path.relative_to(download_root)) for path, valid in results if not valid]

    report = {
        "raw_playlist_entries": inspected["raw_count"],
        "expected_unique_ids": len(expected),
        "local_media_files": len(records),
        "present_unique_ids": len(present & set(expected)),
        "duplicates": {key: [row["filename"] for row in value] for key, value in duplicate_ids.items()},
        "matched_untagged": matched_untagged,
        "missing": [{"id": video_id, "title": expected[video_id].get("title")} for video_id in missing],
        "invalid_audio": invalid_audio,
        "source_errors": inspected["errors"],
    }

    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Исходных позиций : {report['raw_playlist_entries']}")
        print(f"Уникальных ID    : {report['expected_unique_ids']}")
        print(f"Найдено локально : {report['present_unique_ids']}")
        print(f"Дубликатов ID    : {len(report['duplicates'])}")
        print(f"Битых аудиофайлов: {len(report['invalid_audio'])}")
        print(f"Отсутствует      : {len(report['missing'])}")
        for item in report["missing"]:
            print(f"  {item['id']} | {item['title']}")
        for filename in report["invalid_audio"]:
            print(f"  INVALID | {filename}")

    return 1 if invalid_audio or duplicate_ids else 0


if __name__ == "__main__":
    raise SystemExit(main())
