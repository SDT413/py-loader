import { api } from "./api.js";
import { emit } from "./store.js";
import { confirmDialog, toast } from "./ui.js";
import { countLabel } from "./util.js";

export async function fetchItems(ids) {
    if (!ids.length) return [];
    const data = await api("/api/library/resolve", { method: "POST", body: { ids } });
    return data.items;
}

export async function setFavorite(ids, favorite) {
    try {
        await api("/api/library/favorite", { method: "POST", body: { ids, favorite } });
        emit("media:updated", { ids, changes: { favorite } });
        if (ids.length > 1) toast(favorite ? `В избранном: ${ids.length}` : `Убрано из избранного: ${ids.length}`);
    } catch (error) { toast(error.message, "error"); }
}

export async function deleteMedia(ids, title = "") {
    const many = ids.length > 1;
    const ok = await confirmDialog({
        title: many ? `Удалить ${countLabel(ids.length, "файл", "файла", "файлов")}?` : "Удалить файл?",
        message: many ? "Файлы будут удалены с диска и из всех плейлистов." : `«${title}» будет удалён с диска и из всех плейлистов.`,
        confirmLabel: "Удалить",
        danger: true,
    });
    if (!ok) return false;
    try {
        const data = await api("/api/library", { method: "DELETE", body: { ids } });
        emit("media:deleted", { ids });
        emit("playlists:changed");
        toast(many ? `Удалено файлов: ${data.deleted}` : "Файл удалён");
        return true;
    } catch (error) {
        toast(error.message, "error");
        return false;
    }
}

export async function sendToPhone(ids) {
    try {
        const status = await api("/api/outbox", { method: "POST", body: { ids } });
        emit("outbox:changed", status);
        const goPhone = { label: "Открыть", onClick: () => { location.hash = "#/phone"; } };
        if (!status.enabled) {
            toast("Добавлено в «Отправленное». Включи доступ с телефона, чтобы забрать файлы", "warning", { action: goPhone, timeout: 7000 });
        } else {
            toast(status.added ? `Отправлено на телефон: ${status.added}` : "Уже среди отправленных", "success", { action: goPhone });
        }
    } catch (error) { toast(error.message, "error"); }
}

export async function revealInFolder(item) {
    try { await api("/api/open-folder", { method: "POST", body: { id: item.id } }); }
    catch (error) { toast(error.message, "error"); }
}
