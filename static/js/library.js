import { addToPlaylistDialog } from "./actions.js";
import { api } from "./api.js";
import { deleteMedia, fetchItems, sendToPhone, setFavorite } from "./media.js";
import * as player from "./player.js";
import { mediaCardHtml, syncPlaying, trackHeadHtml, trackRowHtml, updateFavorite } from "./render.js";
import { on, shared } from "./store.js";
import { applySelection, bindTrackList } from "./tracklist.js";
import { setButtonLoading, toast } from "./ui.js";
import { $, $$, countLabel, debounce, escapeHtml, formatLongDuration, isTyping, storage } from "./util.js";

const PAGE = 200;
const state = {
    items: [],
    total: 0,
    filter: "all",
    search: "",
    sort: "date",
    collection: "",
    layout: storage.get("pyloader.library.layout", "list"),
    selected: new Set(),
    revision: -1,
    active: false,
    stale: true,
    requestId: 0,
};

function params(extra = {}) {
    const query = new URLSearchParams({ filter: state.filter, search: state.search, sort: state.sort, ...extra });
    if (state.collection) query.set("collection", state.collection);
    return query;
}

async function allIds() {
    if (state.items.length >= state.total) return state.items.map((item) => item.id);
    return (await api(`/api/library/ids?${params()}`)).ids;
}

export async function refresh({ append = false } = {}) {
    const requestId = ++state.requestId;
    try {
        const data = await api(`/api/library?${params({ limit: String(PAGE), offset: String(append ? state.items.length : 0) })}`);
        if (requestId !== state.requestId) return;
        state.items = append ? [...state.items, ...data.items] : data.items;
        state.total = data.total;
        state.revision = data.revision;
        state.stale = false;
        const known = new Set(state.items.map((item) => item.id));
        if (!append && state.items.length >= state.total) {
            for (const id of [...state.selected]) if (!known.has(id)) state.selected.delete(id);
        }
        render();
    } catch (error) {
        toast(error.message, "error");
    }
}

async function loadCollections() {
    try {
        const data = await api("/api/library/collections");
        const select = $("#library-collection");
        const value = state.collection;
        select.innerHTML = `<option value="">Все коллекции</option>${data.collections
            .map((row) => `<option value="${escapeHtml(row.name)}">${escapeHtml(row.name)} · ${row.count}</option>`).join("")}`;
        select.value = data.collections.some((row) => row.name === value) ? value : "";
    } catch { /* optional */ }
}

function renderLead() {
    const stats = shared.stats;
    if (!stats) return;
    const filtered = state.filter !== "all" || state.search || state.collection;
    $("#library-lead").textContent = !stats.total_count
        ? "Здесь появятся скачанные файлы"
        : filtered
            ? `Найдено ${state.total} из ${stats.total_count}`
            : `${countLabel(stats.total_count, "файл", "файла", "файлов")} · ${stats.total_size_text}${stats.total_duration ? ` · ${formatLongDuration(stats.total_duration)}` : ""}`;
}

function render() {
    const content = $("#library-content");
    const filtered = state.filter !== "all" || state.search || state.collection;
    $("#library-empty").classList.toggle("hidden", state.items.length > 0);
    $("#library-empty-title").textContent = filtered ? "Ничего не найдено" : "Медиатека пуста";
    $("#library-empty-text").textContent = filtered ? "Измени фильтр или поисковый запрос." : "Скачай первый трек во вкладке «Загрузка» — он появится здесь.";
    $("#library-more-wrap").classList.toggle("hidden", state.items.length >= state.total);
    $("#library-more-button").textContent = `Показать ещё · осталось ${state.total - state.items.length}`;
    $$("[data-layout]").forEach((button) => button.classList.toggle("active", button.dataset.layout === state.layout));
    if (!state.items.length) {
        content.innerHTML = "";
    } else if (state.layout === "grid") {
        content.innerHTML = `<div class="media-grid ${state.selected.size ? "selecting" : ""}">${state.items
            .map((item, index) => mediaCardHtml(item, { index, selected: state.selected.has(item.id) })).join("")}</div>`;
    } else {
        content.innerHTML = `<div class="track-list">${trackHeadHtml("select")}${state.items
            .map((item, index) => trackRowHtml(item, { index, selected: state.selected.has(item.id) })).join("")}</div>`;
    }
    renderLead();
    renderSelection();
    syncPlaying(content, player.current()?.id, player.isPlaying());
}

function renderSelection() {
    const count = state.selected.size;
    $("#library-selection").classList.toggle("hidden", count === 0);
    $("#library-selection-count").textContent = `Выбрано: ${count}`;
    const all = count > 0 && count >= state.total;
    for (const box of [$("#library-select-all"), $("[data-select-all]", $("#library-content"))].filter(Boolean)) {
        box.checked = all;
        box.indeterminate = count > 0 && !all;
    }
    $(".media-grid", $("#library-content"))?.classList.toggle("selecting", count > 0);
}

