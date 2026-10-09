import { emit, on, prefs } from "./store.js";
import { fetchItems, setFavorite } from "./media.js";
import { coverHtml } from "./render.js";
import { setTitle, toast } from "./ui.js";
import { $, escapeHtml, formatClock, icon, shuffleArray, storage, streamUrl, thumbUrl } from "./util.js";

const STORE_KEY = "pyloader.player.v1";
const audio = $("#audio-element");
const video = $("#video-element");

const state = {
    queue: [],
    index: -1,
    order: [],
    orderPos: -1,
    shuffle: false,
    repeat: "off",
    volume: 0.8,
    muted: false,
    element: audio,
    seeking: false,
    lastSave: 0,
};

export const current = () => state.queue[state.index] || null;
export const isPlaying = () => Boolean(current()) && !state.element.paused && !state.element.ended;

function rebuildOrder() {
    const indexes = state.queue.map((_, index) => index);
    if (state.shuffle) {
        const rest = shuffleArray(indexes.filter((index) => index !== state.index));
        state.order = state.index >= 0 ? [state.index, ...rest] : rest;
        state.orderPos = state.index >= 0 ? 0 : -1;
    } else {
        state.order = indexes;
        state.orderPos = state.index;
    }
}

function persist(force = false) {
    if (!force && Date.now() - state.lastSave < 4000) return;
    state.lastSave = Date.now();
    storage.set(STORE_KEY, {
        ids: state.queue.map((item) => item.id),
        index: state.index,
        position: state.element.currentTime || 0,
        shuffle: state.shuffle,
        repeat: state.repeat,
        volume: state.volume,
        muted: state.muted,
    });
}

function setRangeValue(input, fraction) {
    const value = Math.max(0, Math.min(1, fraction || 0));
    input.value = String(Math.round(value * Number(input.max)));
    input.style.setProperty("--value", `${value * 100}%`);
}

function load(index, { autoplay = true, position = 0 } = {}) {
    const item = state.queue[index];
    if (!item) return;
    state.index = index;
    state.orderPos = state.order.indexOf(index);
    const element = item.media_type === "video" ? video : audio;
    if (element !== state.element) {
        state.element.pause();
        state.element.removeAttribute("src");
        state.element.load();
        state.element = element;
    }
    element.src = streamUrl(item);
    element.volume = state.volume;
    element.muted = state.muted;
    if (position) element.addEventListener("loadedmetadata", () => { element.currentTime = position; }, { once: true });
    if (item.media_type === "video" && autoplay) showStage(); else if (item.media_type !== "video") hideStage(false);
    if (autoplay) {
        element.play().catch((error) => {
            // Unsupported formats are reported once by the element's "error" handler.
            if (error.name === "NotAllowedError") toast("Браузер не дал начать воспроизведение — нажми ▶", "warning");
        });
    }
    renderTrack();
    renderQueue();
    updateMediaSession();
    persist(true);
    emit("player:track", item);
}

// ---------- Public API ----------
export function playItems(items, startIndex = 0, { shuffle = null } = {}) {
    const playable = items.filter((item) => item && item.filename);
    if (!playable.length) { toast("Нечего воспроизводить", "warning"); return; }
    state.queue = playable;
    if (shuffle !== null) state.shuffle = shuffle;
    state.index = state.shuffle && shuffle ? Math.floor(Math.random() * playable.length) : Math.max(0, Math.min(startIndex, playable.length - 1));
    rebuildOrder();
    renderModes();
    load(state.index);
}

export async function playIds(ids, options = {}) {
    try { playItems(await fetchItems(ids), 0, options); }
    catch (error) { toast(error.message, "error"); }
}

