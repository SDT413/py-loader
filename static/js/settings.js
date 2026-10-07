import { api } from "./api.js";
import { emit, on, prefs, setPref, shared } from "./store.js";
import { confirmDialog, copyText, toast } from "./ui.js";
import { $, $$, escapeHtml, icon } from "./util.js";

const media = window.matchMedia("(prefers-color-scheme: light)");

export function applyTheme() {
    const theme = prefs.theme === "auto" ? (media.matches ? "light" : "dark") : prefs.theme;
    document.documentElement.dataset.theme = theme;
    $$("[data-theme-choice]").forEach((button) => button.classList.toggle("active", button.dataset.themeChoice === prefs.theme));
}

function renderSystem() {
    const system = shared.system;
    if (!system) return;
    const cell = (label, value, extra = "") => `<div class="system-cell"><span>${escapeHtml(label)}</span><strong>${value}${extra}</strong></div>`;
    const status = (ok, good, bad) => `<span class="pill ${ok ? "ok" : "warn"}">${icon(ok ? "check" : "alert")}${ok ? good : bad}</span>`;
    $("#system-info").innerHTML = [
        cell("PyLoader", escapeHtml(system.version)),
        cell("yt-dlp", escapeHtml(system.yt_dlp_version)),
        cell("FFmpeg", status(system.ffmpeg, "Найден", "Не найден — нужен для MP3 и склейки видео")),
        cell("Node.js", status(system.node, "Найден", "Не найден — нужен для части видео YouTube")),
        cell("Папка загрузок", `<span title="${escapeHtml(system.download_root)}">${escapeHtml(system.download_root)}</span>`,
            `<button class="icon-button" type="button" id="open-downloads" title="Открыть" aria-label="Открыть папку">${icon("folder")}</button>`),
        cell("Свободно на диске", `${escapeHtml(system.disk_free_text)} из ${escapeHtml(system.disk_total_text)}`),
        cell("Параллельных загрузок", `${system.workers} <small class="muted">· PYLOADER_WORKERS</small>`),
    ].join("");
}

function bookmarkletHref() {
    const target = `${location.origin}/?url=`;
    return `javascript:(()=>{window.open('${target}'+encodeURIComponent(location.href),'pyloader')})()`;
}

export function init() {
    applyTheme();
    media.addEventListener("change", applyTheme);
    $$("[data-theme-choice]").forEach((button) => button.addEventListener("click", () => {
        setPref("theme", button.dataset.themeChoice);
        applyTheme();
    }));

    const toggles = { "#pref-auto-analyze": "autoAnalyze", "#pref-notify": "notify", "#pref-resume": "resumePlayer" };
    for (const [selector, key] of Object.entries(toggles)) {
        const input = $(selector);
        input.checked = Boolean(prefs[key]);
        input.addEventListener("change", async () => {
            if (key === "notify" && input.checked) {
                if (!("Notification" in window)) {
                    toast("Браузер не поддерживает уведомления", "warning");
                    input.checked = false;
                    return;
                }
                const permission = await Notification.requestPermission();
                if (permission !== "granted") {
                    toast("Уведомления запрещены в настройках браузера", "warning");
                    input.checked = false;
                    return;
                }
            }
            setPref(key, input.checked);
        });
    }

    const bookmarklet = $("#bookmarklet");
    bookmarklet.href = bookmarkletHref();
    bookmarklet.addEventListener("click", (event) => {
        event.preventDefault();
        toast("Перетащи кнопку на панель закладок браузера", "warning");
    });

    $(".settings-layout").addEventListener("click", async (event) => {
        const copy = event.target.closest("[data-copy]");
        if (copy) copyText($(copy.dataset.copy).textContent);
        if (event.target.closest("#open-downloads")) {
            try { await api("/api/open-folder", { method: "POST", body: {} }); }
            catch (error) { toast(error.message, "error"); }
        }
    });

    $("#shutdown-button").addEventListener("click", async () => {
        const ok = await confirmDialog({
            title: "Выключить PyLoader?",
            message: "Активные загрузки будут отменены. Снова запустить можно ярлыком на рабочем столе или PyLoader.bat.",
            confirmLabel: "Выключить",
            danger: true,
        });
        if (!ok) return;
        try {
            await api("/api/shutdown", { method: "POST", body: { confirm: true } });
            document.body.innerHTML = `<div class="empty-state" style="height:100vh"><span class="empty-icon">${icon("logo")}</span><strong>PyLoader выключен</strong><p>Это окно можно закрыть.</p></div>`;
        } catch (error) { toast(error.message, "error"); }
    });

    on("system", renderSystem);
    emit("prefs-ready");
}