async function selectAll(checked) {
    if (!checked) {
        state.selected.clear();
    } else {
        try { (await allIds()).forEach((id) => state.selected.add(id)); }
        catch (error) { toast(error.message, "error"); }
    }
    applySelection($("#library-content"), state.selected);
    renderSelection();
}

async function bulk(action) {
    const ids = [...state.selected];
    if (action === "clear") { selectAll(false); return; }
    if (!ids.length) return;
    try {
        if (action === "play") player.playIds(ids);
        if (action === "queue") player.addToQueue(await fetchItems(ids));
        if (action === "playlist") addToPlaylistDialog(ids);
        if (action === "phone") sendToPhone(ids);
        if (action === "favorite") setFavorite(ids, true);
        if (action === "delete" && await deleteMedia(ids)) selectAll(false);
    } catch (error) { toast(error.message, "error"); }
}

async function playAll(shuffle) {
    const button = shuffle ? $("#library-shuffle-button") : $("#library-play-button");
    setButtonLoading(button, true, "Загружаю…");
    try {
        const ids = await allIds();
        if (!ids.length) { toast("В медиатеке пока пусто", "warning"); return; }
        await player.playIds(ids, { shuffle });
    } catch (error) {
        toast(error.message, "error");
    } finally {
        setButtonLoading(button, false);
    }
}

export function focusSearch() {
    const input = $("#library-search");
    input.focus();
    input.select();
}

export function enter() {
    state.active = true;
    loadCollections();
    if (state.stale || state.revision !== shared.libraryRevision) refresh(); else renderLead();
}

export function leave() {
    state.active = false;
}

export function init() {
    const content = $("#library-content");
    bindTrackList(content, {
        getItems: () => state.items,
        selection: state.selected,
        onSelectionChange: renderSelection,
        onSelectAll: selectAll,
    });

    const search = debounce(() => { state.search = $("#library-search").value.trim(); refresh(); }, 220);
    $("#library-search").addEventListener("input", search);
    $("#library-search").addEventListener("keydown", (event) => {
        if (event.key === "Escape") { event.target.value = ""; search(); event.target.blur(); }
    });
    $("#library-filter").addEventListener("click", (event) => {
        const chip = event.target.closest("[data-filter]");
        if (!chip) return;
        state.filter = chip.dataset.filter;
        $$("[data-filter]").forEach((node) => node.classList.toggle("active", node === chip));
        refresh();
    });
    $("#library-collection").addEventListener("change", (event) => { state.collection = event.target.value; refresh(); });
    $("#library-sort").addEventListener("change", (event) => { state.sort = event.target.value; refresh(); });
    $$("[data-layout]").forEach((button) => button.addEventListener("click", () => {
        state.layout = button.dataset.layout;
        storage.set("pyloader.library.layout", state.layout);
        render();
    }));
    $("#library-more-button").addEventListener("click", () => refresh({ append: true }));
    $("#library-play-button").addEventListener("click", () => playAll(false));
    $("#library-shuffle-button").addEventListener("click", () => playAll(true));
    $("#library-select-all").addEventListener("change", (event) => selectAll(event.target.checked));
    $("#library-selection").addEventListener("click", (event) => {
        const button = event.target.closest("[data-bulk]");
        if (button) bulk(button.dataset.bulk);
    });
    $("#reindex-button").addEventListener("click", async () => {
        const button = $("#reindex-button");
        button.disabled = true;
        button.classList.add("spinning");
        try {
            const data = await api("/api/library/reindex", { method: "POST" });
            toast(`Проиндексировано файлов: ${data.indexed}`);
            shared.stats = { ...(shared.stats || {}), ...data };
            await Promise.all([refresh(), loadCollections()]);
        } catch (error) {
            toast(error.message, "error");
        } finally {
            button.disabled = false;
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.defaultPrevented || !state.active || isTyping() || $("#modal").open) return;
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") { event.preventDefault(); selectAll(true); }
        if (event.key === "Escape" && state.selected.size) selectAll(false);
        if (event.key === "Delete" && state.selected.size) bulk("delete");
    });

    on("library:revision", (revision) => {
        if (revision === state.revision) return;
        state.stale = true;
        if (state.active) { refresh(); loadCollections(); }
    });
    on("stats", () => { if (state.active) renderLead(); });
    on("media:updated", ({ ids, changes }) => {
        const set = new Set(ids);
        for (const item of state.items) if (set.has(item.id)) Object.assign(item, changes);
        if ("favorite" in changes) {
            if (state.filter === "favorites") refresh();
            else updateFavorite(content, ids, changes.favorite);
        }
    });
    on("media:deleted", ({ ids }) => {
        ids.forEach((id) => state.selected.delete(id));
        state.stale = true;
        if (state.active) refresh();
    });
    on("player:state", ({ item, playing }) => syncPlaying(content, item?.id, playing));
    on("player:empty-play", () => playAll(false));
}
