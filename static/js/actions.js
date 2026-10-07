import { api } from "./api.js";
import { deleteMedia, fetchItems, revealInFolder, sendToPhone, setFavorite } from "./media.js";
import * as player from "./player.js";
import { mosaicHtml } from "./render.js";
import { emit, shared } from "./store.js";
import { openDialog, openMenu, promptDialog, toast } from "./ui.js";
import { countLabel, downloadUrl, escapeHtml, formatLongDuration, icon } from "./util.js";

async function withItems(ids, fallback, handler) {
    try {
        const items = ids.length === 1 && fallback ? [fallback] : await fetchItems(ids);
        handler(items);
    } catch (error) { toast(error.message, "error"); }
}

function saveCopy(item) {
    const link = document.createElement("a");
    link.href = downloadUrl(item);
    link.download = "";
    document.body.append(link);
    link.click();
    link.remove();
}

export async function createPlaylistFlow(ids = [], { open = true } = {}) {
    const name = await promptDialog({
        title: "Новый плейлист",
        label: "Название",
        placeholder: "Например, «В дорогу»",
        confirmLabel: "Создать",
    });
    if (!name) return null;
    try {
        const data = await api("/api/playlists", { method: "POST", body: { name, ids } });
        emit("playlists:changed");
        toast(ids.length ? `Плейлист «${data.playlist.name}» создан · ${data.playlist.count}` : `Плейлист «${data.playlist.name}» создан`);
        if (open) location.hash = `#/playlist/${data.playlist.id}`;
        return data.playlist;
    } catch (error) {
        toast(error.message, "error");
        return null;
    }
}

export async function addToPlaylist(playlistId, ids) {
    try {
        const data = await api(`/api/playlists/${encodeURIComponent(playlistId)}/items`, { method: "POST", body: { ids } });
        const playlist = shared.playlists.find((row) => row.id === playlistId);
        emit("playlists:changed");
        const name = playlist ? `«${playlist.name}»` : "плейлист";
        toast(data.added ? `Добавлено в ${name}: ${data.added}` : `Уже есть в ${name}`, data.added ? "success" : "warning", {
            action: { label: "Открыть", onClick: () => { location.hash = `#/playlist/${playlistId}`; } },
        });
    } catch (error) { toast(error.message, "error"); }
}

export async function addToPlaylistDialog(ids) {
    let playlists = shared.playlists;
    try { playlists = (await api("/api/playlists")).playlists; } catch { /* use cached */ }
    const body = document.createElement("div");
    body.className = "stack";
    body.innerHTML = `
        ${playlists.length ? `<div class="picker-list">${playlists.map((playlist) => `
            <button class="picker-item" type="button" data-playlist="${escapeHtml(playlist.id)}">
                ${mosaicHtml(playlist.thumbnails)}
                <span><strong>${escapeHtml(playlist.name)}</strong><small>${countLabel(playlist.count, "трек", "трека", "треков")}</small></span>
            </button>`).join("")}</div>` : `<p class="muted small">Плейлистов пока нет — создай первый.</p>`}
        <div class="picker-new"><input placeholder="Новый плейлист…" maxlength="80" aria-label="Название нового плейлиста"><button class="button primary compact" type="button">${icon("plus")}Создать</button></div>`;
    const input = body.querySelector("input");
    await openDialog({
        title: ids.length > 1 ? `Добавить ${countLabel(ids.length, "файл", "файла", "файлов")} в плейлист` : "Добавить в плейлист",
        body,
        actions: [{ label: "Закрыть", value: "cancel" }],
        onOpen: (dialog, close) => {
            body.addEventListener("click", async (event) => {
                const target = event.target.closest("[data-playlist]");
                if (target) { close("added"); await addToPlaylist(target.dataset.playlist, ids); }
            });
            const create = async () => {
                const name = input.value.trim();
                if (!name) { input.focus(); return; }
                close("created");
                try {
                    const data = await api("/api/playlists", { method: "POST", body: { name, ids } });
                    emit("playlists:changed");
                    toast(`Плейлист «${data.playlist.name}» создан · ${data.playlist.count}`, "success", {
                        action: { label: "Открыть", onClick: () => { location.hash = `#/playlist/${data.playlist.id}`; } },
                    });
                } catch (error) { toast(error.message, "error"); }
            };
            body.querySelector(".picker-new button").addEventListener("click", create);
            input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); create(); } });
            if (!playlists.length) input.focus();
        },
    });
}

export async function removeFromPlaylist(playlistId, ids) {
    try {
        await api(`/api/playlists/${encodeURIComponent(playlistId)}/items`, { method: "DELETE", body: { ids } });
        emit("playlists:changed");
        emit("playlist:items-removed", { playlistId, ids });
        toast(ids.length > 1 ? `Убрано из плейлиста: ${ids.length}` : "Убрано из плейлиста");
    } catch (error) { toast(error.message, "error"); }
}

/** Context menu for one track, or for the whole selection when the track is part of it. */
export function openTrackMenu(item, { anchor = null, point = null, selection = [], playlistId = null } = {}) {
    const ids = selection.length > 1 && selection.includes(item.id) ? selection : [item.id];
    const many = ids.length > 1;
    const youtube = item.source_url || (item.video_id ? `https://www.youtube.com/watch?v=${item.video_id}` : null);
    openMenu([
        { header: many ? `Выбрано: ${ids.length}` : item.title },
        { label: "Слушать", icon: "play", action: () => (many ? player.playIds(ids) : player.playItems([item])) },
        { label: "Играть следующим", icon: "play-next", action: () => withItems(ids, item, player.playNext) },
        { label: "Добавить в очередь", icon: "queue", action: () => withItems(ids, item, player.addToQueue) },
        "-",
        { label: "Добавить в плейлист…", icon: "list-plus", action: () => addToPlaylistDialog(ids) },
        playlistId ? { label: "Убрать из этого плейлиста", icon: "x", action: () => removeFromPlaylist(playlistId, ids) } : null,
        { label: "Отправить на телефон", icon: "send", action: () => sendToPhone(ids) },
        { label: item.favorite ? "Убрать из избранного" : "В избранное", icon: "heart", action: () => setFavorite(ids, !item.favorite) },
        "-",
        many ? null : { label: "Показать в папке", icon: "folder", action: () => revealInFolder(item) },
        many ? null : { label: "Сохранить копию", icon: "download", action: () => saveCopy(item) },
        !many && youtube ? { label: "Открыть источник", icon: "youtube", action: () => window.open(youtube, "_blank", "noopener") } : null,
        many ? null : "-",
        { label: many ? `Удалить ${countLabel(ids.length, "файл", "файла", "файлов")}…` : "Удалить с диска…", icon: "trash", danger: true, action: () => deleteMedia(ids, item.title) },
    ], { anchor, point });
}

export function describeItems(items) {
    const duration = items.reduce((sum, item) => sum + Number(item.duration || 0), 0);
    return `${countLabel(items.length, "трек", "трека", "треков")}${duration ? ` · ${formatLongDuration(duration)}` : ""}`;
}
