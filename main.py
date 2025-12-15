import os
import uuid
import shutil
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template_string, request, jsonify, send_from_directory
import yt_dlp

# --- КОНФИГУРАЦИЯ ---
app = Flask(__name__)
DOWNLOAD_FOLDER = "downloads"
THUMBNAILS_FOLDER = "downloads/thumbnails"
MAX_CONCURRENT_DOWNLOADS = 3

# Глобальное хранилище состояния загрузок
download_status = {}
active_urls = set()
cancelled_tasks = set()  # Для отмены загрузок
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS)

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

if not os.path.exists(THUMBNAILS_FOLDER):
    os.makedirs(THUMBNAILS_FOLDER)


def is_playlist_url(url):
    """Проверяет является ли URL плейлистом"""
    return 'list=' in url or '/playlist' in url


def get_playlist_info(url):
    """Получает информацию о плейлисте"""
    try:
        ydl_opts = {
            'quiet': True,
            'extract_flat': 'in_playlist',  # Извлекаем плейлист плоско
            'no_warnings': True,
            'ignoreerrors': True,
            # Важно! Говорим что хотим весь плейлист, а не одно видео
            'noplaylist': False,
            'yes_playlist': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            print(f"Playlist debug - type: {info.get('_type')}, entries: {len(info.get('entries', []))}")

            if info.get('_type') == 'playlist' or 'entries' in info:
                entries = list(info.get('entries', []))
                # Фильтруем None записи и недоступные видео
                valid_entries = [e for e in entries if e and e.get('id')]

                return {
                    'is_playlist': True,
                    'title': info.get('title', 'Плейлист'),
                    'count': len(valid_entries),
                    'uploader': info.get('uploader', info.get('channel', 'Неизвестно')),
                    'videos': [
                        {
                            'url': f"https://www.youtube.com/watch?v={e.get('id')}",
                            'title': e.get('title', 'Без названия'),
                            'duration': e.get('duration', 0),
                            'thumbnail': e.get('thumbnail')
                        }
                        for e in valid_entries
                    ]
                }
            else:
                # Это одиночное видео, не плейлист
                return {'is_playlist': False}
    except Exception as e:
        print(f"Playlist info error: {e}")
        return {'is_playlist': False, 'error': str(e)}


def format_file_size(size_bytes):
    """Форматирует размер файла в человекочитаемый формат"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_folder_stats():
    """Возвращает статистику папки загрузок"""
    total_size = 0
    mp3_count = 0
    mp4_count = 0

    if os.path.exists(DOWNLOAD_FOLDER):
        for filename in os.listdir(DOWNLOAD_FOLDER):
            filepath = os.path.join(DOWNLOAD_FOLDER, filename)
            if os.path.isfile(filepath):
                ext = os.path.splitext(filename)[1].lower()
                if ext == '.mp3':
                    mp3_count += 1
                    total_size += os.path.getsize(filepath)
                elif ext == '.mp4':
                    mp4_count += 1
                    total_size += os.path.getsize(filepath)

    return {
        'total_size': format_file_size(total_size),
        'total_size_bytes': total_size,
        'mp3_count': mp3_count,
        'mp4_count': mp4_count,
        'total_count': mp3_count + mp4_count
    }


def get_history_files(filter_format='all', search='', sort_by='date'):
    """Получает список скачанных файлов из папки downloads"""
    files = []
    if not os.path.exists(DOWNLOAD_FOLDER):
        return files

    for filename in os.listdir(DOWNLOAD_FOLDER):
        filepath = os.path.join(DOWNLOAD_FOLDER, filename)
        if not os.path.isfile(filepath):
            continue

        ext = os.path.splitext(filename)[1].lower()

        # Пропускаем временные файлы
        if '.temp' in filename.lower():
            continue

        # Фильтрация по формату
        if filter_format == 'mp3' and ext != '.mp3':
            continue
        if filter_format == 'mp4' and ext != '.mp4':
            continue
        if ext not in ['.mp3', '.mp4']:
            continue

        name_without_ext = os.path.splitext(filename)[0]

        # Поиск
        if search and search.lower() not in name_without_ext.lower():
            continue

        # Поиск обложки
        thumbnail = None
        for thumb_ext in ['.jpg', '.jpeg', '.png', '.webp']:
            thumb_path = os.path.join(THUMBNAILS_FOLDER, name_without_ext + thumb_ext)
            if os.path.exists(thumb_path):
                encoded_name = urllib.parse.quote(name_without_ext + thumb_ext)
                thumbnail = f"/thumbnails/{encoded_name}"
                break

        # Информация о файле
        stat = os.stat(filepath)
        files.append({
            'filename': filename,
            'title': name_without_ext,
            'format': ext[1:],
            'size': format_file_size(stat.st_size),
            'size_bytes': stat.st_size,
            'modified': stat.st_mtime,
            'thumbnail': thumbnail
        })

    # Сортировка
    if sort_by == 'date':
        files.sort(key=lambda x: x['modified'], reverse=True)
    elif sort_by == 'name':
        files.sort(key=lambda x: x['title'].lower())
    elif sort_by == 'size':
        files.sort(key=lambda x: x['size_bytes'], reverse=True)

    return files


# --- HTML ШАБЛОН ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PyLoader - Video Downloader</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        :root {
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #21262d;
            --bg-hover: #30363d;
            --text-primary: #f0f6fc;
            --text-secondary: #8b949e;
            --accent: #238636;
            --accent-hover: #2ea043;
            --danger: #da3633;
            --danger-hover: #f85149;
            --warning: #d29922;
            --info: #58a6ff;
            --border: #30363d;
            --shadow: rgba(0,0,0,0.3);
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
        }
        
        /* === HEADER === */
        .header {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            padding: 15px 30px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 1.5em;
            font-weight: 700;
            color: var(--accent);
        }
        
        .logo-icon {
            width: 40px;
            height: 40px;
            background: linear-gradient(135deg, var(--accent), #1a7f37);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
        }
        
        .header-stats {
            display: flex;
            gap: 25px;
        }
        
        .stat-item {
            text-align: center;
        }
        
        .stat-value {
            font-size: 1.3em;
            font-weight: 700;
            color: var(--accent);
        }
        
        .stat-label {
            font-size: 0.75em;
            color: var(--text-secondary);
            text-transform: uppercase;
        }
        
        /* === MAIN LAYOUT === */
        .main-container {
            display: grid;
            grid-template-columns: 1fr 350px;
            gap: 0;
            min-height: calc(100vh - 71px);
        }
        
        /* === LEFT PANEL - Downloads === */
        .downloads-panel {
            padding: 25px;
            overflow-y: auto;
        }
        
        /* === INPUT SECTION === */
        .input-section {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 25px;
        }
        
        .input-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        
        .input-title {
            font-size: 1.1em;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .url-input {
            width: 100%;
            height: 100px;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text-primary);
            padding: 12px;
            font-family: 'Consolas', monospace;
            font-size: 0.9em;
            resize: vertical;
            transition: border-color 0.2s;
        }
        
        .url-input:focus {
            outline: none;
            border-color: var(--accent);
        }
        
        .url-input::placeholder {
            color: var(--text-secondary);
        }
        
        .input-controls {
            display: flex;
            gap: 12px;
            margin-top: 15px;
        }
        
        /* === PLAYLIST PREVIEW === */
        .playlist-preview {
            background: var(--bg-tertiary);
            border: 1px solid var(--accent);
            border-radius: 10px;
            padding: 15px;
            margin-top: 15px;
            animation: slideIn 0.3s ease;
        }
        
        .playlist-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
        }
        
        .playlist-icon {
            width: 50px;
            height: 50px;
            background: linear-gradient(135deg, var(--accent), #1a7f37);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
        }
        
        .playlist-info h3 {
            font-size: 1em;
            margin-bottom: 4px;
        }
        
        .playlist-info p {
            font-size: 0.85em;
            color: var(--text-secondary);
        }
        
        .playlist-actions {
            display: flex;
            gap: 10px;
            margin-top: 12px;
        }
        
        .playlist-videos {
            max-height: 200px;
            overflow-y: auto;
            margin-top: 12px;
            border-top: 1px solid var(--border);
            padding-top: 12px;
        }
        
        .playlist-video-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px;
            border-radius: 6px;
            margin-bottom: 5px;
            background: var(--bg-secondary);
        }
        
        .playlist-video-item:hover {
            background: var(--bg-hover);
        }
        
        .playlist-video-num {
            width: 24px;
            height: 24px;
            background: var(--bg-tertiary);
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.75em;
            color: var(--text-secondary);
            flex-shrink: 0;
        }
        
        .playlist-video-title {
            flex-grow: 1;
            font-size: 0.85em;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .playlist-video-duration {
            font-size: 0.75em;
            color: var(--text-secondary);
            flex-shrink: 0;
        }
        
        .playlist-loading {
            text-align: center;
            padding: 20px;
            color: var(--text-secondary);
        }
        
        .spinner {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid var(--bg-hover);
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-right: 10px;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .format-select {
            padding: 12px 20px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text-primary);
            font-weight: 500;
            cursor: pointer;
            min-width: 160px;
        }
        
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s;
        }
        
        .btn-primary {
            background: var(--accent);
            color: white;
            flex-grow: 1;
        }
        
        .btn-primary:hover { background: var(--accent-hover); }
        
        .btn-secondary {
            background: var(--bg-tertiary);
            color: var(--text-primary);
            border: 1px solid var(--border);
        }
        
        .btn-secondary:hover { background: var(--bg-hover); }
        
        .btn-danger {
            background: var(--danger);
            color: white;
        }
        
        .btn-danger:hover { background: var(--danger-hover); }
        
        .btn-sm {
            padding: 6px 12px;
            font-size: 0.85em;
        }
        
        .btn-icon {
            padding: 8px;
            min-width: auto;
        }
        
        /* === ACTIVE DOWNLOADS === */
        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        
        .section-title {
            font-size: 1.1em;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .badge {
            background: var(--accent);
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.75em;
            font-weight: 600;
        }
        
        .download-card {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 15px;
            animation: slideIn 0.3s ease;
        }
        
        @keyframes slideIn {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .download-icon {
            width: 50px;
            height: 50px;
            background: var(--bg-tertiary);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            flex-shrink: 0;
        }
        
        .download-info {
            flex-grow: 1;
            min-width: 0;
        }
        
        .download-title {
            font-weight: 600;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-bottom: 4px;
        }
        
        .download-url {
            font-size: 0.8em;
            color: var(--text-secondary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .download-progress {
            margin-top: 8px;
        }
        
        .progress-bar {
            height: 6px;
            background: var(--bg-tertiary);
            border-radius: 3px;
            overflow: hidden;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--accent), #40c463);
            transition: width 0.3s ease;
            border-radius: 3px;
        }
        
        .progress-fill.error { background: var(--danger); }
        .progress-fill.warning { background: var(--warning); }
        
        .download-status {
            display: flex;
            justify-content: space-between;
            margin-top: 5px;
            font-size: 0.8em;
            color: var(--text-secondary);
        }
        
        .status-text {
            display: flex;
            align-items: center;
            gap: 5px;
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--accent);
            animation: pulse 1.5s infinite;
        }
        
        .status-dot.finished { background: var(--accent); animation: none; }
        .status-dot.error { background: var(--danger); animation: none; }
        .status-dot.warning { background: var(--warning); animation: none; }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }
        
        .download-actions {
            display: flex;
            gap: 5px;
        }
        
        /* === RIGHT PANEL - History === */
        .history-panel {
            background: var(--bg-secondary);
            border-left: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            max-height: calc(100vh - 71px);
        }
        
        .history-header {
            padding: 20px;
            border-bottom: 1px solid var(--border);
        }
        
        .history-title {
            font-size: 1.1em;
            font-weight: 600;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .search-box {
            position: relative;
        }
        
        .search-input {
            width: 100%;
            padding: 10px 12px 10px 36px;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text-primary);
            font-size: 0.9em;
        }
        
        .search-input:focus {
            outline: none;
            border-color: var(--accent);
        }
        
        .search-icon {
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-secondary);
        }
        
        .history-filters {
            display: flex;
            gap: 8px;
            margin-top: 12px;
        }
        
        .filter-btn {
            padding: 6px 12px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            border-radius: 6px;
            color: var(--text-secondary);
            font-size: 0.85em;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .filter-btn:hover { background: var(--bg-hover); }
        .filter-btn.active {
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }
        
        .history-list {
            flex-grow: 1;
            overflow-y: auto;
            padding: 15px;
        }
        
        .history-item {
            background: var(--bg-tertiary);
            border-radius: 10px;
            padding: 12px;
            margin-bottom: 10px;
            display: flex;
            gap: 12px;
            transition: transform 0.2s, box-shadow 0.2s;
            cursor: default;
        }
        
        .history-item:hover {
            transform: translateX(3px);
            box-shadow: -3px 0 0 var(--accent);
        }
        
        .history-thumb {
            width: 80px;
            height: 60px;
            border-radius: 6px;
            object-fit: cover;
            background: var(--bg-hover);
            flex-shrink: 0;
        }
        
        .history-thumb-placeholder {
            width: 80px;
            height: 60px;
            border-radius: 6px;
            background: linear-gradient(135deg, var(--bg-hover), var(--bg-tertiary));
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            flex-shrink: 0;
        }
        
        .history-info {
            flex-grow: 1;
            min-width: 0;
        }
        
        .history-title-text {
            font-weight: 500;
            font-size: 0.9em;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-bottom: 6px;
        }
        
        .history-meta {
            display: flex;
            gap: 10px;
            font-size: 0.75em;
            color: var(--text-secondary);
            flex-wrap: wrap;
        }
        
        .history-meta span {
            display: flex;
            align-items: center;
            gap: 3px;
        }
        
        .history-actions {
            display: flex;
            flex-direction: column;
            gap: 5px;
        }
        
        .history-empty {
            text-align: center;
            padding: 40px 20px;
            color: var(--text-secondary);
        }
        
        .history-empty-icon {
            font-size: 48px;
            margin-bottom: 10px;
            opacity: 0.5;
        }
        
        /* === MODAL === */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.7);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            opacity: 0;
            visibility: hidden;
            transition: all 0.2s;
        }
        
        .modal-overlay.active {
            opacity: 1;
            visibility: visible;
        }
        
        .modal {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 25px;
            max-width: 400px;
            width: 90%;
            transform: scale(0.9);
            transition: transform 0.2s;
        }
        
        .modal-overlay.active .modal {
            transform: scale(1);
        }
        
        .modal-title {
            font-size: 1.2em;
            font-weight: 600;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .modal-text {
            color: var(--text-secondary);
            margin-bottom: 20px;
            line-height: 1.5;
        }
        
        .modal-actions {
            display: flex;
            gap: 10px;
            justify-content: flex-end;
        }
        
        /* === TOAST === */
        .toast-container {
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 1001;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .toast {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px 20px;
            display: flex;
            align-items: center;
            gap: 10px;
            box-shadow: 0 4px 12px var(--shadow);
            animation: toastIn 0.3s ease;
        }
        
        .toast.success { border-left: 4px solid var(--accent); }
        .toast.error { border-left: 4px solid var(--danger); }
        .toast.warning { border-left: 4px solid var(--warning); }
        
        @keyframes toastIn {
            from { opacity: 0; transform: translateX(100px); }
            to { opacity: 1; transform: translateX(0); }
        }
        
        /* === SCROLLBAR === */
        ::-webkit-scrollbar {
            width: 8px;
        }
        
        ::-webkit-scrollbar-track {
            background: var(--bg-primary);
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--bg-tertiary);
            border-radius: 4px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: var(--bg-hover);
        }
        
        /* === RESPONSIVE === */
        @media (max-width: 900px) {
            .main-container {
                grid-template-columns: 1fr;
            }
            
            .history-panel {
                border-left: none;
                border-top: 1px solid var(--border);
                max-height: none;
            }
            
            .header-stats {
                display: none;
            }
        }
    </style>
</head>
<body>
    <!-- HEADER -->
    <header class="header">
        <div class="logo">
            <div class="logo-icon">⬇️</div>
            <span>PyLoader</span>
        </div>
        <div class="header-stats" id="header-stats">
            <div class="stat-item">
                <div class="stat-value" id="stat-total">0</div>
                <div class="stat-label">Файлов</div>
            </div>
            <div class="stat-item">
                <div class="stat-value" id="stat-size">0 MB</div>
                <div class="stat-label">Размер</div>
            </div>
            <div class="stat-item">
                <div class="stat-value" id="stat-active">0</div>
                <div class="stat-label">Активных</div>
            </div>
        </div>
    </header>
    
    <div class="main-container">
        <!-- LEFT PANEL -->
        <div class="downloads-panel">
            <!-- Input Section -->
            <div class="input-section">
                <div class="input-header">
                    <div class="input-title">
                        🔗 Добавить загрузку
                    </div>
                    <span class="badge" style="background: var(--info);">Плейлисты поддерживаются!</span>
                </div>
                <textarea class="url-input" id="urls" placeholder="Вставьте ссылки (каждая с новой строки)&#10;https://www.youtube.com/watch?v=...&#10;https://youtube.com/playlist?list=...&#10;&#10;💡 Поддерживаются одиночные видео и плейлисты" oninput="checkForPlaylist()"></textarea>
                
                <!-- Playlist Preview -->
                <div id="playlist-preview" style="display: none;"></div>
                
                <div class="input-controls">
                    <select class="format-select" id="format">
                        <option value="mp3">🎵 Audio (MP3)</option>
                        <option value="mp4">🎬 Video (MP4)</option>
                    </select>
                    <button class="btn btn-primary" onclick="startDownload()">
                        ⬇️ Скачать
                    </button>
                </div>
            </div>
            
            <!-- Active Downloads -->
            <div class="section-header">
                <div class="section-title">
                    📥 Активные загрузки
                    <span class="badge" id="active-count">0</span>
                </div>
                <button class="btn btn-secondary btn-sm" onclick="clearCompleted()">
                    🗑️ Очистить завершённые
                </button>
            </div>
            
            <div id="downloads-container">
                <!-- Downloads will appear here -->
            </div>
        </div>
        
        <!-- RIGHT PANEL - History -->
        <div class="history-panel">
            <div class="history-header">
                <div class="history-title">
                    📁 История загрузок
                </div>
                <div class="search-box">
                    <span class="search-icon">🔍</span>
                    <input type="text" class="search-input" id="search-input" placeholder="Поиск..." oninput="debounceSearch()">
                </div>
                <div class="history-filters">
                    <button class="filter-btn active" data-filter="all" onclick="setFilter('all')">Все</button>
                    <button class="filter-btn" data-filter="mp3" onclick="setFilter('mp3')">🎵 MP3</button>
                    <button class="filter-btn" data-filter="mp4" onclick="setFilter('mp4')">🎬 MP4</button>
                </div>
            </div>
            <div class="history-list" id="history-container">
                <!-- History will appear here -->
            </div>
        </div>
    </div>
    
    <!-- Delete Confirmation Modal -->
    <div class="modal-overlay" id="delete-modal">
        <div class="modal">
            <div class="modal-title">⚠️ Удалить файл?</div>
            <div class="modal-text" id="delete-modal-text">
                Вы уверены что хотите удалить этот файл? Это действие нельзя отменить.
            </div>
            <div class="modal-actions">
                <button class="btn btn-secondary" onclick="closeModal()">Отмена</button>
                <button class="btn btn-danger" onclick="confirmDelete()">Удалить</button>
            </div>
        </div>
    </div>
    
    <!-- Toast Container -->
    <div class="toast-container" id="toast-container"></div>

    <script>
        let currentFilter = 'all';
        let searchTimeout = null;
        let fileToDelete = null;
        let playlistCheckTimeout = null;
        let currentPlaylistData = null;
        
        // === PLAYLIST FUNCTIONS ===
        function checkForPlaylist() {
            clearTimeout(playlistCheckTimeout);
            const urlsText = document.getElementById('urls').value.trim();
            const preview = document.getElementById('playlist-preview');
            
            // Проверяем есть ли URL плейлиста
            const urls = urlsText.split('\\n').map(u => u.trim()).filter(u => u.length > 0);
            const playlistUrl = urls.find(url => url.includes('list=') || url.includes('/playlist'));
            
            if (!playlistUrl) {
                preview.style.display = 'none';
                currentPlaylistData = null;
                return;
            }
            
            // Debounce чтобы не спамить запросами
            playlistCheckTimeout = setTimeout(() => {
                preview.style.display = 'block';
                preview.innerHTML = `
                    <div class="playlist-loading">
                        <span class="spinner"></span>
                        Загрузка информации о плейлисте...
                    </div>
                `;
                
                fetch('/check_url', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: playlistUrl })
                })
                .then(res => res.json())
                .then(data => {
                    if (data.is_playlist) {
                        currentPlaylistData = data;
                        showPlaylistPreview(data);
                    } else {
                        preview.style.display = 'none';
                        currentPlaylistData = null;
                    }
                })
                .catch(err => {
                    preview.style.display = 'none';
                    currentPlaylistData = null;
                });
            }, 500);
        }
        
        function showPlaylistPreview(data) {
            const preview = document.getElementById('playlist-preview');
            const videosHtml = data.videos.slice(0, 10).map((video, index) => `
                <div class="playlist-video-item">
                    <div class="playlist-video-num">${index + 1}</div>
                    <div class="playlist-video-title" title="${video.title}">${video.title}</div>
                    <div class="playlist-video-duration">${formatDuration(video.duration)}</div>
                </div>
            `).join('');
            
            const moreCount = data.videos.length > 10 ? data.videos.length - 10 : 0;
            
            preview.innerHTML = `
                <div class="playlist-preview">
                    <div class="playlist-header">
                        <div class="playlist-icon">📋</div>
                        <div class="playlist-info">
                            <h3>${data.title}</h3>
                            <p>${data.count} видео · ${data.uploader}</p>
                        </div>
                    </div>
                    <div class="playlist-actions">
                        <button class="btn btn-primary btn-sm" onclick="downloadPlaylist('all')">
                            ⬇️ Скачать все (${data.count})
                        </button>
                        <button class="btn btn-secondary btn-sm" onclick="downloadPlaylist('first10')">
                            📥 Первые 10
                        </button>
                        <button class="btn btn-secondary btn-sm" onclick="hidePlaylistPreview()">
                            ✕ Скрыть
                        </button>
                    </div>
                    <div class="playlist-videos">
                        ${videosHtml}
                        ${moreCount > 0 ? `<div style="text-align: center; padding: 10px; color: var(--text-secondary);">... и ещё ${moreCount} видео</div>` : ''}
                    </div>
                </div>
            `;
        }
        
        function formatDuration(seconds) {
            if (!seconds) return '--:--';
            const mins = Math.floor(seconds / 60);
            const secs = seconds % 60;
            return `${mins}:${secs.toString().padStart(2, '0')}`;
        }
        
        function downloadPlaylist(mode) {
            if (!currentPlaylistData) return;
            
            const format = document.getElementById('format').value;
            let urls = [];
            
            if (mode === 'all') {
                urls = currentPlaylistData.videos.map(v => v.url);
            } else if (mode === 'first10') {
                urls = currentPlaylistData.videos.slice(0, 10).map(v => v.url);
            }
            
            if (urls.length === 0) {
                showToast('Нет видео для скачивания', 'warning');
                return;
            }
            
            fetch('/add_download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ urls, format })
            })
            .then(res => res.json())
            .then(data => {
                document.getElementById('urls').value = '';
                hidePlaylistPreview();
                if (data.added > 0) {
                    showToast(`Добавлено ${data.added} загрузок из плейлиста`, 'success');
                }
                if (data.skipped > 0) {
                    showToast(`Пропущено ${data.skipped} (уже качаются)`, 'warning');
                }
            });
        }
        
        function hidePlaylistPreview() {
            document.getElementById('playlist-preview').style.display = 'none';
            currentPlaylistData = null;
        }
        
        // === DOWNLOAD FUNCTIONS ===
        function startDownload() {
            // Если есть плейлист — используем его данные
            if (currentPlaylistData) {
                downloadPlaylist('all');
                return;
            }
            
            const urlsText = document.getElementById('urls').value;
            const format = document.getElementById('format').value;
            const urls = urlsText.split('\\n').map(u => u.trim()).filter(u => u.length > 0);

            if (urls.length === 0) {
                showToast('Введите хотя бы одну ссылку!', 'warning');
                return;
            }

            fetch('/add_download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ urls, format })
            })
            .then(res => res.json())
            .then(data => {
                document.getElementById('urls').value = '';
                hidePlaylistPreview();
                if (data.added > 0) {
                    showToast(`Добавлено ${data.added} загрузок`, 'success');
                }
                if (data.skipped > 0) {
                    showToast(`Пропущено ${data.skipped} (уже качаются)`, 'warning');
                }
            });
        }
        
        function cancelDownload(taskId) {
            fetch('/cancel_download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: taskId })
            })
            .then(res => res.json())
            .then(data => {
                showToast('Загрузка отменена', 'warning');
            });
        }
        
        function clearCompleted() {
            fetch('/clear_completed', { method: 'POST' })
            .then(res => res.json())
            .then(data => {
                showToast(`Очищено ${data.cleared} завершённых`, 'success');
                updateProgress();
            });
        }
        
        // === HISTORY FUNCTIONS ===
        function loadHistory() {
            const search = document.getElementById('search-input').value;
            fetch(`/history?format=${currentFilter}&search=${encodeURIComponent(search)}`)
            .then(res => res.json())
            .then(files => {
                const container = document.getElementById('history-container');
                
                if (files.length === 0) {
                    container.innerHTML = `
                        <div class="history-empty">
                            <div class="history-empty-icon">📭</div>
                            <div>Нет файлов</div>
                        </div>`;
                    return;
                }

                container.innerHTML = files.map(file => `
                    <div class="history-item">
                        ${file.thumbnail 
                            ? `<img src="${file.thumbnail}" class="history-thumb" alt="">` 
                            : `<div class="history-thumb-placeholder">${file.format === 'mp3' ? '🎵' : '🎬'}</div>`}
                        <div class="history-info">
                            <div class="history-title-text" title="${file.title}">${file.title}</div>
                            <div class="history-meta">
                                <span>💾 ${file.size}</span>
                                <span>📄 ${file.format.toUpperCase()}</span>
                            </div>
                        </div>
                        <div class="history-actions">
                            <a href="/download/${encodeURIComponent(file.filename)}" class="btn btn-primary btn-sm btn-icon" title="Скачать">⬇️</a>
                            <button class="btn btn-danger btn-sm btn-icon" onclick="showDeleteModal('${encodeURIComponent(file.filename)}', '${file.title}')" title="Удалить">🗑️</button>
                        </div>
                    </div>
                `).join('');
            });
        }
        
        function setFilter(filter) {
            currentFilter = filter;
            document.querySelectorAll('.filter-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.filter === filter);
            });
            loadHistory();
        }
        
        function debounceSearch() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(loadHistory, 300);
        }
        
        // === STATS ===
        function loadStats() {
            fetch('/stats')
            .then(res => res.json())
            .then(stats => {
                document.getElementById('stat-total').textContent = stats.total_count;
                document.getElementById('stat-size').textContent = stats.total_size;
            });
        }
        
        // === PROGRESS UPDATE ===
        function updateProgress() {
            fetch('/progress')
            .then(res => res.json())
            .then(data => {
                const container = document.getElementById('downloads-container');
                const entries = Object.entries(data);
                
                let activeCount = 0;
                let hasFinished = false;

                entries.forEach(([id, info]) => {
                    let el = document.getElementById(`task-${id}`);
                    
                    const isActive = !['Готово', 'Уже скачано', 'Отменено'].includes(info.status) && !info.status.startsWith('Ошибка');
                    if (isActive) activeCount++;

                    if (!el) {
                        el = document.createElement('div');
                        el.id = `task-${id}`;
                        el.className = 'download-card';
                        el.innerHTML = `
                            <div class="download-icon">${info.format === 'mp3' ? '🎵' : '🎬'}</div>
                            <div class="download-info">
                                <div class="download-title" id="title-${id}">Получение данных...</div>
                                <div class="download-url">${info.url}</div>
                                <div class="download-progress">
                                    <div class="progress-bar">
                                        <div class="progress-fill" id="bar-${id}"></div>
                                    </div>
                                    <div class="download-status">
                                        <span class="status-text">
                                            <span class="status-dot" id="dot-${id}"></span>
                                            <span id="status-${id}">Ожидание</span>
                                        </span>
                                        <span id="percent-${id}">0%</span>
                                    </div>
                                </div>
                            </div>
                            <div class="download-actions">
                                <button class="btn btn-danger btn-sm btn-icon" onclick="cancelDownload('${id}')" title="Отменить">✕</button>
                            </div>
                        `;
                        container.prepend(el);
                    }

                    const titleEl = document.getElementById(`title-${id}`);
                    const statusEl = document.getElementById(`status-${id}`);
                    const barEl = document.getElementById(`bar-${id}`);
                    const dotEl = document.getElementById(`dot-${id}`);
                    const percentEl = document.getElementById(`percent-${id}`);

                    if (info.title) titleEl.textContent = info.title;
                    statusEl.textContent = info.status;
                    barEl.style.width = info.percent;
                    percentEl.textContent = info.percent;
                    
                    // Update visual status
                    barEl.classList.remove('error', 'warning');
                    dotEl.classList.remove('finished', 'error', 'warning');
                    
                    if (info.status === 'Готово') {
                        dotEl.classList.add('finished');
                        hasFinished = true;
                    } else if (info.status === 'Уже скачано') {
                        dotEl.classList.add('warning');
                        barEl.classList.add('warning');
                    } else if (info.status.startsWith('Ошибка') || info.status === 'Отменено') {
                        dotEl.classList.add('error');
                        barEl.classList.add('error');
                    }
                });
                
                document.getElementById('active-count').textContent = activeCount;
                document.getElementById('stat-active').textContent = activeCount;
                
                if (hasFinished) {
                    loadHistory();
                    loadStats();
                }
            });
        }
        
        // === MODAL ===
        function showDeleteModal(filename, title) {
            fileToDelete = filename;
            document.getElementById('delete-modal-text').textContent = 
                `Вы уверены что хотите удалить "${decodeURIComponent(title)}"? Это действие нельзя отменить.`;
            document.getElementById('delete-modal').classList.add('active');
        }
        
        function closeModal() {
            document.getElementById('delete-modal').classList.remove('active');
            fileToDelete = null;
        }
        
        function confirmDelete() {
            if (!fileToDelete) return;
            
            fetch('/delete_file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: decodeURIComponent(fileToDelete) })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    showToast('Файл удалён', 'success');
                    loadHistory();
                    loadStats();
                } else {
                    showToast('Ошибка удаления', 'error');
                }
                closeModal();
            });
        }
        
        // === TOAST ===
        function showToast(message, type = 'success') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.innerHTML = `
                <span>${type === 'success' ? '✅' : type === 'error' ? '❌' : '⚠️'}</span>
                <span>${message}</span>
            `;
            container.appendChild(toast);
            
            setTimeout(() => {
                toast.style.animation = 'toastIn 0.3s ease reverse';
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }
        
        // === KEYBOARD SHORTCUTS ===
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeModal();
            if (e.ctrlKey && e.key === 'v' && document.activeElement.tagName !== 'TEXTAREA') {
                document.getElementById('urls').focus();
            }
        });
        
        // === INIT ===
        setInterval(updateProgress, 1000);
        loadHistory();
        loadStats();
    </script>
</body>
</html>
"""


# --- ЛОГИКА ЗАГРУЗКИ ---

def progress_hook(d, task_id):
    if task_id in cancelled_tasks:
        raise Exception("Загрузка отменена")

    if d['status'] == 'downloading':
        percent = d.get('_percent_str', '0%').replace('%', '').strip()
        try:
            download_status[task_id]['percent'] = f"{float(percent):.1f}%"
            download_status[task_id]['status'] = f"Скачивание"
        except:
            pass
    elif d['status'] == 'finished':
        download_status[task_id]['percent'] = "100%"
        download_status[task_id]['status'] = "Обработка..."


def get_ffmpeg_path():
    """Проверяет наличие ffmpeg.exe рядом со скриптом"""
    current_dir = os.getcwd()
    local_ffmpeg = os.path.join(current_dir, 'ffmpeg.exe')
    if os.path.exists(local_ffmpeg):
        return current_dir
    return None


def sanitize_filename(title):
    """Убирает проблемные символы из названия файла (как это делает yt-dlp)"""
    replacements = {
        '/': '⧸', '\\': '⧹', '|': '｜', ':': '：',
        '*': '＊', '?': '？', '"': '＂', '<': '＜', '>': '＞',
    }
    result = title
    for char, replacement in replacements.items():
        result = result.replace(char, replacement)
    return result


def move_thumbnail_to_folder(video_title):
    """Копирует обложку из downloads в downloads/thumbnails"""
    safe_title = sanitize_filename(video_title)

    for ext in ['.jpg', '.jpeg', '.png', '.webp']:
        thumb_path = os.path.join(DOWNLOAD_FOLDER, safe_title + ext)
        if os.path.exists(thumb_path):
            dest_path = os.path.join(THUMBNAILS_FOLDER, safe_title + ext)
            try:
                shutil.copy2(thumb_path, dest_path)
                os.remove(thumb_path)
            except:
                pass
            break


def save_thumbnail_from_url(video_title, thumbnail_url):
    """Скачивает обложку напрямую из URL"""
    if not thumbnail_url:
        return

    safe_title = sanitize_filename(video_title)
    dest_path = os.path.join(THUMBNAILS_FOLDER, safe_title + '.jpg')

    for ext in ['.jpg', '.jpeg', '.png', '.webp']:
        if os.path.exists(os.path.join(THUMBNAILS_FOLDER, safe_title + ext)):
            return

    try:
        urllib.request.urlretrieve(thumbnail_url, dest_path)
    except:
        pass


def download_task(task_id, url, fmt):
    try:
        if task_id in cancelled_tasks:
            download_status[task_id]['status'] = "Отменено"
            return

        ffmpeg_location = get_ffmpeg_path()

        ydl_opts = {
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
            'quiet': True,
            'progress_hooks': [lambda d: progress_hook(d, task_id)],
            'writethumbnail': True,
            'nooverwrites': True,
        }

        if ffmpeg_location:
            ydl_opts['ffmpeg_location'] = ffmpeg_location

        if fmt == 'mp3':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
                    {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'},
                    {'key': 'EmbedThumbnail'},
                    {'key': 'FFmpegMetadata'},
                ],
            })
        else:
            ydl_opts.update({
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'postprocessors': [
                    {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'},
                    {'key': 'EmbedThumbnail'},
                    {'key': 'FFmpegMetadata'},
                ],
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Без названия')
            download_status[task_id]['title'] = video_title

            safe_title = sanitize_filename(video_title)
            expected_file = os.path.join(DOWNLOAD_FOLDER, f"{safe_title}.{fmt}")

            if os.path.exists(expected_file):
                download_status[task_id]['status'] = "Уже скачано"
                download_status[task_id]['percent'] = "100%"
                return

            thumbnail_url = info.get('thumbnail')
            save_thumbnail_from_url(video_title, thumbnail_url)

            if task_id in cancelled_tasks:
                download_status[task_id]['status'] = "Отменено"
                return

            ydl.download([url])
            move_thumbnail_to_folder(video_title)

        download_status[task_id]['status'] = "Готово"
        download_status[task_id]['percent'] = "100%"

    except Exception as e:
        error_msg = str(e)
        if "Загрузка отменена" in error_msg:
            download_status[task_id]['status'] = "Отменено"
        elif "ffmpeg" in error_msg.lower():
            download_status[task_id]['status'] = "Ошибка: FFmpeg не найден"
        else:
            download_status[task_id]['status'] = f"Ошибка"
        print(f"Error: {e}")

    finally:
        normalized = url.split('&')[0] if '&' in url else url
        active_urls.discard(normalized)
        cancelled_tasks.discard(task_id)


# --- ВЕБ МАРШРУТЫ ---

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/check_url', methods=['POST'])
def check_url():
    """Проверяет URL и возвращает информацию о плейлисте если это плейлист"""
    url = request.json.get('url', '').strip()
    if not url:
        return jsonify({'is_playlist': False})

    if is_playlist_url(url):
        info = get_playlist_info(url)
        return jsonify(info)

    return jsonify({'is_playlist': False})


@app.route('/add_download', methods=['POST'])
def add_download():
    data = request.json
    urls = data.get('urls', [])
    fmt = data.get('format', 'mp3')
    added = 0
    skipped = 0

    for url in urls:
        normalized_url = url.split('&')[0] if '&' in url else url

        if normalized_url in active_urls:
            skipped += 1
            continue

        active_urls.add(normalized_url)
        task_id = str(uuid.uuid4())
        download_status[task_id] = {
            'url': url,
            'status': 'В очереди',
            'percent': '0%',
            'title': 'Получение данных...',
            'format': fmt
        }
        executor.submit(download_task, task_id, normalized_url, fmt)
        added += 1

    return jsonify({'status': 'ok', 'added': added, 'skipped': skipped})


@app.route('/cancel_download', methods=['POST'])
def cancel_download():
    task_id = request.json.get('task_id')
    if task_id:
        cancelled_tasks.add(task_id)
        if task_id in download_status:
            download_status[task_id]['status'] = 'Отменено'
    return jsonify({'status': 'ok'})


@app.route('/clear_completed', methods=['POST'])
def clear_completed():
    cleared = 0
    to_remove = []

    completed_statuses = ['Готово', 'Уже скачано', 'Отменено']

    for task_id, info in download_status.items():
        status = info.get('status', '')
        # Проверяем завершённые статусы
        is_completed = (
            status in completed_statuses or
            status.startswith('Ошибка') or
            'Ошибка' in status or
            info.get('percent', '0%') == '100%' and status not in ['Скачивание', 'В очереди', 'Обработка...']
        )

        if is_completed:
            to_remove.append(task_id)
            cleared += 1
            print(f"Clearing task {task_id}: {status}")

    for task_id in to_remove:
        del download_status[task_id]

    print(f"Cleared {cleared} tasks, remaining: {len(download_status)}")
    return jsonify({'status': 'ok', 'cleared': cleared})


@app.route('/progress')
def get_progress():
    return jsonify(download_status)


@app.route('/history')
def get_history():
    filter_format = request.args.get('format', 'all')
    search = request.args.get('search', '')
    sort_by = request.args.get('sort', 'date')
    files = get_history_files(filter_format, search, sort_by)
    return jsonify(files)


@app.route('/stats')
def get_stats():
    return jsonify(get_folder_stats())


@app.route('/delete_file', methods=['POST'])
def delete_file():
    filename = request.json.get('filename')
    if not filename:
        return jsonify({'success': False})

    try:
        filepath = os.path.join(DOWNLOAD_FOLDER, filename)
        if os.path.exists(filepath):
            os.remove(filepath)

        # Удаляем обложку
        name_without_ext = os.path.splitext(filename)[0]
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            thumb_path = os.path.join(THUMBNAILS_FOLDER, name_without_ext + ext)
            if os.path.exists(thumb_path):
                os.remove(thumb_path)
                break

        return jsonify({'success': True})
    except Exception as e:
        print(f"Delete error: {e}")
        return jsonify({'success': False})


@app.route('/download/<path:filename>')
def download_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)


@app.route('/thumbnails/<path:filename>')
def serve_thumbnail(filename):
    return send_from_directory(THUMBNAILS_FOLDER, filename)


if __name__ == '__main__':
    print("=" * 50)
    print("🚀 PyLoader - Video Downloader")
    print("=" * 50)
    print(f"📂 Папка загрузок: {os.path.abspath(DOWNLOAD_FOLDER)}")
    print(f"🌐 Открой в браузере: http://127.0.0.1:5000")
    print()

    if get_ffmpeg_path():
        print("✅ FFmpeg найден")
    else:
        print("⚠️  FFmpeg не найден в папке скрипта")

    print("=" * 50)
    app.run(debug=True, port=5000)

