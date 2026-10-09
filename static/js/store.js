import { storage } from "./util.js";

// Shared state and a tiny event bus so views can react to each other without imports cycles.
const bus = new EventTarget();

export function emit(name, detail) {
    bus.dispatchEvent(new CustomEvent(name, { detail }));
}

export function on(name, handler) {
    const listener = (event) => handler(event.detail);
    bus.addEventListener(name, listener);
    return () => bus.removeEventListener(name, listener);
}

export const shared = {
    system: null,
    stats: null,
    playlists: [],
    libraryRevision: -1,
    route: { view: "download", param: null },
};


const PREFS_KEY = "pyloader.prefs.v1";
const PREF_DEFAULTS = { theme: "auto", autoAnalyze: true, notify: false, resumePlayer: true };

export const prefs = { ...PREF_DEFAULTS, ...storage.get(PREFS_KEY, {}) };

export function setPref(key, value) {
    prefs[key] = value;
    storage.set(PREFS_KEY, prefs);
    emit("prefs", { key, value });
}
