import { api } from "./api.js";
import { fetchItems } from "./media.js";
import * as player from "./player.js";
import { emit, prefs } from "./store.js";
import { confirmDialog, openMenu, setButtonLoading, setTitle, toast } from "./ui.js";
import { $, $$, countLabel, escapeHtml, extractUrls, formatDuration, formatEta, icon, storage } from "./util.js";

const SETTINGS_KEY = "pyloader.smart.v2";
const TERMINAL = new Set(["completed", "failed", "cancelled", "skipped"]);
const MAX_ANALYSIS_ROWS = 500;
const MAX_JOB_ROWS = 300;

const formatOptions = {
    audio: [["mp3", "MP3"], ["m4a", "M4A · без перекодирования"], ["opus", "OPUS · компактный"]],
    video: [["mp4", "MP4 · совместимый"], ["mkv", "MKV · максимум качества"]],
};
const stateLabels = {
    queued: "В очереди", extracting: "Получение данных", downloading: "Скачивание",
    processing: "Обработка", completed: "Готово", skipped: "Уже в медиатеке",
    failed: "Ошибка", cancelled: "Отменено",
};

const state = {
    mode: "audio",
    analysis: null,
    analysisItems: [],
    selectedKeys: new Set(),
    jobs: new Map(),
    jobNodes: new Map(),
    version: 0,
    epoch: null,
    summary: { active: 0 },
    filter: "all",
    pollTimer: null,
    wasActive: false,
    libraryRevision: null,
};

// ---------- Settings ----------
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

function renderSmartSummary() {
    const options = currentOptions();
    const parts = [];
    if (options.skip_duplicates) parts.push("без повторов");
    if (options.organize) parts.push("папки плейлистов");
    if (options.numerate) parts.push("нумерация");
    if (state.mode === "video" && options.subtitles) parts.push(`субтитры ${options.subtitle_languages || "ru,en"}`);
    if (options.cookies_browser !== "none") parts.push(`cookies ${$("#cookies-browser").selectedOptions[0]?.textContent}`);
    if (options.proxy) parts.push("прокси");
    $("#smart-summary").textContent = parts.length ? parts.join(" · ") : "Дедупликация, папки, субтитры и доступ";
}

function saveSettings() {
    storage.set(SETTINGS_KEY, { mode: state.mode, ...currentOptions() });
    renderSmartSummary();
}

function updateMode(mode, preferredFormat = null) {
    state.mode = mode;
    $$(".segment[data-mode]").forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
    const select = $("#format-select");
    const allowed = formatOptions[mode];
    const value = allowed.some(([format]) => format === preferredFormat) ? preferredFormat : allowed[0][0];
    select.innerHTML = allowed.map(([format, label]) => `<option value="${format}">${escapeHtml(label)}</option>`).join("");
    select.value = value;
    syncFormatFields();
    $$(".video-only").forEach((node) => node.classList.toggle("hidden", mode !== "video"));
    saveSettings();
    if (state.analysis) renderAnalysisItems();
}

function syncFormatFields() {
    $("#bitrate-field").classList.toggle("hidden", state.mode !== "audio" || $("#format-select").value !== "mp3");
    $("#quality-field").classList.toggle("hidden", state.mode !== "video");
}

function restoreSettings() {
    const saved = storage.get(SETTINGS_KEY, {});
    const values = {
        "quality-select": saved.quality,
        "bitrate-select": saved.bitrate,
        "cookies-browser": saved.cookies_browser,
        "subtitle-languages": saved.subtitle_languages,
        "proxy-input": saved.proxy,
    };
    for (const [id, value] of Object.entries(values)) {
        if (value !== undefined && value !== null) $(`#${id}`).value = value;
    }
    const checks = {
        "skip-duplicates": saved.skip_duplicates ?? true,
        "organize-playlists": saved.organize ?? false,
        "numerate-playlists": saved.numerate ?? false,
        "subtitles-enabled": saved.subtitles ?? false,
        "auto-subtitles": saved.auto_subtitles ?? false,
    };
    for (const [id, value] of Object.entries(checks)) $(`#${id}`).checked = Boolean(value);
    updateMode(saved.mode === "video" ? "video" : "audio", saved.format);
}

// ---------- URLs ----------
function parseUrls() {
    return extractUrls($("#urls-input").value);
}

