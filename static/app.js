const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
    mode: "audio",
    analysis: null,
    analysisItems: [],
    selectedKeys: new Set(),
    jobs: [],
    libraryItems: [],
    libraryTotal: 0,
    lastTerminalCount: 0,
    libraryTimer: null,
};

const formatOptions = {
    audio: [
        ["mp3", "MP3"],
        ["m4a", "M4A · без перекодирования"],
        ["opus", "OPUS · компактный"],
    ],
    video: [
        ["mp4", "MP4 · совместимый"],
        ["mkv", "MKV · максимум качества"],
    ],
};

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[char]);
}

function encodePath(path) {
    return String(path || "").split("/").map(encodeURIComponent).join("/");
}

function formatDuration(seconds) {
    const value = Number(seconds || 0);
    if (!value) return "—";
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    const secs = Math.floor(value % 60);
    return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}` : `${minutes}:${String(secs).padStart(2, "0")}`;
}

function formatEta(seconds) {
    if (seconds === null || seconds === undefined) return "";
    if (seconds < 60) return `${Math.max(0, Math.round(seconds))} сек`;
    return `${Math.ceil(seconds / 60)} мин`;
}

async function api(url, options = {}) {
    const init = { ...options, headers: { ...(options.headers || {}) } };
    if (init.body && typeof init.body !== "string") {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(init.body);
    }
    const response = await fetch(url, init);
    let data;
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
}

function toast(message, type = "success", timeout = 4200) {
    const node = document.createElement("div");
    node.className = `toast ${type}`;
    node.textContent = message;
    $("#toast-region").append(node);
    window.setTimeout(() => {
        node.classList.add("out");
        window.setTimeout(() => node.remove(), 220);
    }, timeout);
}

function setButtonLoading(button, loading, label = "Подождите…") {
    if (!button) return;
    if (loading) {
        button.dataset.original = button.innerHTML;
        button.disabled = true;
        button.innerHTML = `<span class="spinner"></span>${escapeHtml(label)}`;
    } else {
        button.disabled = false;
        if (button.dataset.original) button.innerHTML = button.dataset.original;
    }
}

function parseUrls() {
    return $("#urls-input").value.split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
}

function currentOptions() {
    return {
        format: $("#format-select").value,
        quality: $("#quality-select").value,
        bitrate: $("#bitrate-select").value,
        skip_duplicates: $("#skip-duplicates").checked,
        organize: $("#organize-playlists").checked,
        numerate: $("#numerate-playlists").checked,
        subtitles: $("#subtitles-enabled").checked,
        auto_subtitles: $("#auto-subtitles").checked,
        subtitle_languages: $("#subtitle-languages").value.trim(),
        cookies_browser: $("#cookies-browser").value,
        proxy: $("#proxy-input").value.trim(),
    };
}

function saveSettings() {
    localStorage.setItem("pyloader.smart.v2", JSON.stringify({ mode: state.mode, ...currentOptions() }));
}

function restoreSettings() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem("pyloader.smart.v2") || "{}"); } catch { saved = {}; }
    state.mode = saved.mode === "video" ? "video" : "audio";
    updateMode(state.mode, saved.format);
    const values = {
        "quality-select": saved.quality,
        "bitrate-select": saved.bitrate,
        "cookies-browser": saved.cookies_browser,
        "subtitle-languages": saved.subtitle_languages,
        "proxy-input": saved.proxy,
    };
    Object.entries(values).forEach(([id, value]) => {
        const element = $(`#${id}`);
        if (element && value !== undefined && value !== null) element.value = value;
    });
    const checks = {
        "skip-duplicates": saved.skip_duplicates ?? true,
        "organize-playlists": saved.organize ?? false,
        "numerate-playlists": saved.numerate ?? false,
        "subtitles-enabled": saved.subtitles ?? false,
        "auto-subtitles": saved.auto_subtitles ?? false,
    };
    Object.entries(checks).forEach(([id, value]) => { $(`#${id}`).checked = Boolean(value); });
    saveSettings();
}

