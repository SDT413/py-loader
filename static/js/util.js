export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[char]);
}

export function icon(name, className = "") {
    return `<svg class="${className}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

export function encodePath(path) {
    return String(path || "").split("/").map(encodeURIComponent).join("/");
}

export const thumbUrl = (item) => (item?.thumbnail ? `/thumbs/${encodePath(item.thumbnail)}` : null);
export const streamUrl = (item) => `/stream/${encodePath(item.filename)}`;
export const downloadUrl = (item) => `/media/${encodePath(item.filename)}`;

export function formatDuration(seconds) {
    const value = Math.round(Number(seconds || 0));
    if (!value) return "—";
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    const secs = value % 60;
    const mm = hours ? String(minutes).padStart(2, "0") : String(minutes);
    return `${hours ? `${hours}:` : ""}${mm}:${String(secs).padStart(2, "0")}`;
}

export function formatClock(seconds) {
    const value = Number.isFinite(seconds) ? seconds : 0;
    return formatDuration(value) === "—" ? "0:00" : formatDuration(value);
}

export function formatLongDuration(seconds) {
    const value = Math.round(Number(seconds || 0));
    if (value < 60) return value ? `${value} сек` : "0 мин";
    const hours = Math.floor(value / 3600);
    const minutes = Math.round((value % 3600) / 60);
    if (!hours) return `${minutes} мин`;
    return minutes ? `${hours} ч ${minutes} мин` : `${hours} ч`;
}

export function formatBytes(size) {
    let value = Number(size || 0);
    const units = ["B", "KB", "MB", "GB", "TB"];
    for (const unit of units) {
        if (value < 1024 || unit === units.at(-1)) return unit === "B" ? `${value.toFixed(0)} B` : `${value.toFixed(1)} ${unit}`;
        value /= 1024;
    }
    return "0 B";
}

export function formatEta(seconds) {
    if (seconds === null || seconds === undefined) return "";
    if (seconds < 60) return `${Math.max(0, Math.round(seconds))} сек`;
    if (seconds < 3600) return `${Math.ceil(seconds / 60)} мин`;
    return `${Math.floor(seconds / 3600)} ч ${Math.round((seconds % 3600) / 60)} мин`;
}

/** Russian plural: plural(5, "файл", "файла", "файлов") → "файлов". */
export function plural(count, one, few, many) {
    const n = Math.abs(Number(count)) % 100;
    const last = n % 10;
    if (n > 10 && n < 20) return many;
    if (last > 1 && last < 5) return few;
    if (last === 1) return one;
    return many;
}

export const countLabel = (count, one, few, many) => `${count} ${plural(count, one, few, many)}`;

export function relativeTime(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    const diff = (Date.now() - date.getTime()) / 1000;
    if (diff < 60) return "только что";
    if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
    const time = date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    const today = new Date();
    if (date.toDateString() === today.toDateString()) return `сегодня в ${time}`;
    const yesterday = new Date(today.getTime() - 86400000);
    if (date.toDateString() === yesterday.toDateString()) return `вчера в ${time}`;
    return date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

export function debounce(fn, wait = 250) {
    let timer;
    return (...args) => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => fn(...args), wait);
    };
}

const URL_RE = /\bhttps?:\/\/[^\s<>"'`]+/gi;
const BARE_RE = /(^|[\s(])((?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)\/[^\s<>"'`]+)/gi;

/** Pull every http(s) link (and bare youtube.com / youtu.be links) out of arbitrary text. */
export function extractUrls(text) {
    const found = [];
    const source = String(text || "");
    for (const match of source.matchAll(URL_RE)) found.push(match[0]);
    for (const match of source.matchAll(BARE_RE)) found.push(`https://${match[2]}`);
    const cleaned = found
        .map((url) => url.replace(/[)\].,;:!?»"']+$/, ""))
        .filter((url) => { try { return Boolean(new URL(url).hostname); } catch { return false; } });
    return [...new Set(cleaned)];
}

export const storage = {
    get(key, fallback) {
        try {
            const raw = window.localStorage.getItem(key);
            return raw === null ? fallback : JSON.parse(raw);
        } catch {
            return fallback;
        }
    },
    set(key, value) {
        try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage full or blocked */ }
    },
};

export function isTyping(target = document.activeElement) {
    if (!target) return false;
    const tag = target.tagName;
    return tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable
        || (tag === "INPUT" && !["checkbox", "radio", "range", "button"].includes(target.type));
}

export function shuffleArray(values) {
    const copy = [...values];
    for (let index = copy.length - 1; index > 0; index -= 1) {
        const swap = Math.floor(Math.random() * (index + 1));
        [copy[index], copy[swap]] = [copy[swap], copy[index]];
    }
    return copy;
}
