import { openTrackMenu } from "./actions.js";
import { setFavorite } from "./media.js";
import * as player from "./player.js";

export const DRAG_IDS = "application/x-pyloader-ids";
const DRAG_REORDER = "application/x-pyloader-reorder";

export function applySelection(container, selection) {
    for (const node of container.querySelectorAll("[data-id]")) {
        const selected = selection.has(node.dataset.id);
        node.classList.toggle("selected", selected);
        const box = node.querySelector("[data-select]");
        if (box) box.checked = selected;
    }
}

/**
 * Event delegation shared by the library and playlist views.
 * options.getItems() returns the rendered items in order; options.selection is a Set of ids
 * (or null when the list has no selection), options.onReorder(ids) enables drag reordering.
 */
export function bindTrackList(container, options) {
    const { getItems, selection = null, onSelectionChange = () => {}, onSelectAll = null, playlistId = () => null, onReorder = null } = options;
    let anchor = null;
    let dragId = null;

    const nodeItem = (node) => getItems()[Number(node.dataset.index)];

    const play = (item, index) => {
        if (player.current()?.id === item.id) { player.toggle(); return; }
        player.playItems(getItems(), index);
    };

    const select = (index, checked, range) => {
        const items = getItems();
        if (range && anchor !== null) {
            const [from, to] = anchor < index ? [anchor, index] : [index, anchor];
            for (let position = from; position <= to; position += 1) {
                if (checked) selection.add(items[position].id); else selection.delete(items[position].id);
            }
        } else if (checked) {
            selection.add(items[index].id);
        } else {
            selection.delete(items[index].id);
        }
        anchor = index;
        applySelection(container, selection);
        onSelectionChange();
    };

    container.addEventListener("click", (event) => {
        if (event.target.closest("[data-select-all]")) {
            onSelectAll?.(event.target.checked);
            return;
        }
        const node = event.target.closest("[data-id]");
        if (!node) return;
        const index = Number(node.dataset.index);
        const item = nodeItem(node);
        if (!item) return;
        const box = event.target.closest("[data-select]");
        if (box && selection) { select(index, box.checked, event.shiftKey); return; }
        if (event.target.closest(".check")) return;
        const actionButton = event.target.closest("[data-action]");
        if (actionButton) {
            const action = actionButton.dataset.action;
            if (action === "play") play(item, index);
            if (action === "favorite") setFavorite([item.id], !item.favorite);
            if (action === "menu") openTrackMenu(item, { anchor: actionButton, selection: selection ? [...selection] : [], playlistId: playlistId() });
            return;
        }
        if (event.target.closest("a, button, input")) return;
        if (selection && (event.ctrlKey || event.metaKey || event.shiftKey || selection.size)) {
            select(index, !selection.has(item.id), event.shiftKey);
        }
    });

    container.addEventListener("dblclick", (event) => {
        const node = event.target.closest("[data-id]");
        if (!node || event.target.closest("button, a, input, .check")) return;
        play(nodeItem(node), Number(node.dataset.index));
    });

    container.addEventListener("contextmenu", (event) => {
        const node = event.target.closest("[data-id]");
        if (!node) return;
        event.preventDefault();
        openTrackMenu(nodeItem(node), {
            point: { x: event.clientX, y: event.clientY },
            selection: selection ? [...selection] : [],
            playlistId: playlistId(),
        });
    });

    container.addEventListener("dragstart", (event) => {
        const node = event.target.closest("[data-id]");
        if (!node) return;
        const id = node.dataset.id;
        const ids = selection && selection.has(id) && selection.size > 1 ? [...selection] : [id];
        event.dataTransfer.effectAllowed = onReorder ? "copyMove" : "copy";
        event.dataTransfer.setData(DRAG_IDS, JSON.stringify(ids));
        if (onReorder) {
            event.dataTransfer.setData(DRAG_REORDER, id);
            dragId = id;
            node.classList.add("dragging");
        }
    });

    container.addEventListener("dragend", () => {
        dragId = null;
        container.querySelectorAll(".dragging, .drag-over-top, .drag-over-bottom").forEach((node) => {
            node.classList.remove("dragging", "drag-over-top", "drag-over-bottom");
        });
    });

    if (!onReorder) return;
    container.addEventListener("dragover", (event) => {
        if (!dragId) return;
        const node = event.target.closest("[data-id]");
        if (!node) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        const box = node.getBoundingClientRect();
        const after = event.clientY > box.top + box.height / 2;
        container.querySelectorAll(".drag-over-top, .drag-over-bottom").forEach((other) => other.classList.remove("drag-over-top", "drag-over-bottom"));
        node.classList.add(after ? "drag-over-bottom" : "drag-over-top");
    });
    container.addEventListener("drop", (event) => {
        if (!dragId) return;
        const node = event.target.closest("[data-id]");
        if (!node) return;
        event.preventDefault();
        const after = node.classList.contains("drag-over-bottom");
        const ids = getItems().map((item) => item.id).filter((id) => id !== dragId);
        const targetIndex = ids.indexOf(node.dataset.id);
        if (targetIndex === -1 && node.dataset.id !== dragId) return;
        if (node.dataset.id !== dragId) ids.splice(targetIndex + (after ? 1 : 0), 0, dragId);
        else return;
        onReorder(ids);
    });
}