function renderUrlCount() {
    const count = parseUrls().length;
    const chip = $("#url-count");
    chip.classList.toggle("hidden", count === 0);
    chip.textContent = countLabel(count, "ссылка", "ссылки", "ссылок");
}

/** Append links (from paste, drop, bookmarklet) to the box, skipping ones already there. */
export function addUrls(urls, { analyze = false } = {}) {
    const existing = new Set(parseUrls());
    const fresh = urls.filter((url) => !existing.has(url));
    const area = $("#urls-input");
    if (fresh.length) area.value = [area.value.trim(), ...fresh].filter(Boolean).join("\n");
    renderUrlCount();
    if (location.hash !== "#/download") location.hash = "#/download";
    if (!fresh.length) {
        toast(urls.length ? "Эти ссылки уже в списке" : "Ссылок не найдено", "warning");
        return;
    }
    toast(`Добавлено ${countLabel(fresh.length, "ссылка", "ссылки", "ссылок")}`);
    if (analyze) inspectLinks();
}

// ---------- Analysis ----------
function inLibrary(item) {
    return (item.library_formats || []).includes($("#format-select").value);
}

function defaultSelection() {
    const skip = $("#skip-duplicates").checked;
    return new Set(state.analysisItems.filter((item) => !(skip && inLibrary(item))).map((item) => item.key));
}

export async function inspectLinks({ queueAfter = false } = {}) {
    const urls = parseUrls();
    if (!urls.length) {
        toast("Добавь хотя бы одну ссылку", "warning");
        $("#urls-input").focus();
        return null;
    }
    const button = $("#inspect-button");
    setButtonLoading(button, true, "Анализирую…");
    $("#download-button").disabled = true;
    $("#analysis-panel").classList.remove("hidden");
    $("#analysis-summary").innerHTML = "";
    $("#source-list").innerHTML = "";
    $("#analysis-items").innerHTML = `<div class="empty-state"><span class="spinner"></span><strong>Читаю ${countLabel(urls.length, "ссылку", "ссылки", "ссылок")}</strong><p>Большие каналы и плейлисты могут занять до минуты.</p></div>`;
    try {
        const data = await api("/api/inspect", {
            method: "POST",
            body: { urls, cookies_browser: $("#cookies-browser").value, proxy: $("#proxy-input").value.trim() },
        });
        state.analysis = data;
        state.analysisItems = data.items || [];
        state.selectedKeys = defaultSelection();
        renderAnalysis();
        if (data.errors?.length) toast(`Не удалось разобрать: ${countLabel(data.errors.length, "ссылку", "ссылки", "ссылок")}`, "warning", 6500);
        if (!state.analysisItems.length) toast("Доступных материалов не найдено", "error");
        if (queueAfter && state.selectedKeys.size) await queueSelected();
        else if (queueAfter && state.analysisItems.length) toast("Всё найденное уже есть в медиатеке", "warning");
        return data;
    } catch (error) {
        $("#analysis-items").innerHTML = `<div class="empty-state"><span class="empty-icon">${icon("alert")}</span><strong>Анализ не удался</strong><p>${escapeHtml(error.message)}</p></div>`;
        toast(error.message, "error");
        return null;
    } finally {
        setButtonLoading(button, false);
        $("#download-button").disabled = false;
    }
}

function renderAnalysis() {
    const data = state.analysis;
    if (!data) return;
    $("#analysis-panel").classList.remove("hidden");
    $("#analysis-title").textContent = data.sources.length === 1 ? data.sources[0].title : `Источников: ${data.sources.length}`;
    const existing = state.analysisItems.filter(inLibrary).length;
    $("#analysis-summary").innerHTML = [
        [data.raw_count, "найдено позиций", ""],
        [data.unique_count, "уникальных", "accent"],
        [data.duplicate_count, "повторов убрано", ""],
        [existing, "уже в медиатеке", ""],
        [formatDuration(data.total_duration), "общая длительность", ""],
    ].map(([value, label, cls]) => `<div class="summary-cell ${cls}"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`).join("");
    $("#source-list").innerHTML = [
        ...data.sources.map((source) => `<div class="source-chip" title="${escapeHtml(source.title)}">
            <strong>${escapeHtml(source.title)}</strong><small>${escapeHtml(source.uploader)} · ${source.count}</small></div>`),
        ...data.errors.map((error) => `<div class="source-chip error" title="${escapeHtml(error.error)}">
            <strong>${escapeHtml(error.url)}</strong><small>${escapeHtml(error.error)}</small></div>`),
    ].join("");
    renderAnalysisItems();
}