function insertAt(position, items) {
    const fresh = items.filter((item) => item && item.filename);
    if (!fresh.length) return 0;
    state.queue.splice(position, 0, ...fresh);
    const count = fresh.length;
    state.order = state.order.map((index) => (index >= position ? index + count : index));
    if (state.index >= position) state.index += count;
    const added = fresh.map((_, offset) => position + offset);
    const insertPos = state.orderPos >= 0 ? state.orderPos + 1 : state.order.length;
    state.order.splice(insertPos, 0, ...added);
    state.orderPos = state.order.indexOf(state.index);
    return count;
}

export function playNext(items) {
    if (!current()) { playItems(items); return; }
    const count = insertAt(state.index + 1, items);
    renderQueue();
    persist(true);
    if (count) toast(count > 1 ? `Сыграет следующими: ${count}` : "Сыграет следующим");
}

export function addToQueue(items) {
    if (!current()) { playItems(items); return; }
    const count = insertAt(state.queue.length, items);
    renderQueue();
    persist(true);
    if (count) toast(`В очереди: +${count}`);
}

export function removeAt(queueIndex) {
    if (queueIndex < 0 || queueIndex >= state.queue.length) return;
    const wasCurrent = queueIndex === state.index;
    state.queue.splice(queueIndex, 1);
    state.order = state.order.filter((index) => index !== queueIndex).map((index) => (index > queueIndex ? index - 1 : index));
    if (state.index > queueIndex) state.index -= 1;
    if (wasCurrent) {
        if (!state.queue.length) { stop(); return; }
        const nextPos = Math.min(state.orderPos, state.order.length - 1);
        load(state.order[nextPos], { autoplay: isPlaying() || !state.element.paused });
        return;
    }
    state.orderPos = state.order.indexOf(state.index);
    renderQueue();
    persist(true);
}

export function stop() {
    state.element.pause();
    state.element.removeAttribute("src");
    state.element.load();
    state.queue = [];
    state.order = [];
    state.index = -1;
    state.orderPos = -1;
    hideStage(false);
    renderTrack();
    renderQueue();
    persist(true);
    emit("player:track", null);
}

export function toggle() {
    const item = current();
    if (!item) { emit("player:empty-play"); return; }
    if (state.element.paused) {
        if (item.media_type === "video") showStage();
        state.element.play().catch(() => {});
    } else {
        state.element.pause();
    }
}

export function next({ auto = false } = {}) {
    if (!state.queue.length) return;
    if (auto && state.repeat === "one") {
        state.element.currentTime = 0;
        state.element.play().catch(() => {});
        return;
    }
    let pos = state.orderPos + 1;
    if (pos >= state.order.length) {
        if (auto && state.repeat === "off") {
            state.element.pause();
            state.element.currentTime = 0;
            renderTrack();
            return;
        }
        if (state.shuffle) {
            state.order = shuffleArray(state.queue.map((_, index) => index));
        }
        pos = 0;
    }
    load(state.order[pos]);
}

export function prev() {
    if (!state.queue.length) return;
    if (state.element.currentTime > 3) { state.element.currentTime = 0; return; }
    let pos = state.orderPos - 1;
    if (pos < 0) pos = state.repeat === "all" ? state.order.length - 1 : 0;
    load(state.order[pos]);
}

export function seekBy(seconds) {
    if (!current() || !Number.isFinite(state.element.duration)) return;
    state.element.currentTime = Math.max(0, Math.min(state.element.duration, state.element.currentTime + seconds));
}

export function toggleMute() {
    state.muted = !state.muted;
    state.element.muted = state.muted;
    renderVolume();
    persist(true);
}

function setVolume(value) {
    state.volume = Math.max(0, Math.min(1, value));
    state.muted = state.volume === 0;
    state.element.volume = state.volume;
    state.element.muted = state.muted;
    renderVolume();
    persist(true);
}

function toggleShuffle() {
    state.shuffle = !state.shuffle;
    rebuildOrder();
    renderModes();
    renderQueue();
    persist(true);
}

