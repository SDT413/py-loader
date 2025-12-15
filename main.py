import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template_string, request, jsonify, send_from_directory
import yt_dlp

# --- КОНФИГУРАЦИЯ ---
app = Flask(__name__)
DOWNLOAD_FOLDER = "downloads"
THUMBNAILS_FOLDER = "downloads/thumbnails"
MAX_CONCURRENT_DOWNLOADS = 3  # Количество одновременных загрузок

# Глобальное хранилище состояния загрузок
download_status = {}
active_urls = set()  # Защита от дублирования загрузок
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS)

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

if not os.path.exists(THUMBNAILS_FOLDER):
    os.makedirs(THUMBNAILS_FOLDER)


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


def get_history_files(filter_format='all'):
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

        # Поиск обложки
        name_without_ext = os.path.splitext(filename)[0]
        thumbnail = None
        for thumb_ext in ['.jpg', '.jpeg', '.png', '.webp']:
            thumb_path = os.path.join(THUMBNAILS_FOLDER, name_without_ext + thumb_ext)
            if os.path.exists(thumb_path):
                # URL-encode для спецсимволов в названии
                import urllib.parse
                encoded_name = urllib.parse.quote(name_without_ext + thumb_ext)
                thumbnail = f"/thumbnails/{encoded_name}"
                break

        # Информация о файле
        stat = os.stat(filepath)
        files.append({
            'filename': filename,
            'title': name_without_ext,
            'format': ext[1:],  # mp3 или mp4
            'size': format_file_size(stat.st_size),
            'size_bytes': stat.st_size,
            'modified': stat.st_mtime,
            'thumbnail': thumbnail
        })

    # Сортировка по дате изменения (новые сверху)
    files.sort(key=lambda x: x['modified'], reverse=True)
    return files


