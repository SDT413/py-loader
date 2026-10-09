import { api } from "./api.js";
import { coverHtml } from "./render.js";
import { on } from "./store.js";
import { confirmDialog, copyText, toast } from "./ui.js";
import { $, escapeHtml, icon, relativeTime } from "./util.js";

const state = { status: null, active: false, timer: null, qrFor: null };

function render() {
    const status = state.status;
    if (!status) return;
    $("#nav-phone-dot").classList.toggle("hidden", !status.enabled);
    $("#bridge-toggle").checked = status.enabled;
    $("#pairing-on").classList.toggle("hidden", !status.enabled);
    $("#pairing-off").classList.toggle("hidden", status.enabled);
    $("#bridge-status-text").textContent = status.enabled
        ? `Включён · ${status.address}:${status.port}`
        : "Выключен — компьютер не виден в сети";
    $("#bridge-error").classList.toggle("hidden", !status.error);
    $("#bridge-error").textContent = status.error || "";
    if (status.enabled) {
        $("#bridge-url").textContent = status.url;
        if (state.qrFor !== status.connect_url) {
            state.qrFor = status.connect_url;
            $("#bridge-qr").src = `/api/bridge/qr.svg?v=${encodeURIComponent(status.address)}-${Date.now()}`;
        }
        const select = $("#bridge-address");
        $("#bridge-address-field").classList.toggle("hidden", status.addresses.length < 2);
        const options = status.addresses.join("|");
        if (select.dataset.options !== options) {
            select.dataset.options = options;
            select.innerHTML = status.addresses.map((address) => `<option value="${escapeHtml(address)}">${escapeHtml(address)}</option>`).join("");
        }
        if (document.activeElement !== select) select.value = status.address;
    }
    renderOutbox();
}

function renderOutbox() {
    const items = state.status?.outbox || [];
    $("#outbox-count").textContent = items.length;
    $("#outbox-empty").classList.toggle("hidden", items.length > 0);
    $("#outbox-clear-delivered").disabled = !items.some((item) => item.delivered_at);
    $("#outbox-clear-all").disabled = !items.length;
    $("#outbox-list").innerHTML = items.map((item) => `<div class="outbox-row" data-id="${escapeHtml(item.id)}">
        <span class="track-cover">${coverHtml(item, { play: false })}</span>
        <div class="track-main"><div class="track-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
            <div class="track-sub">${escapeHtml([String(item.format || "").toUpperCase(), item.size_text, `отправлено ${relativeTime(item.sent_at)}`].join(" · "))}</div></div>
        ${item.delivered_at
            ? `<span class="pill ok" title="${escapeHtml(new Date(item.delivered_at).toLocaleString("ru-RU"))}">${icon("check")}Получено</span>`
            : `<span class="pill muted">Ждёт телефон</span>`}
        <button class="icon-button" type="button" data-outbox-remove="${escapeHtml(item.id)}" title="Убрать" aria-label="Убрать">${icon("x")}</button>
    </div>`).join("");
    const pending = state.status?.outbox_pending || 0;
    $("#nav-phone-dot").title = pending ? `Ждут на телефоне: ${pending}` : "Доступ с телефона включён";
}

export async function refreshStatus() {
    try {
        state.status = await api("/api/bridge");
        render();
    } catch (error) { if (state.active) toast(error.message, "error"); }
}

async function update(body, button = null) {
    if (button) button.disabled = true;
    try {
        state.status = await api("/api/bridge", { method: "POST", body });
        render();
        return true;
    } catch (error) {
        toast(error.message, "error", 8000);
        await refreshStatus();
        return false;
    } finally {
        if (button) button.disabled = false;
    }
}

export function enter() {
    state.active = true;
    refreshStatus();
    window.clearInterval(state.timer);
    state.timer = window.setInterval(() => { if (!document.hidden) refreshStatus(); }, 4000);
}

export function leave() {
    state.active = false;
    window.clearInterval(state.timer);
}

export function init() {
    $("#bridge-toggle").addEventListener("change", async (event) => {
        const enabled = event.target.checked;
        const ok = await update({ enabled }, event.target);
        if (ok && enabled) toast("Доступ с телефона включён — отсканируй QR-код");
    });
    $("#bridge-address").addEventListener("change", (event) => update({ address: event.target.value }));
    $("#bridge-copy").addEventListener("click", () => { if (state.status) copyText(state.status.connect_url); });
    $("#bridge-rotate").addEventListener("click", async () => {
        const ok = await confirmDialog({
            title: "Сменить ключ доступа?",
            message: "Все подключённые телефоны потеряют доступ, пока снова не отсканируют новый QR-код.",
            confirmLabel: "Сменить ключ",
            danger: true,
        });
        if (!ok) return;
        try {
            state.status = await api("/api/bridge/token", { method: "POST" });
            render();
            toast("Ключ обновлён — старые подключения отключены");
        } catch (error) { toast(error.message, "error"); }
    });
    $("#outbox-list").addEventListener("click", async (event) => {
        const button = event.target.closest("[data-outbox-remove]");
        if (!button) return;
        try {
            state.status = await api("/api/outbox", { method: "DELETE", body: { ids: [button.dataset.outboxRemove] } });
            render();
        } catch (error) { toast(error.message, "error"); }
    });
    $("#outbox-clear-delivered").addEventListener("click", async () => {
        try {
            state.status = await api("/api/outbox", { method: "DELETE", body: { delivered: true } });
            render();
        } catch (error) { toast(error.message, "error"); }
    });
    $("#outbox-clear-all").addEventListener("click", async () => {
        const ok = await confirmDialog({ title: "Очистить «Отправленное»?", message: "Файлы останутся в медиатеке, просто исчезнут из списка для телефона.", confirmLabel: "Очистить" });
        if (!ok) return;
        try {
            state.status = await api("/api/outbox", { method: "DELETE", body: { all: true } });
            render();
        } catch (error) { toast(error.message, "error"); }
    });
    on("outbox:changed", (status) => { state.status = status; render(); });
    on("media:deleted", () => refreshStatus());
}
