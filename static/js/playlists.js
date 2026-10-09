import { addToPlaylist, createPlaylistFlow, describeItems } from "./actions.js";
import { api } from "./api.js";
import { sendToPhone } from "./media.js";
import * as player from "./player.js";
import { mosaicHtml, syncPlaying, trackHeadHtml, trackRowHtml, updateFavorite } from "./render.js";
import { emit, on, shared } from "./store.js";
import { DRAG_IDS, bindTrackList } from "./tracklist.js";
import { confirmDialog, openMenu, promptDialog, toast } from "./ui.js";
import { $, countLabel, escapeHtml, formatLongDuration, icon } from "./util.js";

const detail = { kind: null, key: null, playlist: null, items: [], active: false };

function cardHtml({ href, name, meta, thumbnails }) {
    return `<a class="collection-card" href="${href}">${mosaicHtml(thumbnails)}<strong title="${escapeHtml(name)}">${escapeHtml(name)}</strong><small>${escapeHtml(meta)}</small></a>`;
}

function playlistMeta(row) {
    return `${countLabel(row.count, "трек", "трека", "треков")}${row.duration ? ` · ${formatLongDuration(row.duration)}` : ""}`;
}

function renderSidebar() {
    const container = $("#sidebar-playlists");
    const current = shared.route.view === "playlist" ? shared.route.param : null;
    container.innerHTML = shared.playlists.length
        ? shared.playlists.map((row) => `<a class="sidebar-playlist ${current === row.id ? "active" : ""}" href="#/playlist/${row.id}" data-drop-playlist="${row.id}">
            ${icon("playlist")}<span>${escapeHtml(row.name)}</span><small>${row.count}</small></a>`).join("")
        : `<div class="sidebar-empty">Перетащи сюда треки или создай плейлист кнопкой +</div>`;
}

export async function loadPlaylists() {
    try {
        shared.playlists = (await api("/api/playlists")).playlists;
        renderSidebar();
        if (shared.route.view === "playlists") renderUserPlaylists();
    } catch { /* keep the previous list */ }
}

function renderUserPlaylists() {
    $("#user-playlists").innerHTML = shared.playlists.map((row) => cardHtml({
        href: `#/playlist/${row.id}`, name: row.name, meta: playlistMeta(row), thumbnails: row.thumbnails,
    })).join("") + `<button class="collection-card new" type="button" id="playlist-new-card">${icon("plus")}<span>Новый плейлист</span></button>`;
}

export async function enterOverview() {
    renderUserPlaylists();
    await loadPlaylists();
    try {
        const data = await api("/api/library/collections");
        $("#auto-collections").innerHTML = data.collections.length
            ? data.collections.map((row) => cardHtml({
                href: `#/collection/${encodeURIComponent(row.name)}`, name: row.name, meta: playlistMeta(row), thumbnails: row.thumbnails,
            })).join("")
            : `<div class="collection-empty">Скачай плейлист YouTube — он появится здесь отдельной коллекцией.</div>`;
    } catch (error) { toast(error.message, "error"); }
}

function renderDetail() {
    const { kind, playlist, items } = detail;
    const isPlaylist = kind === "playlist";
    $("#playlist-kind").textContent = isPlaylist ? "Плейлист" : "Коллекция YouTube";
    $("#playlist-name").textContent = isPlaylist ? playlist.name : detail.key;
    $("#playlist-name").title = $("#playlist-name").textContent;
    $("#playlist-meta").textContent = items.length ? describeItems(items) : "Пока пусто";
    $("#playlist-cover").outerHTML = mosaicHtml(items.map((item) => item.thumbnail), "large").replace("<div", '<div id="playlist-cover"');
    $("#playlist-empty").classList.toggle("hidden", items.length > 0);
    for (const id of ["#playlist-play", "#playlist-shuffle", "#playlist-phone"]) $(id).disabled = !items.length;
    const mode = isPlaylist ? "sortable" : "plain";
    $("#playlist-content").innerHTML = items.length
        ? `<div class="track-list ${isPlaylist ? "sortable" : "no-select"}">${trackHeadHtml(mode)}${items
            .map((item, index) => trackRowHtml(item, { index, mode: isPlaylist ? "sortable" : "plain" })).join("")}</div>`
        : "";
    if (!isPlaylist) {
        // Collections are read-only: show the position instead of a drag handle.
        $("#playlist-content").querySelectorAll(".track-row > .check").forEach((node, index) => {
            node.outerHTML = `<span class="track-num" style="text-align:center">${items[index].playlist_index || index + 1}</span>`;
        });
    }
    syncPlaying($("#playlist-content"), player.current()?.id, player.isPlaying());
}

export async function enterDetail(kind, key) {
    detail.kind = kind;
    detail.key = key;
    detail.active = true;
    renderSidebar();
    try {
        if (kind === "playlist") {
            const data = await api(`/api/playlists/${encodeURIComponent(key)}`);
            detail.playlist = data.playlist;
            detail.items = data.playlist.items;
        } else {
            const query = new URLSearchParams({ collection: key, sort: "playlist", limit: "1000" });
            detail.playlist = null;
            detail.items = (await api(`/api/library?${query}`)).items;
        }
        renderDetail();
    } catch (error) {
        toast(error.message, "error");
        location.hash = "#/playlists";
    }
}