function renderAnalysisItems() {
    if (!state.analysis) return;
    const query = $("#analysis-search").value.trim().toLocaleLowerCase();
    const visible = state.analysisItems
        .map((item, index) => ({ item, index }))
        .filter(({ item }) => !query || `${item.title || ""} ${item.playlist_title || ""}`.toLocaleLowerCase().includes(query));
    const shown = visible.slice(0, MAX_ANALYSIS_ROWS);
    const format = $("#format-select").value.toUpperCase();
    $("#analysis-items").innerHTML = shown.length ? shown.map(({ item, index }) => {
        const checked = state.selectedKeys.has(item.key);
        const thumb = item.thumbnail ? `<img class="analysis-thumb" src="${escapeHtml(item.thumbnail)}" alt="" loading="lazy">` : `<div class="analysis-thumb"></div>`;
        const formats = item.library_formats || [];
        const badge = inLibrary(item)
            ? `<span class="pill ok">${icon("check")}Уже есть ${escapeHtml(format)}</span>`
            : formats.length
                ? `<span class="pill muted" title="В медиатеке в другом формате">Есть ${escapeHtml(formats.join(", ").toUpperCase())}</span>`
                : ["private", "subscriber_only", "premium_only", "needs_auth"].includes(item.availability)
                    ? `<span class="pill warn" title="Может понадобиться выбрать cookies браузера">Нужен вход</span>`
                    : "";
        return `<label class="analysis-item ${checked ? "" : "unchecked"}">
            <span class="check"><input type="checkbox" data-analysis-index="${index}" ${checked ? "checked" : ""}><span></span></span>
            ${thumb}
            <span class="min-w-0"><strong title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</strong><small>${escapeHtml(item.playlist_title || "Видео")} · ${formatDuration(item.duration)}</small></span>
            ${badge}
        </label>`;
    }).join("") + (visible.length > shown.length ? `<div class="list-note">Показаны первые ${MAX_ANALYSIS_ROWS} из ${visible.length} — выбор применяется ко всем, уточни поиск, чтобы увидеть остальные.</div>` : "")
        : `<div class="empty-state"><strong>Ничего не найдено</strong><p>Попробуй другой запрос.</p></div>`;
    updateSelectedCount();
}

function updateSelectedCount() {
    const count = state.selectedKeys.size;
    $("#selected-count").textContent = `Выбрано ${count} из ${state.analysisItems.length}`;
    $("#download-selected-button span").textContent = count ? `Скачать выбранное · ${count}` : "Ничего не выбрано";
    $("#download-selected-button").disabled = count === 0;
    const errors = state.analysis?.errors?.length || 0;
    $("#analysis-note").textContent = errors ? `Ошибок источников: ${errors}` : "Повторы уже исключены по YouTube ID";
}

async function queueSelected() {
    const selected = state.analysisItems.filter((item) => state.selectedKeys.has(item.key));
    if (!selected.length) { toast("Выбери хотя бы один элемент", "warning"); return; }
    const button = $("#download-selected-button");
    setButtonLoading(button, true, "Добавляю…");
    try {
        const data = await api("/api/jobs", { method: "POST", body: { items: selected, options: currentOptions() } });
        const skipped = data.skipped_active + data.skipped_library + data.skipped_batch;
        if (data.added) toast(`В очередь: ${countLabel(data.added, "загрузка", "загрузки", "загрузок")}`);
        if (skipped) toast(`Пропущено без повторной загрузки: ${skipped}`, "warning");
        saveSettings();
        pollJobs();
    } catch (error) {
        toast(error.message, "error");
    } finally {
        setButtonLoading(button, false);
        updateSelectedCount();
    }
}

async function downloadAll() {
    if (state.analysis && state.analysisItems.length) {
        state.selectedKeys = defaultSelection();
        renderAnalysisItems();
        if (!state.selectedKeys.size) { toast("Всё найденное уже есть в медиатеке", "warning"); return; }
        await queueSelected();
    } else {
        await inspectLinks({ queueAfter: true });
    }
}

