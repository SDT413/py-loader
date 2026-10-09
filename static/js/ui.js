import { $, escapeHtml, icon } from "./util.js";

// ---------- Toasts ----------
export function toast(message, type = "success", options = {}) {
    const { timeout = type === "error" ? 7000 : 4200, action = null } =
        typeof options === "number" ? { timeout: options } : options;
    const region = $("#toast-region");
    const node = document.createElement("div");
    node.className = `toast ${type}`;
    node.setAttribute("role", type === "error" ? "alert" : "status");
    node.innerHTML = `${icon(type === "error" ? "alert" : type === "warning" ? "info" : "check")}<span></span>`;
    node.querySelector("span").textContent = message;
    let timer;
    const dismiss = () => {
        window.clearTimeout(timer);
        node.classList.add("out");
        window.setTimeout(() => node.remove(), 200);
    };
    if (action) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button ghost";
        button.textContent = action.label;
        button.addEventListener("click", () => { dismiss(); action.onClick(); });
        node.append(button);
    }
    region.append(node);
    while (region.children.length > 4) region.firstElementChild.remove();
    timer = window.setTimeout(dismiss, timeout);
    node.addEventListener("mouseenter", () => window.clearTimeout(timer));
    node.addEventListener("mouseleave", () => { timer = window.setTimeout(dismiss, 2000); });
}

export function setButtonLoading(button, loading, label = "Подождите…") {
    if (!button) return;
    if (loading) {
        if (!button.dataset.original) button.dataset.original = button.innerHTML;
        button.disabled = true;
        button.innerHTML = `<span class="spinner"></span>${escapeHtml(label)}`;
    } else {
        button.disabled = false;
        if (button.dataset.original) button.innerHTML = button.dataset.original;
        delete button.dataset.original;
    }
}

export async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
    } catch {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.append(area);
        area.select();
        document.execCommand("copy");
        area.remove();
    }
    toast("Скопировано");
}

// ---------- Dialogs ----------
/**
 * Show the shared <dialog>. The primary action is the form's submit button, so Enter
 * confirms; other actions close with their own value. Resolves with the action value
 * or null when dismissed.
 */
export async function openDialog({ title, message = "", body = null, actions = [], validate = null, onOpen = null, wide = false }) {
    const dialog = $("#modal");
    if (dialog.open) {
        // "close" fires asynchronously; wait so it cannot resolve the new dialog's promise.
        const closed = new Promise((resolve) => dialog.addEventListener("close", resolve, { once: true }));
        dialog.close();
        await closed;
    }
    return new Promise((resolve) => {
        const primary = actions.find((action) => action.primary) || null;
        dialog.style.width = wide ? "min(560px, calc(100vw - 32px))" : "";
        dialog.innerHTML = `<form method="dialog">
            <h2>${escapeHtml(title)}</h2>
            ${message ? `<p>${escapeHtml(message)}</p>` : ""}
            <div class="modal-body"></div>
            <div class="modal-actions"></div>
        </form>`;
        const form = dialog.querySelector("form");
        const bodyNode = dialog.querySelector(".modal-body");
        if (body) bodyNode.append(body); else bodyNode.remove();
        const actionsNode = dialog.querySelector(".modal-actions");
        let result = null;
        for (const action of actions) {
            const button = document.createElement("button");
            button.className = `button ${action.kind || (action.primary ? "primary" : "ghost")}`;
            button.textContent = action.label;
            button.value = action.value;
            if (action.primary) {
                button.type = "submit";
            } else {
                button.type = "button";
                button.addEventListener("click", () => { result = action.value; dialog.close(); });
            }
            actionsNode.append(button);
        }
        if (!actions.length) actionsNode.remove();
        form.addEventListener("submit", (event) => {
            if (!primary) { event.preventDefault(); return; }
            if (validate && validate() === false) { event.preventDefault(); return; }
            result = primary.value;
        });
        dialog.addEventListener("close", () => resolve(result), { once: true });
        dialog.showModal();
        const autofocus = dialog.querySelector("[autofocus]") || dialog.querySelector("input") || dialog.querySelector(".button.primary, .button.danger");
        autofocus?.focus();
        onOpen?.(dialog, (value) => { result = value; dialog.close(); });
    });
}

export async function confirmDialog({ title, message = "", confirmLabel = "ОК", danger = false }) {
    const value = await openDialog({
        title,
        message,
        actions: [
            { label: "Отмена", value: "cancel" },
            { label: confirmLabel, value: "ok", primary: true, kind: danger ? "danger" : "primary" },
        ],
    });
    return value === "ok";
}