export function leave() {
    detail.active = false;
}

async function reorder(ids) {
    const byId = new Map(detail.items.map((item) => [item.id, item]));
    detail.items = ids.map((id) => byId.get(id));
    renderDetail();
    try {
        await api(`/api/playlists/${encodeURIComponent(detail.playlist.id)}`, { method: "PATCH", body: { ids } });
        emit("playlists:changed");
    } catch (error) {
        toast(error.message, "error");
        enterDetail(detail.kind, detail.key);
    }
}

function detailMenu(anchor) {
    if (detail.kind === "playlist") {
        openMenu([
            { label: "Переименовать…", icon: "edit", action: renamePlaylist },
            { label: "Добавить треки из медиатеки", icon: "library", action: () => { location.hash = "#/library"; } },
            "-",
            { label: "Удалить плейлист…", icon: "trash", danger: true, action: deletePlaylist },
        ], { anchor });
    } else {
        openMenu([
            { label: "Сохранить как мой плейлист…", icon: "list-plus", action: () => createPlaylistFlow(detail.items.map((item) => item.id)) },
        ], { anchor });
    }
}

async function renamePlaylist() {
    const name = await promptDialog({ title: "Переименовать плейлист", label: "Название", value: detail.playlist.name });
    if (!name) return;
    try {
        await api(`/api/playlists/${encodeURIComponent(detail.playlist.id)}`, { method: "PATCH", body: { name } });
        detail.playlist.name = name;
        renderDetail();
        emit("playlists:changed");
    } catch (error) { toast(error.message, "error"); }
}

async function deletePlaylist() {
    const ok = await confirmDialog({
        title: "Удалить плейлист?",
        message: `«${detail.playlist.name}» исчезнет из списка. Сами файлы останутся в медиатеке.`,
        confirmLabel: "Удалить",
        danger: true,
    });
    if (!ok) return;
    try {
        await api(`/api/playlists/${encodeURIComponent(detail.playlist.id)}`, { method: "DELETE" });
        emit("playlists:changed");
        toast("Плейлист удалён");
        location.hash = "#/playlists";
    } catch (error) { toast(error.message, "error"); }
}

export function init() {
    const content = $("#playlist-content");
    bindTrackList(content, {
        getItems: () => detail.items,
        playlistId: () => (detail.kind === "playlist" ? detail.playlist?.id : null),
        onReorder: reorder,
        canReorder: () => detail.kind === "playlist",
    });

    $("#new-playlist-button").addEventListener("click", () => createPlaylistFlow());
    $("#sidebar-new-playlist").addEventListener("click", () => createPlaylistFlow());
    $("#user-playlists").addEventListener("click", (event) => {
        if (event.target.closest("#playlist-new-card")) createPlaylistFlow();
    });
    $("#playlist-play").addEventListener("click", () => player.playItems(detail.items));
    $("#playlist-shuffle").addEventListener("click", () => player.playItems(detail.items, 0, { shuffle: true }));
    $("#playlist-phone").addEventListener("click", () => sendToPhone(detail.items.map((item) => item.id)));
    $("#playlist-more").addEventListener("click", (event) => detailMenu(event.currentTarget));

    // Drag tracks from any list onto a playlist in the sidebar.
    const sidebar = $("#sidebar-playlists");
    sidebar.addEventListener("dragover", (event) => {
        const target = event.target.closest("[data-drop-playlist]");
        if (!target || !event.dataTransfer.types.includes(DRAG_IDS)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        sidebar.querySelectorAll(".drop-target").forEach((node) => node.classList.remove("drop-target"));
        target.classList.add("drop-target");
    });
    sidebar.addEventListener("dragleave", (event) => event.target.closest("[data-drop-playlist]")?.classList.remove("drop-target"));
    sidebar.addEventListener("drop", (event) => {
        const target = event.target.closest("[data-drop-playlist]");
        if (!target) return;
        event.preventDefault();
        target.classList.remove("drop-target");
        try { addToPlaylist(target.dataset.dropPlaylist, JSON.parse(event.dataTransfer.getData(DRAG_IDS))); } catch { /* not ours */ }
    });

    on("playlists:changed", async () => {
        await loadPlaylists();
        if (detail.active && detail.kind === "playlist") enterDetail("playlist", detail.key);
    });
    on("route", renderSidebar);
    on("media:updated", ({ ids, changes }) => {
        const set = new Set(ids);
        for (const item of detail.items) if (set.has(item.id)) Object.assign(item, changes);
        if ("favorite" in changes) updateFavorite(content, ids, changes.favorite);
    });
    on("media:deleted", ({ ids }) => {
        const set = new Set(ids);
        detail.items = detail.items.filter((item) => !set.has(item.id));
        if (detail.active) renderDetail();
    });
    on("playlist:items-removed", ({ playlistId, ids }) => {
        if (!detail.active || detail.playlist?.id !== playlistId) return;
        const set = new Set(ids);
        detail.items = detail.items.filter((item) => !set.has(item.id));
        renderDetail();
    });
    on("library:revision", () => { if (detail.active && detail.kind === "collection") enterDetail("collection", detail.key); });
    on("player:state", ({ item, playing }) => syncPlaying(content, item?.id, playing));
}