function cycleRepeat() {
    state.repeat = { off: "all", all: "one", one: "off" }[state.repeat];
    renderModes();
    persist(true);
}

// ---------- Video stage ----------
function showStage() {
    const item = current();
    if (!item || item.media_type !== "video") return;
    $("#video-stage").hidden = false;
    $("#video-stage-title").textContent = item.title;
}

function hideStage(pause = true) {
    const stage = $("#video-stage");
    if (stage.hidden) return;
    stage.hidden = true;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    if (pause && state.element === video) video.pause();
}

export function closeOverlays() {
    if (!$("#video-stage").hidden) { hideStage(true); return true; }
    if (!$("#play-queue").hidden) { toggleQueue(false); return true; }
    return false;
}

// ---------- Rendering ----------
function renderTrack() {
    const item = current();
    const player = $("#player");
    player.classList.toggle("is-empty", !item);
    const cover = $("#player-cover");
    cover.classList.toggle("video", item?.media_type === "video");
    cover.innerHTML = item && thumbUrl(item)
        ? `<img src="${thumbUrl(item)}" alt="">`
        : `<span class="player-cover-placeholder">${icon(item?.media_type === "video" ? "film" : "library")}</span>`;
    $("#player-title").textContent = item ? item.title : "Ничего не играет";
    $("#player-title").title = item ? item.title : "";
    $("#player-artist").textContent = item ? [item.artist, item.playlist_title].filter(Boolean).join(" · ") || (item.media_type === "video" ? "Видео" : "Аудио") : "Выбери трек в медиатеке";
    const fav = $("#player-fav");
    fav.disabled = !item;
    fav.classList.toggle("is-fav", Boolean(item?.favorite));
    renderPlayState();
    if (!item) {
        $("#player-time").textContent = "0:00";
        $("#player-duration").textContent = "0:00";
        setRangeValue($("#player-seek"), 0);
    }
}

function renderPlayState() {
    const playing = isPlaying();
    const button = $("#player-toggle");
    button.innerHTML = icon(playing ? "pause" : "play");
    button.setAttribute("aria-label", playing ? "Пауза" : "Воспроизвести");
    button.title = playing ? "Пауза (пробел)" : "Воспроизвести (пробел)";
    setTitle({ playing: playing ? current()?.title : null });
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = current() ? (playing ? "playing" : "paused") : "none";
    emit("player:state", { item: current(), playing });
}

function renderProgress() {
    const element = state.element;
    const duration = Number.isFinite(element.duration) ? element.duration : current()?.duration || 0;
    $("#player-duration").textContent = formatClock(duration);
    if (state.seeking) return;
    $("#player-time").textContent = formatClock(element.currentTime);
    setRangeValue($("#player-seek"), duration ? element.currentTime / duration : 0);
}

function renderVolume() {
    setRangeValue($("#player-volume"), state.muted ? 0 : state.volume);
    const button = $("#player-mute");
    button.innerHTML = icon(state.muted || state.volume === 0 ? "volume-mute" : "volume");
    button.setAttribute("aria-label", state.muted ? "Включить звук" : "Выключить звук");
}

function renderModes() {
    const shuffle = $("#player-shuffle");
    shuffle.classList.toggle("is-active", state.shuffle);
    shuffle.setAttribute("aria-pressed", String(state.shuffle));
    const repeat = $("#player-repeat");
    repeat.classList.toggle("is-active", state.repeat !== "off");
    repeat.setAttribute("aria-pressed", String(state.repeat !== "off"));
    repeat.innerHTML = icon(state.repeat === "one" ? "repeat-one" : "repeat");
    repeat.title = { off: "Повтор выключен", all: "Повтор очереди", one: "Повтор трека" }[state.repeat];
}