function updateMode(mode, preferredFormat = null) {
    state.mode = mode;
    $$(".segment").forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
    const format = $("#format-select");
    const allowed = formatOptions[mode];
    const nextValue = allowed.some(([value]) => value === preferredFormat) ? preferredFormat : allowed[0][0];
    format.innerHTML = allowed.map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`).join("");
    format.value = nextValue;
    $("#bitrate-field").classList.toggle("hidden", mode !== "audio" || nextValue !== "mp3");
    $("#quality-field").classList.toggle("hidden", mode !== "video");
    $$(".video-only").forEach((element) => element.classList.toggle("hidden", mode !== "video"));
    saveSettings();
}

function onFormatChanged() {
    $("#bitrate-field").classList.toggle("hidden", state.mode !== "audio" || $("#format-select").value !== "mp3");
    saveSettings();
}

async function inspectLinks(queueAfter = false) {
    const urls = parseUrls();
    if (!urls.length) {
        toast("Добавь хотя бы одну ссылку", "warning");
        $("#urls-input").focus();
        return null;
    }
    const button = $("#inspect-button");
    setButtonLoading(button, true, "Анализирую…");
    $("#analysis-panel").classList.remove("hidden");
    $("#analysis-items").innerHTML = `<div class="empty-state"><span class="spinner"></span><strong>Читаю YouTube</strong><p>Большие каналы и плейлисты могут занять немного времени.</p></div>`;
    try {
        const data = await api("/api/inspect", {
            method: "POST",
            body: {
                urls,
                cookies_browser: $("#cookies-browser").value,
                proxy: $("#proxy-input").value.trim(),
            },
        });
        state.analysis = data;
        state.analysisItems = data.items || [];
        state.selectedKeys = new Set(state.analysisItems.map((item) => item.key));
        renderAnalysis();
        if (data.errors?.length) toast(`Не удалось разобрать ссылок: ${data.errors.length}`, "warning", 6500);
        if (!state.analysisItems.length) toast("Доступных элементов не найдено", "error");
        if (queueAfter && state.analysisItems.length) await queueSelected();
        return data;
    } catch (error) {
        $("#analysis-items").innerHTML = `<div class="empty-state"><span class="empty-icon">!</span><strong>Анализ не удался</strong><p>${escapeHtml(error.message)}</p></div>`;
        toast(error.message, "error", 7000);
        return null;
    } finally {
        setButtonLoading(button, false);
    }
}

function renderAnalysis() {
    const data = state.analysis;
    if (!data) return;
    $("#analysis-panel").classList.remove("hidden");
    $("#analysis-title").textContent = data.sources.length === 1 ? data.sources[0].title : `Источников: ${data.sources.length}`;
    $("#analysis-summary").innerHTML = [
        [data.raw_count, "позиций найдено"],
        [data.unique_count, "уникальных"],
        [data.duplicate_count, "повторов убрано"],
        [formatDuration(data.total_duration), "общая длительность"],
    ].map(([value, label]) => `<div class="summary-cell"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`).join("");
    $("#source-list").innerHTML = data.sources.map((source) => `
        <div class="source-chip" title="${escapeHtml(source.title)}">
            <strong>${escapeHtml(source.title)}</strong>
            <small>${escapeHtml(source.uploader)} · ${source.count}</small>
        </div>`).join("");
    $("#analysis-note").textContent = data.errors.length ? `Ошибок источников: ${data.errors.length}` : "Повторы уже исключены по YouTube ID";
    renderAnalysisItems();
}

function renderAnalysisItems() {
    const query = $("#analysis-search").value.trim().toLocaleLowerCase();
    const visible = state.analysisItems.filter((item) => {
        const text = `${item.title || ""} ${item.playlist_title || ""}`.toLocaleLowerCase();
        return !query || text.includes(query);
    });
    $("#analysis-items").innerHTML = visible.length ? visible.map((item) => {
        const index = state.analysisItems.indexOf(item);
        const checked = state.selectedKeys.has(item.key) ? "checked" : "";
        const thumb = item.thumbnail ? `<img class="analysis-thumb" src="${escapeHtml(item.thumbnail)}" alt="" loading="lazy">` : `<div class="analysis-thumb"></div>`;
        const availability = ["private", "subscriber_only", "premium_only", "needs_auth"].includes(item.availability)
            ? `<span class="availability">Доступ может потребовать cookies</span>` : "";
        return `<label class="analysis-item">
            <input type="checkbox" data-analysis-index="${index}" ${checked}>
            ${thumb}
            <span><strong title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</strong><small>${escapeHtml(item.playlist_title || "Видео")} · ${formatDuration(item.duration)}</small></span>
            ${availability}
        </label>`;
    }).join("") : `<div class="empty-state"><strong>Ничего не найдено</strong><p>Попробуй другой запрос.</p></div>`;
    updateSelectedCount();
}

function updateSelectedCount() {
    $("#selected-count").textContent = `${state.selectedKeys.size} выбрано`;
    $("#download-selected-button").textContent = `Скачать выбранное · ${state.selectedKeys.size}`;
    $("#download-selected-button").disabled = state.selectedKeys.size === 0;
}

async function queueSelected() {
    const selected = state.analysisItems.filter((item) => state.selectedKeys.has(item.key));
    if (!selected.length) {
        toast("Выбери хотя бы один элемент", "warning");
        return;
    }
    const button = $("#download-selected-button");
    setButtonLoading(button, true, "Добавляю…");
    try {
        const data = await api("/api/jobs", { method: "POST", body: { items: selected, options: currentOptions() } });
        const skipped = data.skipped_active + data.skipped_library + data.skipped_batch;
        if (data.added) toast(`Добавлено в очередь: ${data.added}`);
        if (skipped) toast(`Пропущено без повторной загрузки: ${skipped}`, "warning");
        saveSettings();
        await refreshJobs();
    } catch (error) {
        toast(error.message, "error", 7000);
    } finally {
        setButtonLoading(button, false);
        updateSelectedCount();
    }
}

async function downloadAll() {
    if (state.analysisItems.length && parseUrls().length) {
        state.selectedKeys = new Set(state.analysisItems.map((item) => item.key));
        renderAnalysisItems();
        await queueSelected();
    } else {
        await inspectLinks(true);
    }
}

const stateLabels = {
    queued: "В очереди", extracting: "Получение данных", downloading: "Скачивание",
    processing: "Обработка", completed: "Готово", skipped: "Пропущено",
    failed: "Ошибка", cancelled: "Отменено",
};

async function refreshJobs() {
    try {
        const data = await api("/api/jobs");
        state.jobs = data.jobs;
        $("#active-count").textContent = data.active;
        renderJobs();
        const terminalCount = state.jobs.filter((job) => ["completed", "failed", "cancelled", "skipped"].includes(job.state)).length;
        if (terminalCount !== state.lastTerminalCount) {
            state.lastTerminalCount = terminalCount;
            window.clearTimeout(state.libraryTimer);
            state.libraryTimer = window.setTimeout(() => { refreshLibrary(); refreshStats(); }, 500);
        }
    } catch (error) {
        console.error("Job refresh failed", error);
    }
}

function renderJobs() {
    $("#queue-empty").classList.toggle("hidden", state.jobs.length > 0);
    $("#job-list").innerHTML = state.jobs.map((job) => {
        const active = !["completed", "failed", "cancelled", "skipped"].includes(job.state);
        const meta = [stateLabels[job.state] || job.status, job.speed_text, job.eta ? `ещё ${formatEta(job.eta)}` : ""].filter(Boolean).join(" · ");
        const error = job.error ? `<div class="job-error" title="${escapeHtml(job.error)}">${escapeHtml(job.error)}</div>` : "";
        const action = active
            ? `<button class="icon-button" data-job-action="cancel" data-job-id="${job.id}" title="Отменить" aria-label="Отменить">×</button>`
            : job.state === "failed"
                ? `<button class="button ghost compact" data-job-action="retry" data-job-id="${job.id}">Повторить</button>`
                : "";
        return `<article class="job-card ${escapeHtml(job.state)}">
            <div class="job-icon">${job.format.toUpperCase()}</div>
            <div class="job-main">
                <div class="job-title" title="${escapeHtml(job.title)}">${escapeHtml(job.title)}</div>
                <div class="job-meta"><span>${escapeHtml(meta)}</span><span>${Number(job.progress || 0).toFixed(1)}%</span></div>
                ${error}
                <div class="progress-track"><div class="progress-fill" style="width:${Math.max(0, Math.min(100, Number(job.progress || 0)))}%"></div></div>
            </div>
            <div class="job-actions">${action}</div>
        </article>`;
    }).join("");
}

async function jobAction(action, jobId) {
    try {
        await api(`/api/jobs/${encodeURIComponent(jobId)}/${action}`, { method: "POST" });
        toast(action === "retry" ? "Повтор добавлен в очередь" : "Отмена запрошена", action === "retry" ? "success" : "warning");
        await refreshJobs();
    } catch (error) { toast(error.message, "error"); }
}

async function refreshStats() {
    try {
        const [stats, system] = await Promise.all([api("/api/stats"), api("/api/system")]);
        $("#stat-total").textContent = stats.total_count;
        $("#stat-size").textContent = stats.total_size_text;
        $("#stat-breakdown").textContent = `${stats.audio_count} аудио · ${stats.video_count} видео`;
        $("#stat-path").textContent = system.download_root;
        $("#stat-path").title = system.download_root;
        $("#stat-workers").textContent = system.workers;
        $("#system-badges").innerHTML = `
            <span class="badge ${system.ffmpeg ? "ok" : "warn"}">FFmpeg</span>
            <span class="badge ${system.node ? "ok" : "warn"}">Node</span>
            <span class="badge ok">yt-dlp ${escapeHtml(system.yt_dlp_version)}</span>`;
    } catch (error) {
        $("#system-badges").innerHTML = `<span class="badge warn">Система недоступна</span>`;
    }
}

async function refreshLibrary(append = false) {
    const offset = append ? state.libraryItems.length : 0;
    const params = new URLSearchParams({
        filter: $("#library-filter").value,
        search: $("#library-search").value.trim(),
        sort: $("#library-sort").value,
        limit: "120",
        offset: String(offset),
    });
    try {
        const data = await api(`/api/library?${params}`);
        state.libraryItems = append ? [...state.libraryItems, ...data.items] : data.items;
        state.libraryTotal = data.total;
        renderLibrary(state.libraryItems);
    } catch (error) { toast(error.message, "error"); }
}

function renderLibrary(items) {
    $("#library-empty").classList.toggle("hidden", items.length > 0);
    const libraryMoreWrap = $("#library-more-wrap");
    const allItemsLoaded = items.length >= state.libraryTotal;
    libraryMoreWrap.classList.toggle("hidden", allItemsLoaded);
    libraryMoreWrap.hidden = allItemsLoaded;
    $("#library-grid").innerHTML = items.map((item) => {
        const thumbnail = item.thumbnail
            ? `<img src="/thumbs/${encodePath(item.thumbnail)}" alt="" loading="lazy">`
            : `<div class="media-placeholder">${item.media_type === "audio" ? "♫" : "▶"}</div>`;
        const playlist = item.playlist_title ? `<span title="${escapeHtml(item.playlist_title)}">${escapeHtml(item.playlist_title)}</span>` : "";
        return `<article class="media-card">
            <div class="media-cover">${thumbnail}<span class="format-chip">${escapeHtml(item.format).toUpperCase()}</span></div>
            <div class="media-body">
                <div class="media-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
                <div class="media-meta"><span>${escapeHtml(item.size_text)}</span>${playlist}</div>
                <div class="media-actions">
                    <a class="button secondary" href="/media/${encodePath(item.filename)}">Скачать</a>
                    <button class="icon-button" data-media-action="folder" data-filename="${escapeHtml(item.filename)}" title="Показать в папке" aria-label="Показать в папке">⌁</button>
                    <button class="icon-button" data-media-action="delete" data-filename="${escapeHtml(item.filename)}" data-title="${escapeHtml(item.title)}" title="Удалить" aria-label="Удалить">×</button>
                </div>
            </div>
        </article>`;
    }).join("");
}

async function mediaAction(action, filename, title = "") {
    try {
        if (action === "folder") {
            await api("/api/open-folder", { method: "POST", body: { filename } });
            return;
        }
        if (action === "delete") {
            if (!window.confirm(`Удалить «${title || filename}» с диска?`)) return;
            await api("/api/library", { method: "DELETE", body: { filename } });
            toast("Файл удалён");
            await Promise.all([refreshLibrary(), refreshStats()]);
        }
    } catch (error) { toast(error.message, "error"); }
}

function bindEvents() {
    $$(".segment").forEach((button) => button.addEventListener("click", () => updateMode(button.dataset.mode)));
    $("#format-select").addEventListener("change", onFormatChanged);
    $$("#smart-settings input, #smart-settings select, #smart-settings input, #bitrate-select, #quality-select").forEach((element) => element.addEventListener("change", saveSettings));
    $("#inspect-button").addEventListener("click", () => inspectLinks(false));
    $("#download-button").addEventListener("click", downloadAll);
    $("#download-selected-button").addEventListener("click", queueSelected);
    $("#paste-button").addEventListener("click", async () => {
        try {
            const value = await navigator.clipboard.readText();
            if (value) $("#urls-input").value = [$("#urls-input").value.trim(), value.trim()].filter(Boolean).join("\n");
            $("#urls-input").focus();
        } catch { toast("Браузер не дал доступ к буферу — вставь Ctrl+V", "warning"); }
    });
    $("#select-all-button").addEventListener("click", () => { state.selectedKeys = new Set(state.analysisItems.map((item) => item.key)); renderAnalysisItems(); });
    $("#select-none-button").addEventListener("click", () => { state.selectedKeys.clear(); renderAnalysisItems(); });
    $("#analysis-search").addEventListener("input", renderAnalysisItems);
    $("#analysis-items").addEventListener("change", (event) => {
        const index = Number(event.target.dataset.analysisIndex);
        if (!Number.isInteger(index)) return;
        const item = state.analysisItems[index];
        if (event.target.checked) state.selectedKeys.add(item.key); else state.selectedKeys.delete(item.key);
        updateSelectedCount();
    });
    $("#job-list").addEventListener("click", (event) => {
        const button = event.target.closest("[data-job-action]");
        if (button) jobAction(button.dataset.jobAction, button.dataset.jobId);
    });
    $("#clear-jobs-button").addEventListener("click", async () => {
        try {
            const data = await api("/api/jobs/completed", { method: "DELETE" });
            toast(`Убрано из списка: ${data.cleared}`);
            await refreshJobs();
        } catch (error) { toast(error.message, "error"); }
    });
    $("#library-grid").addEventListener("click", (event) => {
        const button = event.target.closest("[data-media-action]");
        if (button) mediaAction(button.dataset.mediaAction, button.dataset.filename, button.dataset.title);
    });
    let searchTimer;
    $("#library-search").addEventListener("input", () => { window.clearTimeout(searchTimer); searchTimer = window.setTimeout(refreshLibrary, 250); });
    $("#library-filter").addEventListener("change", () => refreshLibrary(false));
    $("#library-sort").addEventListener("change", () => refreshLibrary(false));
    const moreButton = $("#library-more-button");
    if (moreButton) moreButton.addEventListener("click", () => refreshLibrary(true));
    $("#reindex-button").addEventListener("click", async () => {
        const button = $("#reindex-button"); button.disabled = true;
        try { const data = await api("/api/library/reindex", { method: "POST" }); toast(`Проиндексировано файлов: ${data.indexed}`); await Promise.all([refreshLibrary(), refreshStats()]); }
        catch (error) { toast(error.message, "error"); }
        finally { button.disabled = false; }
    });
    $("#open-folder-button").addEventListener("click", () => mediaAction("folder", ""));
    document.addEventListener("keydown", (event) => {
        if (event.ctrlKey && event.key === "Enter") { event.preventDefault(); inspectLinks(false); }
        if (event.ctrlKey && event.key.toLowerCase() === "v" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) $("#urls-input").focus();
    });
}

async function init() {
    restoreSettings();
    bindEvents();
    await Promise.all([refreshStats(), refreshJobs(), refreshLibrary()]);
    window.setInterval(refreshJobs, 1000);
    window.setInterval(refreshStats, 5000);
}

document.addEventListener("DOMContentLoaded", init);