// ---------- Queue ----------
function visibleJob(job) {
    if (state.filter === "active") return !TERMINAL.has(job.state);
    if (state.filter === "done") return job.state === "completed" || job.state === "skipped";
    if (state.filter === "failed") return job.state === "failed" || job.state === "cancelled";
    return true;
}

function jobHtml(job) {
    const active = !TERMINAL.has(job.state);
    const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
    const label = job.state === "downloading" || job.state === "processing" ? job.status : stateLabels[job.state] || job.status;
    const meta = [label, job.speed_text, job.eta ? `ещё ${formatEta(job.eta)}` : "", job.playlist_title].filter(Boolean).join(" · ");
    const thumb = job.thumbnail ? `<img src="${escapeHtml(job.thumbnail)}" alt="" loading="lazy">` : "";
    let actions = "";
    if (active) {
        actions = `<button class="icon-button" type="button" data-job-action="cancel" title="Отменить" aria-label="Отменить">${icon("x")}</button>`;
    } else {
        if (job.state === "completed" && job.media_id) {
            actions += `<button class="icon-button" type="button" data-job-action="play" title="Воспроизвести" aria-label="Воспроизвести">${icon("play")}</button>`;
        }
        if (job.state === "failed" || job.state === "cancelled") {
            actions += `<button class="icon-button" type="button" data-job-action="retry" title="Повторить" aria-label="Повторить">${icon("refresh")}</button>`;
        }
        actions += `<button class="icon-button" type="button" data-job-action="remove" title="Убрать из списка" aria-label="Убрать из списка">${icon("x")}</button>`;
    }
    const percent = job.state === "queued" ? "" : `${progress.toFixed(job.state === "completed" ? 0 : 1)}%`;
    return `<div class="job-thumb">${thumb}<b>${escapeHtml(String(job.format || "").toUpperCase())}</b></div>
        <div class="job-main">
            <div class="job-title" title="${escapeHtml(job.title)}">${escapeHtml(job.title)}</div>
            <div class="job-meta"><span>${escapeHtml(meta)}</span><span>${percent}</span></div>
            <div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div>
            ${job.error ? `<div class="job-error" title="${escapeHtml(job.error)}">${escapeHtml(job.error)}</div>` : ""}
        </div>
        <div class="job-actions">${actions}</div>`;
}

function sortedJobs() {
    return [...state.jobs.values()].sort((a, b) => (b.batch - a.batch) || (a.seq - b.seq));
}

function renderJobs(changedIds = null) {
    const list = $("#job-list");
    const all = sortedJobs();
    const visible = all.filter(visibleJob);
    const shown = visible.slice(0, MAX_JOB_ROWS);
    const keep = new Set(shown.map((job) => job.id));
    for (const [id, node] of state.jobNodes) {
        if (!keep.has(id)) { node.remove(); state.jobNodes.delete(id); }
    }
    let previous = null;
    for (const job of shown) {
        let node = state.jobNodes.get(job.id);
        if (!node) {
            node = document.createElement("article");
            node.dataset.jobId = job.id;
            state.jobNodes.set(job.id, node);
            node.dataset.v = "";
        }
        if (node.dataset.v !== String(job._v) || !changedIds || changedIds.has(job.id)) {
            node.className = `job-card ${job.state}`;
            node.innerHTML = jobHtml(job);
            node.dataset.v = String(job._v);
        }
        const expected = previous ? previous.nextElementSibling : list.firstElementChild;
        if (expected !== node) list.insertBefore(node, expected);
        previous = node;
    }
    $("#job-list-note").classList.toggle("hidden", visible.length <= shown.length);
    $("#job-list-note").textContent = `Показаны ${shown.length} из ${visible.length}`;
    $("#queue-empty").classList.toggle("hidden", visible.length > 0);
    $("#queue-empty strong").textContent = all.length ? "Здесь пусто" : "Очередь пуста";
    $("#queue-empty p").textContent = all.length ? "В этой вкладке нет задач." : "Добавь одну ссылку или целую коллекцию.";
    renderSummary(all);
}