export async function promptDialog({ title, message = "", label = "", value = "", placeholder = "", confirmLabel = "Сохранить" }) {
    const wrapper = document.createElement("label");
    wrapper.className = "text-field";
    wrapper.innerHTML = `${label ? `<span>${escapeHtml(label)}</span>` : ""}<input maxlength="80" autocomplete="off">`;
    const input = wrapper.querySelector("input");
    input.value = value;
    input.placeholder = placeholder;
    const result = await openDialog({
        title,
        message,
        body: wrapper,
        actions: [{ label: "Отмена", value: "cancel" }, { label: confirmLabel, value: "ok", primary: true }],
        validate: () => {
            if (input.value.trim()) return true;
            input.focus();
            return false;
        },
        onOpen: () => { input.focus(); input.select(); },
    });
    return result === "ok" ? input.value.trim() : null;
}

// ---------- Context menu ----------
let menuCleanup = null;

export function closeMenu() {
    const menu = $("#context-menu");
    if (menu.hidden) return;
    menu.hidden = true;
    menu.innerHTML = "";
    menuCleanup?.();
    menuCleanup = null;
}

/**
 * items: [{ label, icon, action, danger, disabled } | "-" | { header: "text" }]
 * Opens next to `anchor` (an element), or at `point` ({x, y}) for right-clicks.
 */
export function openMenu(items, { anchor = null, point = null } = {}) {
    closeMenu();
    const menu = $("#context-menu");
    const actions = [];
    menu.innerHTML = items.filter(Boolean).map((item) => {
        if (item === "-") return `<div class="menu-sep" role="separator"></div>`;
        if (item.header) return `<div class="menu-label">${escapeHtml(item.header)}</div>`;
        actions.push(item);
        return `<button class="menu-item ${item.danger ? "danger" : ""}" type="button" role="menuitem" data-index="${actions.length - 1}" ${item.disabled ? "disabled" : ""}>
            ${item.icon ? icon(item.icon) : ""}<span>${escapeHtml(item.label)}</span></button>`;
    }).join("");
    menu.hidden = false;

    const { innerWidth, innerHeight } = window;
    const rect = menu.getBoundingClientRect();
    let left;
    let top;
    if (point) {
        left = point.x;
        top = point.y;
    } else {
        const box = anchor.getBoundingClientRect();
        left = box.right - rect.width;
        top = box.bottom + 6;
        if (top + rect.height > innerHeight - 8) top = box.top - rect.height - 6;
    }
    left = Math.max(8, Math.min(left, innerWidth - rect.width - 8));
    top = Math.max(8, Math.min(top, innerHeight - rect.height - 8));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;

    const buttons = [...menu.querySelectorAll(".menu-item:not(:disabled)")];
    buttons[0]?.focus({ preventScroll: true });

    const onClick = (event) => {
        const button = event.target.closest(".menu-item");
        if (!button || button.disabled) return;
        const item = actions[Number(button.dataset.index)];
        closeMenu();
        anchor?.focus?.({ preventScroll: true });
        item.action?.();
    };
    const onPointerDown = (event) => { if (!menu.contains(event.target)) closeMenu(); };
    const onKey = (event) => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu(); anchor?.focus?.(); return; }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            const index = buttons.indexOf(document.activeElement);
            const next = event.key === "ArrowDown" ? index + 1 : index - 1;
            buttons[(next + buttons.length) % buttons.length]?.focus();
        }
    };
    const onScroll = (event) => { if (!menu.contains(event.target)) closeMenu(); };
    menu.addEventListener("click", onClick);
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", closeMenu);
    menuCleanup = () => {
        menu.removeEventListener("click", onClick);
        document.removeEventListener("pointerdown", onPointerDown, true);
        document.removeEventListener("keydown", onKey, true);
        window.removeEventListener("scroll", onScroll, true);
        window.removeEventListener("resize", closeMenu);
    };
}

// ---------- Document title ----------
const titleState = { jobs: 0, progress: null, playing: null };

export function setTitle(changes) {
    Object.assign(titleState, changes);
    const parts = [];
    if (titleState.jobs) {
        parts.push(titleState.progress !== null ? `↓ ${titleState.progress}% (${titleState.jobs})` : `↓ ${titleState.jobs}`);
    }
    if (titleState.playing) parts.push(`▶ ${titleState.playing}`);
    parts.push("PyLoader");
    document.title = parts.join(" · ");
}