function queueItemHtml(item, queueIndex, isCurrent) {
    return `<div class="queue-item ${isCurrent ? "current" : ""}" data-queue-index="${queueIndex}">
        <span class="track-cover">${coverHtml(item, { play: false })}</span>
        <div class="track-main"><div class="track-title">${escapeHtml(item.title)}</div><div class="track-sub">${escapeHtml(item.artist || item.playlist_title || "")}</div></div>
        <button class="icon-button" type="button" data-queue-remove="${queueIndex}" aria-label="Убрать из очереди">${icon("x")}</button>
    </div>`;
}

function renderQueue() {
    const list = $("#play-queue-list");
    if ($("#play-queue").hidden) return;
    const item = current();
    if (!item) {
        list.innerHTML = `<div class="empty-state"><strong>Очередь пуста</strong><p>Нажми ▶ на любом треке в медиатеке.</p></div>`;
        return;
    }
    const upcoming = state.order.slice(state.orderPos + 1, state.orderPos + 301);
    list.innerHTML = `<div class="queue-divider">Сейчас играет</div>${queueItemHtml(item, state.index, true)}
        ${upcoming.length ? `<div class="queue-divider">Далее · ${state.order.length - state.orderPos - 1}</div>` : ""}
        ${upcoming.map((index) => queueItemHtml(state.queue[index], index, false)).join("")}`;
}

function toggleQueue(force) {
    const panel = $("#play-queue");
    const open = force ?? panel.hidden;
    panel.hidden = !open;
    $("#player-queue-button").classList.toggle("is-active", open);
    $("#player-queue-button").setAttribute("aria-expanded", String(open));
    if (open) renderQueue();
}

function updateMediaSession() {
    if (!("mediaSession" in navigator)) return;
    const item = current();
    if (!item) { navigator.mediaSession.metadata = null; return; }
    const artwork = thumbUrl(item) ? [{ src: new URL(thumbUrl(item), location.href).href, sizes: "480x360" }] : [];
    navigator.mediaSession.metadata = new MediaMetadata({
        title: item.title,
        artist: item.artist || "",
        album: item.playlist_title || "PyLoader",
        artwork,
    });
}

function updatePositionState() {
    const element = state.element;
    if (!("mediaSession" in navigator) || !Number.isFinite(element.duration) || !element.duration) return;
    try {
        navigator.mediaSession.setPositionState({
            duration: element.duration,
            playbackRate: element.playbackRate,
            position: Math.min(element.currentTime, element.duration),
        });
    } catch { /* unsupported */ }
}

// ---------- Init ----------
async function restore() {
    const saved = storage.get(STORE_KEY, null);
    if (!saved) return;
    state.shuffle = Boolean(saved.shuffle);
    state.repeat = ["off", "all", "one"].includes(saved.repeat) ? saved.repeat : "off";
    state.volume = typeof saved.volume === "number" ? saved.volume : 0.8;
    state.muted = Boolean(saved.muted);
    renderModes();
    renderVolume();
    if (!prefs.resumePlayer || !saved.ids?.length) return;
    try {
        const items = await fetchItems(saved.ids.slice(0, 2000));
        if (!items.length) return;
        const currentId = saved.ids[saved.index];
        state.queue = items;
        state.index = Math.max(0, items.findIndex((item) => item.id === currentId));
        rebuildOrder();
        load(state.index, { autoplay: false, position: items[state.index].id === currentId ? saved.position : 0 });
    } catch { /* library unavailable; start empty */ }
}