function renderSummary(all) {
    const summary = state.summary;
    const counts = {
        all: summary.total || 0,
        active: summary.active || 0,
        done: (summary.completed || 0) + (summary.skipped || 0),
        failed: (summary.failed || 0) + (summary.cancelled || 0),
    };
    $$("#job-tabs [data-job-filter]").forEach((tab) => {
        tab.classList.toggle("active", tab.dataset.jobFilter === state.filter);
        tab.querySelector("span").textContent = counts[tab.dataset.jobFilter] || "";
    });
    $("#active-count").textContent = summary.active || 0;
    $("#retry-failed-button").classList.toggle("hidden", !summary.failed);
    const badge = $("#nav-download-badge");
    badge.textContent = summary.active || 0;
    badge.classList.toggle("hidden", !summary.active);

    // Overall progress across every batch that still has unfinished jobs.
    const liveBatches = new Set(all.filter((job) => !TERMINAL.has(job.state)).map((job) => job.batch));
    const live = all.filter((job) => liveBatches.has(job.batch));
    const overview = $("#queue-overview");
    overview.classList.toggle("hidden", !live.length);
    let percent = null;
    if (live.length) {
        percent = Math.round(live.reduce((sum, job) => sum + (TERMINAL.has(job.state) ? 100 : Number(job.progress || 0)), 0) / live.length);
        const done = live.filter((job) => TERMINAL.has(job.state)).length;
        $("#queue-overview-title").textContent = `${done} из ${live.length} · ${percent}%`;
        $("#queue-overview-meta").textContent = [
            summary.running ? `качается ${summary.running}` : "",
            summary.queued ? `ждут ${summary.queued}` : "",
        ].filter(Boolean).join(" · ");
        $("#queue-overview-fill").style.width = `${percent}%`;
    }
    setTitle({ jobs: summary.active || 0, progress: summary.active ? percent : null });
}

function notifyDrained() {
    const summary = state.summary;
    if (!prefs.notify || !document.hidden || !("Notification" in window) || Notification.permission !== "granted") return;
    const parts = [`готово ${summary.completed || 0}`];
    if (summary.failed) parts.push(`ошибок ${summary.failed}`);
    try { new Notification("PyLoader: загрузки завершены", { body: parts.join(", "), tag: "pyloader-queue" }); } catch { /* ignored */ }
}

export async function pollJobs() {
    window.clearTimeout(state.pollTimer);
    try {
        const query = new URLSearchParams({ since: String(state.version), epoch: state.epoch || "" });
        const data = await api(`/api/jobs?${query}`);
        if (data.full) state.jobs.clear();
        const changed = new Set();
        for (const job of data.jobs) {
            state.jobs.set(job.id, { ...job, _v: `${data.version}:${job.updated_at}:${job.progress}:${job.state}` });
            changed.add(job.id);
        }
        state.version = data.version;
        state.epoch = data.epoch;
        state.summary = data.summary;
        if (data.full || changed.size) renderJobs(data.full ? null : changed);
        else renderSummary(sortedJobs());
        const active = data.summary.active > 0;
        if (state.wasActive && !active) notifyDrained();
        state.wasActive = active;
        if (data.library_revision !== state.libraryRevision) {
            state.libraryRevision = data.library_revision;
            emit("library:revision", data.library_revision);
        }
    } catch (error) {
        console.error("Job refresh failed", error);
    }
    const delay = state.summary.active ? 1000 : document.hidden ? 6000 : 3000;
    state.pollTimer = window.setTimeout(pollJobs, delay);
}

async function jobAction(action, jobId) {
    const job = state.jobs.get(jobId);
    try {
        if (action === "play" && job?.media_id) {
            player.playItems(await fetchItems([job.media_id]));
            return;
        }
        if (action === "remove") {
            await api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
        } else {
            await api(`/api/jobs/${encodeURIComponent(jobId)}/${action}`, { method: "POST" });
            if (action === "retry") toast("Повтор добавлен в очередь");
        }
        pollJobs();
    } catch (error) { toast(error.message, "error"); }
}

async function queueCommand(command) {
    try {
        if (command === "cancel-all") {
            if (!state.summary.active) { toast("Нечего отменять", "warning"); return; }
            const ok = await confirmDialog({
                title: "Отменить все загрузки?",
                message: `Будет остановлено ${countLabel(state.summary.active, "задача", "задачи", "задач")}. Уже скачанные файлы останутся.`,
                confirmLabel: "Отменить всё",
                danger: true,
            });
            if (!ok) return;
            const data = await api("/api/jobs/cancel-all", { method: "POST" });
            toast(`Отмена запрошена: ${data.cancelled}`, "warning");
        }
        if (command === "retry-failed") {
            const data = await api("/api/jobs/retry-failed", { method: "POST" });
            toast(data.added ? `Повторно в очереди: ${data.added}` : "Нечего повторять", data.added ? "success" : "warning");
        }
        if (command === "clear") {
            const data = await api("/api/jobs/completed", { method: "DELETE" });
            toast(`Убрано из списка: ${data.cleared}`);
        }
        if (command === "folder") await api("/api/open-folder", { method: "POST", body: {} });
        pollJobs();
    } catch (error) { toast(error.message, "error"); }
}

