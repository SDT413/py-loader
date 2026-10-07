import { escapeHtml, formatDuration, icon, thumbUrl } from "./util.js";

export function coverHtml(item, { play = true } = {}) {
    const url = thumbUrl(item);
    const visual = url
        ? `<img src="${url}" alt="" loading="lazy" decoding="async">`
        : `<span class="placeholder">${icon(item.media_type === "video" ? "film" : "library")}</span>`;
    return `${visual}${play ? `<span class="cover-play">${icon("play")}</span>` : ""}`;
}

export function mosaicHtml(thumbnails = [], className = "") {
    const thumbs = thumbnails.filter(Boolean);
    if (!thumbs.length) return `<div class="mosaic ${className}"><span class="placeholder">${icon("playlist")}</span></div>`;
    const shown = thumbs.length >= 4 ? thumbs.slice(0, 4) : thumbs.slice(0, 1);
    return `<div class="mosaic ${shown.length === 1 ? "single" : ""} ${className}">${shown
        .map((thumb) => `<img src="/thumbs/${thumb.split("/").map(encodeURIComponent).join("/")}" alt="" loading="lazy">`).join("")}</div>`;
}

function subtitle(item) {
    return item.artist || (item.media_type === "video" ? "Видео" : "Аудио");
}

function collectionLink(item) {
    if (!item.playlist_title) return "";
    return `<a href="#/collection/${encodeURIComponent(item.playlist_title)}" title="${escapeHtml(item.playlist_title)}">${escapeHtml(item.playlist_title)}</a>`;
}

function actionsHtml(item) {
    return `<div class="track-actions">
        <button class="icon-button fav-button ${item.favorite ? "is-fav" : ""}" type="button" data-action="favorite" title="${item.favorite ? "Убрать из избранного" : "В избранное"}" aria-label="Избранное">${icon("heart")}</button>
        <button class="icon-button" type="button" data-action="menu" title="Ещё" aria-label="Ещё">${icon("more")}</button>
    </div>`;
}

export function trackHeadHtml(mode = "select") {
    const lead = mode === "select"
        ? `<label class="check"><input type="checkbox" data-select-all aria-label="Выбрать всё"><span></span></label>`
        : "<span></span>";
    return `<div class="track-head">${lead}<span></span><span>Название</span><span>Коллекция</span><span>Формат</span><span class="num">Время</span><span class="num col-size">Размер</span><span></span></div>`;
}

export function trackRowHtml(item, { index, mode = "select", selected = false } = {}) {
    const lead = mode === "sortable"
        ? `<span class="drag-handle" title="Перетащи, чтобы изменить порядок">${icon("grip")}</span>`
        : `<label class="check"><input type="checkbox" data-select ${selected ? "checked" : ""} aria-label="Выбрать"><span></span></label>`;
    return `<div class="track-row ${selected ? "selected" : ""}" data-id="${escapeHtml(item.id)}" data-index="${index}" draggable="true">
        ${lead}
        <button class="track-cover" type="button" data-action="play" aria-label="Играть">${coverHtml(item)}</button>
        <div class="track-main"><div class="track-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div><div class="track-sub">${escapeHtml(subtitle(item))}</div></div>
        <div class="track-collection">${collectionLink(item)}</div>
        <div class="track-format"><span class="format-tag ${item.media_type}">${escapeHtml(String(item.format || "").toUpperCase())}</span></div>
        <div class="track-num">${formatDuration(item.duration)}</div>
        <div class="track-num col-size">${escapeHtml(item.size_text || "")}</div>
        ${actionsHtml(item)}
    </div>`;
}

export function mediaCardHtml(item, { index, selected = false } = {}) {
    const duration = item.duration ? `<span class="duration-tag">${formatDuration(item.duration)}</span>` : "";
    return `<article class="media-card ${selected ? "selected" : ""}" data-id="${escapeHtml(item.id)}" data-index="${index}" draggable="true">
        <label class="check"><input type="checkbox" data-select ${selected ? "checked" : ""} aria-label="Выбрать"><span></span></label>
        <button class="media-cover" type="button" data-action="play" aria-label="Играть">${coverHtml(item)}
            <span class="format-tag">${escapeHtml(String(item.format || "").toUpperCase())}</span>${duration}</button>
        <div class="media-body">
            <div class="media-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
            <div class="media-sub">${escapeHtml([subtitle(item), item.size_text].filter(Boolean).join(" · "))}</div>
            ${actionsHtml(item)}
        </div>
    </article>`;
}

/** Highlight the current track in a rendered list without re-rendering it. */
export function syncPlaying(container, currentId, playing) {
    if (!container) return;
    for (const node of container.querySelectorAll("[data-id]")) {
        const isCurrent = node.dataset.id === currentId;
        const wasCurrent = node.classList.contains("is-current");
        node.classList.toggle("is-current", isCurrent);
        const play = node.querySelector(".cover-play");
        if (!play) continue;
        if (isCurrent) {
            play.innerHTML = `<span class="eq ${playing ? "" : "paused"}"><i></i><i></i><i></i></span>`;
        } else if (wasCurrent) {
            play.innerHTML = icon("play");
        }
    }
}

export function updateFavorite(container, ids, favorite) {
    if (!container) return;
    const set = new Set(ids);
    for (const node of container.querySelectorAll("[data-id]")) {
        if (!set.has(node.dataset.id)) continue;
        const button = node.querySelector(".fav-button");
        button?.classList.toggle("is-fav", favorite);
        if (button) button.title = favorite ? "Убрать из избранного" : "В избранное";
    }
}