export function init() {
    for (const element of [audio, video]) {
        element.addEventListener("play", renderPlayState);
        element.addEventListener("pause", () => { renderPlayState(); persist(true); });
        element.addEventListener("timeupdate", () => {
            if (element !== state.element) return;
            renderProgress();
            persist();
            updatePositionState();
        });
        element.addEventListener("durationchange", renderProgress);
        element.addEventListener("ended", () => { if (element === state.element) next({ auto: true }); });
        element.addEventListener("error", () => {
            if (element !== state.element || !element.getAttribute("src")) return;
            toast(`Не удалось воспроизвести «${current()?.title || "файл"}» — формат не поддерживается браузером`, "error");
            if (state.queue.length > 1 && state.orderPos < state.order.length - 1) window.setTimeout(() => next(), 800);
        });
    }

    $("#player-toggle").addEventListener("click", toggle);
    $("#player-next").addEventListener("click", () => next());
    $("#player-prev").addEventListener("click", prev);
    $("#player-shuffle").addEventListener("click", toggleShuffle);
    $("#player-repeat").addEventListener("click", cycleRepeat);
    $("#player-mute").addEventListener("click", toggleMute);
    $("#player-volume").addEventListener("input", (event) => setVolume(Number(event.target.value) / 100));
    const seek = $("#player-seek");
    seek.addEventListener("input", () => {
        state.seeking = true;
        const duration = state.element.duration || 0;
        const fraction = Number(seek.value) / Number(seek.max);
        seek.style.setProperty("--value", `${fraction * 100}%`);
        $("#player-time").textContent = formatClock(fraction * duration);
    });
    seek.addEventListener("change", () => {
        const duration = state.element.duration;
        if (Number.isFinite(duration)) state.element.currentTime = (Number(seek.value) / Number(seek.max)) * duration;
        state.seeking = false;
    });
    $("#player-cover").addEventListener("click", () => {
        if (current()?.media_type === "video") showStage();
    });
    $("#player-fav").addEventListener("click", () => {
        const item = current();
        if (item) setFavorite([item.id], !item.favorite);
    });
    $("#player-queue-button").addEventListener("click", () => toggleQueue());
    $("#play-queue-close").addEventListener("click", () => toggleQueue(false));
    $("#play-queue-clear").addEventListener("click", () => {
        if (!current()) return;
        const keep = current();
        state.queue = [keep];
        state.index = 0;
        rebuildOrder();
        renderQueue();
        persist(true);
    });
    $("#play-queue-list").addEventListener("click", (event) => {
        const remove = event.target.closest("[data-queue-remove]");
        if (remove) { removeAt(Number(remove.dataset.queueRemove)); return; }
        const row = event.target.closest("[data-queue-index]");
        if (row) load(Number(row.dataset.queueIndex));
    });
    $("#video-close").addEventListener("click", () => hideStage(true));
    $("#video-fullscreen").addEventListener("click", () => $("#video-stage").requestFullscreen?.().catch(() => {}));
    video.addEventListener("click", toggle);
    video.addEventListener("dblclick", () => $("#video-stage").requestFullscreen?.().catch(() => {}));

    if ("mediaSession" in navigator) {
        const handlers = {
            play: () => state.element.play().catch(() => {}),
            pause: () => state.element.pause(),
            previoustrack: prev,
            nexttrack: () => next(),
            seekbackward: () => seekBy(-10),
            seekforward: () => seekBy(10),
            seekto: (details) => { if (Number.isFinite(details.seekTime)) state.element.currentTime = details.seekTime; },
        };
        for (const [action, handler] of Object.entries(handlers)) {
            try { navigator.mediaSession.setActionHandler(action, handler); } catch { /* unsupported action */ }
        }
    }

    on("media:updated", ({ ids, changes }) => {
        const set = new Set(ids);
        for (const item of state.queue) if (set.has(item.id)) Object.assign(item, changes);
        renderTrack();
    });
    on("media:deleting", ({ ids }) => {
        if (!current() || !ids.includes(current().id)) return;
        state.element.pause();
        state.element.removeAttribute("src");
        state.element.load();
    });
    on("media:deleted", ({ ids }) => {
        const set = new Set(ids);
        for (let index = state.queue.length - 1; index >= 0; index -= 1) {
            if (set.has(state.queue[index].id)) removeAt(index);
        }
    });
    window.addEventListener("beforeunload", () => persist(true));

    renderModes();
    renderVolume();
    renderTrack();
    restore();
}