# --- HTML ШАБЛОН ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YouTube Downloader Pro</title>
    <style>
        :root {
            --bg-color: #1f1f1f;
            --card-bg: #2d2d2d;
            --text-color: #e0e0e0;
            --accent-color: #4CAF50;
            --error-color: #f44336;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            display: flex;
            justify-content: center;
            min-height: 100vh;
        }
        .container {
            width: 100%;
            max-width: 800px;
            padding: 20px;
        }
        h1 { text-align: center; color: var(--accent-color); margin-bottom: 30px; }

        .input-card {
            background-color: var(--card-bg);
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            margin-bottom: 20px;
        }

        textarea {
            width: 100%;
            height: 100px;
            background-color: #121212;
            border: 1px solid #444;
            color: white;
            padding: 10px;
            border-radius: 5px;
            resize: vertical;
            font-family: monospace;
            box-sizing: border-box; 
        }
        textarea:focus { outline: 2px solid var(--accent-color); }

        .controls {
            display: flex;
            gap: 10px;
            margin-top: 15px;
            align-items: center;
        }

        select, button {
            padding: 10px 15px;
            border-radius: 5px;
            border: none;
            cursor: pointer;
            font-weight: bold;
        }

        select { background: #444; color: white; }

        button#download-btn {
            background-color: var(--accent-color);
            color: white;
            flex-grow: 1;
            transition: background 0.2s;
        }
        button#download-btn:hover { background-color: #45a049; }

        .downloads-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .download-item {
            background-color: var(--card-bg);
            padding: 15px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            gap: 15px;
            animation: fadeIn 0.3s;
        }

        .icon { font-size: 24px; }

        .info { flex-grow: 1; overflow: hidden; }
        .title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: bold; }
        .url-sub { font-size: 0.8em; color: #aaa; }

        .progress-container {
            width: 150px;
            text-align: right;
        }

        .progress-bar-bg {
            width: 100%;
            height: 8px;
            background: #444;
            border-radius: 4px;
            margin-top: 5px;
            overflow: hidden;
        }

        .progress-bar-fill {
            height: 100%;
            background: var(--accent-color);
            width: 0%;
            transition: width 0.3s ease;
        }

        .status-badge {
            font-size: 0.8em;
            padding: 3px 8px;
            border-radius: 10px;
            background: #444;
        }

        .status-finished { background: var(--accent-color); color: white; }
        .status-error { background: var(--error-color); color: white; }
        .status-skipped { background: #ff9800; color: white; }

        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

        /* История файлов */
        .section-title {
            color: var(--accent-color);
            margin: 30px 0 15px 0;
            font-size: 1.2em;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .history-item {
            background-color: var(--card-bg);
            padding: 12px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 10px;
            transition: transform 0.2s;
        }
        .history-item:hover {
            transform: translateX(5px);
        }

        .thumbnail {
            width: 80px;
            height: 60px;
            border-radius: 5px;
            object-fit: cover;
            background: #444;
            flex-shrink: 0;
        }

        .thumbnail-placeholder {
            width: 80px;
            height: 60px;
            border-radius: 5px;
            background: linear-gradient(135deg, #444 0%, #333 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            flex-shrink: 0;
        }

        .file-info {
            flex-grow: 1;
            overflow: hidden;
        }

        .file-title {
            font-weight: bold;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-bottom: 5px;
        }

        .file-meta {
            font-size: 0.85em;
            color: #aaa;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }

        .file-meta span {
            display: flex;
            align-items: center;
            gap: 4px;
        }

        .download-link {
            background: var(--accent-color);
            color: white;
            padding: 8px 15px;
            border-radius: 5px;
            text-decoration: none;
            font-size: 0.9em;
            white-space: nowrap;
            transition: background 0.2s;
        }
        .download-link:hover {
            background: #45a049;
        }

        .empty-history {
            text-align: center;
            color: #666;
            padding: 40px;
            background: var(--card-bg);
            border-radius: 8px;
        }

        /* Обложка в активных загрузках */
        .download-thumbnail {
            width: 60px;
            height: 45px;
            border-radius: 4px;
            object-fit: cover;
            background: #444;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>YouTube Downloader</h1>

        <div class="input-card">
            <label>Вставьте ссылки (одна на строку):</label>
            <textarea id="urls" placeholder="https://www.youtube.com/watch?v=..."></textarea>

            <div class="controls">
                <select id="format">
                    <option value="mp3">Audio (MP3)</option>
                    <option value="mp4">Video (MP4 1080p)</option>
                </select>
                <button id="download-btn" onclick="startDownload()">Скачать</button>
            </div>
        </div>

        <div id="downloads-container" class="downloads-list">
            <!-- Сюда будут добавляться загрузки -->
        </div>

        <h2 class="section-title">📁 История загрузок</h2>
        <div id="history-container">
            <!-- Сюда будет загружена история -->
        </div>
    </div>

    <script>
        let currentFilter = 'all';

        function startDownload() {
            const urlsText = document.getElementById('urls').value;
            const format = document.getElementById('format').value;

            const urls = urlsText.split('\\n').map(u => u.trim()).filter(u => u.length > 0);

            if (urls.length === 0) {
                alert("Введите хотя бы одну ссылку!");
                return;
            }

            fetch('/add_download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ urls: urls, format: format })
            })
            .then(response => response.json())
            .then(data => {
                document.getElementById('urls').value = '';
                console.log("Задачи добавлены");
            });
        }

        function loadHistory() {
            const format = document.getElementById('format').value;
            fetch(`/history?format=${format}`)
            .then(res => res.json())
            .then(files => {
                const container = document.getElementById('history-container');
                
                if (files.length === 0) {
                    container.innerHTML = '<div class="empty-history">📭 Нет скачанных файлов</div>';
                    return;
                }

                container.innerHTML = files.map(file => `
                    <div class="history-item">
                        ${file.thumbnail 
                            ? `<img src="${file.thumbnail}" class="thumbnail" alt="thumbnail">` 
                            : `<div class="thumbnail-placeholder">${file.format === 'mp3' ? '🎵' : '🎬'}</div>`
                        }
                        <div class="file-info">
                            <div class="file-title">${file.title}</div>
                            <div class="file-meta">
                                <span>📦 ${file.size}</span>
                                <span>📄 ${file.format.toUpperCase()}</span>
                                <span>📅 ${new Date(file.modified * 1000).toLocaleDateString('ru-RU')}</span>
                            </div>
                        </div>
                        <a href="/download/${encodeURIComponent(file.filename)}" class="download-link">⬇️ Скачать</a>
                    </div>
                `).join('');
            });
        }

        // Обновляем историю при смене формата
        document.getElementById('format').addEventListener('change', loadHistory);

        function updateProgress() {
            fetch('/progress')
            .then(res => res.json())
            .then(data => {
                const container = document.getElementById('downloads-container');
                let hasFinished = false;

                for (const [id, info] of Object.entries(data)) {
                    let el = document.getElementById(`task-${id}`);

                    if (!el) {
                        el = document.createElement('div');
                        el.id = `task-${id}`;
                        el.className = 'download-item';
                        el.innerHTML = `
                            <div class="icon">${info.format === 'mp3' ? '🎵' : '🎬'}</div>
                            <div class="info">
                                <div class="title" id="title-${id}">Загрузка метаданных...</div>
                                <div class="url-sub">${info.url}</div>
                            </div>
                            <div class="progress-container">
                                <span id="status-${id}" class="status-badge">Ожидание</span>
                                <div class="progress-bar-bg">
                                    <div id="bar-${id}" class="progress-bar-fill"></div>
                                </div>
                            </div>
                        `;
                        container.prepend(el);
                    }

                    const titleEl = document.getElementById(`title-${id}`);
                    const statusEl = document.getElementById(`status-${id}`);
                    const barEl = document.getElementById(`bar-${id}`);

                    if (info.title) titleEl.textContent = info.title;
                    statusEl.textContent = info.status;
                    barEl.style.width = info.percent;

                    if (info.status === 'Готово') {
                        statusEl.className = 'status-badge status-finished';
                        hasFinished = true;
                    } else if (info.status === 'Уже скачано') {
                        statusEl.className = 'status-badge status-skipped';
                    } else if (info.status.startsWith('Ошибка')) {
                        statusEl.className = 'status-badge status-error';
                    }
                }
                
                // Обновляем историю если что-то завершилось
                if (hasFinished) {
                    loadHistory();
                }
            });
        }

        setInterval(updateProgress, 1000);
        
        // Загружаем историю при загрузке страницы
        loadHistory();
    </script>
</body>
</html>
"""


# --- ЛОГИКА ЗАГРУЗКИ ---

def progress_hook(d, task_id):
    if d['status'] == 'downloading':
        percent = d.get('_percent_str', '0%').replace('%', '')
        try:
            download_status[task_id]['percent'] = f"{float(percent)}%"
            download_status[task_id]['status'] = f"Скачивание {d.get('_percent_str', '')}"
        except:
            pass
    elif d['status'] == 'finished':
        download_status[task_id]['percent'] = "100%"
        download_status[task_id]['status'] = "Обработка метаданных..."


def get_ffmpeg_path():
    """Проверяет наличие ffmpeg.exe рядом со скриптом"""
    current_dir = os.getcwd()
    local_ffmpeg = os.path.join(current_dir, 'ffmpeg.exe')
    if os.path.exists(local_ffmpeg):
        return current_dir  # yt-dlp ожидает путь к папке или файлу
    return None


def sanitize_filename(title):
    """Убирает проблемные символы из названия файла (как это делает yt-dlp)"""
    # yt-dlp заменяет эти символы
    replacements = {
        '/': '⧸',
        '\\': '⧹',
        '|': '｜',
        ':': '：',
        '*': '＊',
        '?': '？',
        '"': '＂',
        '<': '＜',
        '>': '＞',
    }
    result = title
    for char, replacement in replacements.items():
        result = result.replace(char, replacement)
    return result


def move_thumbnail_to_folder(video_title):
    """Копирует/перемещает обложку из downloads в downloads/thumbnails"""
    import shutil

    # Санитизируем название как yt-dlp
    safe_title = sanitize_filename(video_title)

    # Ищем обложку в папке downloads (после конвертации будет .jpg)
    for ext in ['.jpg', '.jpeg', '.png', '.webp']:
        thumb_path = os.path.join(DOWNLOAD_FOLDER, safe_title + ext)
        if os.path.exists(thumb_path):
            dest_path = os.path.join(THUMBNAILS_FOLDER, safe_title + ext)
            try:
                # Копируем, т.к. EmbedThumbnail может удалить оригинал
                shutil.copy2(thumb_path, dest_path)
                print(f"✅ Обложка скопирована: {dest_path}")
                # Пробуем удалить оригинал (если ещё есть)
                try:
                    os.remove(thumb_path)
                except:
                    pass
            except Exception as e:
                print(f"⚠️ Ошибка копирования обложки: {e}")
            break


def save_thumbnail_from_url(video_title, thumbnail_url):
    """Скачивает обложку напрямую из URL и сохраняет в папку thumbnails"""
    import urllib.request

    if not thumbnail_url:
        return

    # Санитизируем название как yt-dlp
    safe_title = sanitize_filename(video_title)

    # Всегда сохраняем как jpg для единообразия
    dest_path = os.path.join(THUMBNAILS_FOLDER, safe_title + '.jpg')

    # Если уже есть (любое расширение) — не скачиваем
    for ext in ['.jpg', '.jpeg', '.png', '.webp']:
        if os.path.exists(os.path.join(THUMBNAILS_FOLDER, safe_title + ext)):
            return

    try:
        urllib.request.urlretrieve(thumbnail_url, dest_path)
        print(f"✅ Обложка скачана: {dest_path}")
    except Exception as e:
        print(f"⚠️ Ошибка скачивания обложки: {e}")


def download_task(task_id, url, fmt):
    print(f"Запуск задачи {task_id} для {url}")

    try:
        # Ищем FFmpeg рядом со скриптом
        ffmpeg_location = get_ffmpeg_path()

        ydl_opts = {
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
            'quiet': True,
            'progress_hooks': [lambda d: progress_hook(d, task_id)],
            'writethumbnail': True,  # Скачиваем обложку отдельно для UI
            'nooverwrites': True,  # Не перезаписывать существующие файлы
        }

        if ffmpeg_location:
            ydl_opts['ffmpeg_location'] = ffmpeg_location

        if fmt == 'mp3':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    },
                    {
                        # Конвертируем обложку в JPEG для лучшей совместимости
                        'key': 'FFmpegThumbnailsConvertor',
                        'format': 'jpg',
                    },
                    {'key': 'EmbedThumbnail'},  # Вшиваем обложку в MP3 (будет работать в Telegram!)
                    {'key': 'FFmpegMetadata'},  # Добавляем автора, название и т.д.
                ],
            })
        else:
            # Video
            ydl_opts.update({
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'postprocessors': [
                    {
                        'key': 'FFmpegThumbnailsConvertor',
                        'format': 'jpg',
                    },
                    {'key': 'EmbedThumbnail'},  # Вшиваем обложку в MP4
                    {'key': 'FFmpegMetadata'},  # Добавляем метаданные
                ],
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Без названия')
            download_status[task_id]['title'] = video_title

            # Проверяем существует ли уже файл
            safe_title = sanitize_filename(video_title)
            expected_file = os.path.join(DOWNLOAD_FOLDER, f"{safe_title}.{fmt}")
            if os.path.exists(expected_file):
                download_status[task_id]['status'] = "Уже скачано"
                download_status[task_id]['percent'] = "100%"
                return

            # Сохраняем обложку ДО загрузки (пока EmbedThumbnail её не удалил)
            thumbnail_url = info.get('thumbnail')
            save_thumbnail_from_url(video_title, thumbnail_url)

            ydl.download([url])

            # Пробуем также переместить если осталась локальная копия
            move_thumbnail_to_folder(video_title)

        download_status[task_id]['status'] = "Готово"
        download_status[task_id]['percent'] = "100%"

    except Exception as e:
        error_msg = str(e)
        if "ffmpeg" in error_msg.lower():
            error_msg = "Ошибка: Не найден FFmpeg!"
        elif len(error_msg) > 50:
            error_msg = error_msg[:50] + "..."

        download_status[task_id]['status'] = f"Ошибка: {error_msg}"
        print(f"Error downloading {url}: {e}")

    finally:
        # Убираем URL из активных после завершения
        # Нормализуем как в add_download
        normalized = url.split('&')[0] if '&' in url else url
        active_urls.discard(normalized)


# --- ВЕБ МАРШРУТЫ ---

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/add_download', methods=['POST'])
def add_download():
    data = request.json
    urls = data.get('urls', [])
    fmt = data.get('format', 'mp3')

    added = 0
    skipped = 0

    for url in urls:
        # Нормализуем URL (убираем лишние параметры для сравнения)
        normalized_url = url.split('&')[0] if '&' in url else url

        # Проверяем не качается ли уже этот URL
        if normalized_url in active_urls:
            skipped += 1
            continue

        # Добавляем в активные
        active_urls.add(normalized_url)

        task_id = str(uuid.uuid4())
        download_status[task_id] = {
            'url': url,
            'status': 'В очереди',
            'percent': '0%',
            'title': 'Ожидание метаданных...',
            'format': fmt
        }
        executor.submit(download_task, task_id, normalized_url, fmt)
        added += 1

    return jsonify({'status': 'ok', 'added': added, 'skipped': skipped})


@app.route('/progress')
def get_progress():
    return jsonify(download_status)


@app.route('/history')
def get_history():
    """API для получения истории скачанных файлов"""
    filter_format = request.args.get('format', 'all')
    files = get_history_files(filter_format)
    return jsonify(files)


@app.route('/download/<path:filename>')
def download_file(filename):
    """Отдает файл для скачивания"""
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)


@app.route('/thumbnails/<path:filename>')
def serve_thumbnail(filename):
    return send_from_directory(THUMBNAILS_FOLDER, filename)


if __name__ == '__main__':
    print(f"🚀 Сервер запущен! Откройте в браузере: http://127.0.0.1:5000")
    if not get_ffmpeg_path():
        print("⚠️ ПРЕДУПРЕЖДЕНИЕ: ffmpeg.exe не найден в папке скрипта.")
        print("⚠️ Если он не установлен в системе глобально, скачивание MP3 не сработает.")
    else:
        print("✅ FFmpeg найден в папке скрипта.")

    app.run(debug=True, port=5000)