export function init() {
    restoreSettings();
    renderUrlCount();

    $$(".segment[data-mode]").forEach((button) => button.addEventListener("click", () => updateMode(button.dataset.mode, $("#format-select").value)));
    $("#format-select").addEventListener("change", () => { syncFormatFields(); saveSettings(); if (state.analysis) renderAnalysis(); });
    $$("#smart-settings input, #smart-settings select, #bitrate-select, #quality-select").forEach((node) => node.addEventListener("change", saveSettings));
    $("#skip-duplicates").addEventListener("change", () => { if (state.analysis) { state.selectedKeys = defaultSelection(); renderAnalysisItems(); } });
    $("#urls-input").addEventListener("input", renderUrlCount);
    $("#inspect-button").addEventListener("click", () => inspectLinks());
    $("#download-button").addEventListener("click", downloadAll);
    $("#download-selected-button").addEventListener("click", queueSelected);
    $("#clear-urls-button").addEventListener("click", () => {
        $("#urls-input").value = "";
        renderUrlCount();
        $("#urls-input").focus();
    });
    $("#paste-button").addEventListener("click", async () => {
        try {
            const text = await navigator.clipboard.readText();
            const urls = extractUrls(text);
            if (urls.length) addUrls(urls); else toast("В буфере нет ссылок", "warning");
        } catch {
            toast("Браузер не дал доступ к буферу — вставь через Ctrl+V", "warning");
            $("#urls-input").focus();
        }
    });
    $("#select-all-button").addEventListener("click", () => { state.selectedKeys = new Set(state.analysisItems.map((item) => item.key)); renderAnalysisItems(); });
    $("#select-none-button").addEventListener("click", () => { state.selectedKeys.clear(); renderAnalysisItems(); });
    $("#select-new-button").addEventListener("click", () => {
        state.selectedKeys = new Set(state.analysisItems.filter((item) => !inLibrary(item)).map((item) => item.key));
        renderAnalysisItems();
    });
    $("#close-analysis-button").addEventListener("click", () => {
        state.analysis = null;
        state.analysisItems = [];
        state.selectedKeys.clear();
        $("#analysis-panel").classList.add("hidden");
    });
    $("#analysis-search").addEventListener("input", renderAnalysisItems);
    $("#analysis-items").addEventListener("change", (event) => {
        const index = Number(event.target.dataset.analysisIndex);
        if (!Number.isInteger(index)) return;
        const item = state.analysisItems[index];
        if (event.target.checked) state.selectedKeys.add(item.key); else state.selectedKeys.delete(item.key);
        event.target.closest(".analysis-item")?.classList.toggle("unchecked", !event.target.checked);
        updateSelectedCount();
    });

    $("#job-list").addEventListener("click", (event) => {
        const button = event.target.closest("[data-job-action]");
        const card = event.target.closest("[data-job-id]");
        if (button && card) jobAction(button.dataset.jobAction, card.dataset.jobId);
    });
    $("#job-tabs").addEventListener("click", (event) => {
        const tab = event.target.closest("[data-job-filter]");
        if (!tab) return;
        state.filter = tab.dataset.jobFilter;
        renderJobs();
    });
    $("#retry-failed-button").addEventListener("click", () => queueCommand("retry-failed"));
    $("#queue-menu-button").addEventListener("click", (event) => openMenu([
        { label: "Повторить все ошибки", icon: "refresh", action: () => queueCommand("retry-failed"), disabled: !state.summary.failed },
        { label: "Убрать завершённые", icon: "check", action: () => queueCommand("clear") },
        { label: "Открыть папку загрузок", icon: "folder", action: () => queueCommand("folder") },
        "-",
        { label: "Отменить все загрузки", icon: "stop", danger: true, action: () => queueCommand("cancel-all"), disabled: !state.summary.active },
    ], { anchor: event.currentTarget }));

    document.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            inspectLinks();
        }
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) pollJobs(); });
}
