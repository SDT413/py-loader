import { api } from "./api.js";
import * as downloads from "./downloads.js";
import * as library from "./library.js";
import * as phone from "./phone.js";
import * as player from "./player.js";
import * as playlists from "./playlists.js";
import * as settings from "./settings.js";
import { emit, prefs, shared } from "./store.js";
import { closeMenu, toast } from "./ui.js";
import { $, $$, extractUrls, isTyping } from "./util.js";
import { DRAG_IDS } from "./tracklist.js";

const VIEWS = ["download", "library", "playlists", "phone", "settings"];
let currentView = null;

function parseHash() {
    const [view, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
    const param = rest.length ? decodeURIComponent(rest.join("/")) : null;
    return { view: view || "download", param };
}

function leave(view) {
    if (view === "library") library.leave();
    if (view === "playlist") playlists.leave();
    if (view === "phone") phone.leave();
}

function route() {
    let { view, param } = parseHash();
    if (view === "collection" && param) { view = "playlist"; param = { kind: "collection", key: param }; }
    else if (view === "playlist" && param) param = { kind: "playlist", key: param };
    else if (![...VIEWS, "playlist"].includes(view) || view === "playlist") { view = "download"; param = null; }

    if (currentView && currentView !== view) leave(currentView);
    currentView = view;
    shared.route = { view, param: param?.kind === "playlist" ? param.key : param?.key ?? null };
    $$(".view").forEach((section) => section.classList.toggle("active", section.dataset.view === view));
    const nav = view === "playlist" ? (param.kind === "playlist" ? null : "playlists") : view;
    $$("[data-nav]").forEach((link) => link.classList.toggle("active", link.dataset.nav === nav));
    closeMenu();
    $("#main").scrollTop = 0;

    if (view === "library") library.enter();
    if (view === "playlists") playlists.enterOverview();
    if (view === "playlist") playlists.enterDetail(param.kind, param.key);
    if (view === "phone") phone.enter();
    emit("route", shared.route);
}

async function refreshSystem() {
    try {
        const [stats, system] = await Promise.all([api("/api/stats"), api("/api/system")]);
        shared.stats = stats;
        shared.system = system;
        $("#nav-library-count").textContent = stats.total_count || "";
        const used = system.disk_total ? 1 - system.disk_free / system.disk_total : 0;
        const fill = $("#disk-fill");
        fill.style.width = `${Math.round(used * 100)}%`;
        fill.classList.toggle("warn", used > 0.85);
        fill.classList.toggle("danger", used > 0.95);
        $("#disk-text").textContent = `Свободно ${system.disk_free_text}`;
        $("#system-dots").innerHTML = `
            <span class="dot-badge ${system.ffmpeg ? "ok" : "warn"}" title="${system.ffmpeg ? "FFmpeg найден" : "FFmpeg не найден"}">FFmpeg</span>
            <span class="dot-badge ${system.node ? "ok" : "warn"}" title="${system.node ? "Node.js найден" : "Node.js не найден"}">Node</span>
            <span class="dot-badge ok" title="Версия yt-dlp">yt-dlp ${system.yt_dlp_version}</span>`;
        emit("system", system);
        emit("stats", stats);
        if (stats.revision !== shared.libraryRevision) {
            shared.libraryRevision = stats.revision;
            emit("library:revision", stats.revision);
        }
    } catch {
        $("#system-dots").innerHTML = `<span class="dot-badge warn">Сервер недоступен</span>`;
    }
}

function bindGlobalKeys() {
    document.addEventListener("keydown", (event) => {
        if (event.defaultPrevented || $("#modal").open) return;
        if (event.key === "Escape") {
            if (player.closeOverlays()) event.preventDefault();
            return;
        }
        if (isTyping() || event.altKey) return;
        const ctrl = event.ctrlKey || event.metaKey;
        if (!ctrl && /^[1-5]$/.test(event.key)) {
            location.hash = `#/${VIEWS[Number(event.key) - 1]}`;
            return;
        }
        if (ctrl) return;
        if (event.key === "/") {
            event.preventDefault();
            if (currentView !== "library") location.hash = "#/library";
            window.setTimeout(library.focusSearch, 30);
        } else if (event.key === " " && !event.target.closest?.("button, a, input, select, summary, label, [role='menuitem']")) {
            event.preventDefault();
            player.toggle();
        } else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
            if (!player.current() || event.target.closest?.("input, select, [role='tablist']")) return;
            const forward = event.key === "ArrowRight";
            if (event.shiftKey) { if (forward) player.next(); else player.prev(); }
            else player.seekBy(forward ? 5 : -5);
            event.preventDefault();
        } else if (event.key.toLowerCase() === "m" || event.key.toLowerCase() === "ь") {
            player.toggleMute();
        }
    });

    // Ctrl+V anywhere outside an input pastes links into the download box.
    document.addEventListener("paste", (event) => {
        if (isTyping(event.target)) return;
        const urls = extractUrls(event.clipboardData?.getData("text") || "");
        if (!urls.length) return;
        event.preventDefault();
        downloads.addUrls(urls, { analyze: prefs.autoAnalyze });
    });
}

function bindDropZone() {
    const overlay = $("#drop-overlay");
    let depth = 0;
    const isExternal = (event) => {
        const types = [...(event.dataTransfer?.types || [])];
        return !types.includes(DRAG_IDS) && (types.includes("text/uri-list") || types.includes("text/plain"));
    };
    window.addEventListener("dragenter", (event) => {
        if (!isExternal(event)) return;
        depth += 1;
        overlay.hidden = false;
    });
    window.addEventListener("dragleave", (event) => {
        if (!isExternal(event)) return;
        depth = Math.max(0, depth - 1);
        if (!depth) overlay.hidden = true;
    });
    window.addEventListener("dragover", (event) => {
        if (!isExternal(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
    });
    window.addEventListener("drop", (event) => {
        if (!isExternal(event)) return;
        event.preventDefault();
        depth = 0;
        overlay.hidden = true;
        const text = event.dataTransfer.getData("text/uri-list") || event.dataTransfer.getData("text/plain");
        const urls = extractUrls(text);
        if (urls.length) downloads.addUrls(urls, { analyze: prefs.autoAnalyze });
        else toast("В перетащенном нет ссылок", "warning");
    });
}

function consumeUrlParam() {
    const params = new URLSearchParams(location.search);
    const url = params.get("url");
    if (!url) return;
    history.replaceState(null, "", `/${location.hash || "#/download"}`);
    const urls = extractUrls(url);
    if (urls.length) downloads.addUrls(urls, { analyze: true });
}

async function init() {
    window.name = "pyloader";
    settings.init();
    player.init();
    downloads.init();
    library.init();
    playlists.init();
    phone.init();
    bindGlobalKeys();
    bindDropZone();
    window.addEventListener("hashchange", route);
    route();
    consumeUrlParam();
    await Promise.all([refreshSystem(), playlists.loadPlaylists(), phone.refreshStatus()]);
    downloads.pollJobs();
    window.setInterval(() => { if (!document.hidden) refreshSystem(); }, 10000);
}

init();
