// Minimal phone receiver for the PyLoader bridge. The future mobile app talks to the same /api/v1.
(() => {
    const $ = (selector) => document.querySelector(selector);
    const content = $("#content");
    const audio = $("#audio");
    const state = { tab: "outbox", search: "", items: [], queue: [], index: -1, playlist: null };

    const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
    const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
    const duration = (seconds) => {
        const value = Math.round(Number(seconds || 0));
        if (!value) return "";
        const h = Math.floor(value / 3600);
        const m = Math.floor((value % 3600) / 60);
        const s = String(value % 60).padStart(2, "0");
        return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
    };
    const size = (bytes) => {
        let value = Number(bytes || 0);
        for (const unit of ["B", "KB", "MB", "GB"]) {
            if (value < 1024 || unit === "GB") return unit === "B" ? `${value} B` : `${value.toFixed(1)} ${unit}`;
            value /= 1024;
        }
        return "";
    };

    async function api(path) {
        const response = await fetch(path, { credentials: "same-origin" });
        const data = await response.json().catch(() => ({}));
        if (response.status === 401) { location.href = "/"; throw new Error("unpaired"); }
        if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
    }

    function rowHtml(item, index) {
        const current = state.queue[state.index]?.id === item.id ? "current" : "";
        const cover = item.cover_url ? `<img src="${esc(item.cover_url)}" alt="" loading="lazy">` : icon(item.media_type === "video" ? "film" : "library");
        const sub = [item.artist, duration(item.duration), item.format?.toUpperCase(), size(item.size)].filter(Boolean).join(" · ");
        const delivered = item.delivered_at ? `<span class="badge">${icon("check")}сохранено</span>` : "";
        return `<div class="row ${current}" data-index="${index}">
            <div class="cover">${cover}</div>
            <div class="meta"><div class="title">${esc(item.title)}</div><div class="sub">${esc(sub)}${delivered}</div></div>
            <a class="save ${item.delivered_at ? "done" : ""}" href="${esc(item.download_url)}" download aria-label="Сохранить на телефон">${icon("download")}</a>
        </div>`;
    }

    function renderList(items, emptyText) {
        state.items = items;
        const query = state.search.toLocaleLowerCase();
        const visible = items.map((item, index) => ({ item, index }))
            .filter(({ item }) => !query || `${item.title} ${item.artist || ""}`.toLocaleLowerCase().includes(query));
        const header = state.playlist
            ? `<button class="back" type="button" data-back>${icon("chevron-left")}Плейлисты</button><div class="play-all"><button type="button" data-play-all>${icon("play")}Слушать</button><button class="ghost" type="button" data-shuffle>${icon("shuffle")}Перемешать</button></div>`
            : state.tab === "outbox" && items.length
                ? `<p class="hint">Файлы, которые ты отправил с компьютера. Нажми на трек, чтобы слушать, или ${icon("download")} — чтобы сохранить.</p>`
                : "";
        content.innerHTML = header + (visible.length
            ? visible.map(({ item, index }) => rowHtml(item, index)).join("")
            : `<div class="empty">${icon(state.tab === "outbox" ? "send" : "library")}<strong>Пусто</strong><span>${esc(emptyText)}</span></div>`);
    }

    async function load() {
        content.innerHTML = `<div class="empty"><span>Загрузка…</span></div>`;
        try {
            if (state.playlist) {
                const data = await api(`/api/v1/playlists/${encodeURIComponent(state.playlist)}`);
                renderList(data.items, "В плейлисте пока нет треков");
            } else if (state.tab === "outbox") {
                renderList((await api("/api/v1/outbox")).items, "На компьютере выбери файлы и нажми «На телефон»");
            } else if (state.tab === "playlists") {
                const data = await api("/api/v1/playlists");
                content.innerHTML = data.playlists.length
                    ? data.playlists.map((row) => `<div class="card" data-playlist="${esc(row.id)}">${icon("playlist")}<div><strong>${esc(row.name)}</strong><small>${row.count} · ${duration(row.duration) || "—"}</small></div></div>`).join("")
                    : `<div class="empty">${icon("playlist")}<strong>Плейлистов нет</strong><span>Создай плейлист в PyLoader на компьютере.</span></div>`;
            } else {
                renderList((await api(`/api/v1/library?type=${state.tab}`)).items, "Здесь появятся скачанные файлы");
            }
        } catch (error) {
            if (error.message !== "unpaired") content.innerHTML = `<div class="empty"><strong>Нет связи с компьютером</strong><span>${esc(error.message)}</span></div>`;
        }
    }

    function play(items, index) {
        const item = items[index];
        if (!item) return;
        if (item.media_type === "video") { window.open(item.stream_url, "_blank"); return; }
        state.queue = items.filter((row) => row.media_type !== "video");
        state.index = state.queue.findIndex((row) => row.id === item.id);
        start();
    }

    function start() {
        const item = state.queue[state.index];
        if (!item) return;
        audio.src = item.stream_url;
        audio.play().catch(() => {});
        $("#mini").hidden = false;
        $("#mini-title").textContent = item.title;
        $("#mini-artist").textContent = item.artist || item.playlist_title || "";
        $("#mini-cover").src = item.cover_url || "data:image/gif;base64,R0lGODlhAQABAAAAACw=";
        if ("mediaSession" in navigator) {
            navigator.mediaSession.metadata = new MediaMetadata({
                title: item.title, artist: item.artist || "", album: item.playlist_title || "PyLoader",
                artwork: item.cover_url ? [{ src: new URL(item.cover_url, location.href).href, sizes: "480x360" }] : [],
            });
        }
        document.querySelectorAll(".row").forEach((row) => row.classList.toggle("current", state.items[Number(row.dataset.index)]?.id === item.id));
    }

    const step = (delta) => {
        if (!state.queue.length) return;
        state.index = (state.index + delta + state.queue.length) % state.queue.length;
        start();
    };

    document.querySelector("#tabs").addEventListener("click", (event) => {
        const tab = event.target.closest("[data-tab]");
        if (!tab) return;
        state.tab = tab.dataset.tab;
        state.playlist = null;
        document.querySelectorAll(".tab").forEach((node) => node.classList.toggle("active", node === tab));
        load();
    });
    $("#search").addEventListener("input", (event) => { state.search = event.target.value.trim(); if (state.tab !== "playlists" || state.playlist) renderList(state.items, "Ничего не найдено"); });
    content.addEventListener("click", (event) => {
        if (event.target.closest(".save")) {
            const row = event.target.closest(".row");
            window.setTimeout(() => row?.querySelector(".save")?.classList.add("done"), 400);
            return;
        }
        if (event.target.closest("[data-back]")) { state.playlist = null; load(); return; }
        if (event.target.closest("[data-play-all]")) { play(state.items, 0); return; }
        if (event.target.closest("[data-shuffle]")) {
            const queue = state.items.filter((item) => item.media_type !== "video");
            for (let index = queue.length - 1; index > 0; index -= 1) {
                const swap = Math.floor(Math.random() * (index + 1));
                [queue[index], queue[swap]] = [queue[swap], queue[index]];
            }
            if (!queue.length) return;
            state.queue = queue;
            state.index = 0;
            start();
            return;
        }
        const playlist = event.target.closest("[data-playlist]");
        if (playlist) { state.playlist = playlist.dataset.playlist; load(); return; }
        const row = event.target.closest(".row");
        if (row) play(state.items, Number(row.dataset.index));
    });
    $("#mini-toggle").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
    $("#mini-next").addEventListener("click", () => step(1));
    $("#mini-prev").addEventListener("click", () => step(-1));
    audio.addEventListener("ended", () => step(1));
    audio.addEventListener("play", () => { $("#mini-toggle").innerHTML = icon("pause"); });
    audio.addEventListener("pause", () => { $("#mini-toggle").innerHTML = icon("play"); });
    audio.addEventListener("timeupdate", () => {
        $("#mini-fill").style.width = audio.duration ? `${(audio.currentTime / audio.duration) * 100}%` : "0";
    });
    if ("mediaSession" in navigator) {
        navigator.mediaSession.setActionHandler("play", () => audio.play());
        navigator.mediaSession.setActionHandler("pause", () => audio.pause());
        navigator.mediaSession.setActionHandler("nexttrack", () => step(1));
        navigator.mediaSession.setActionHandler("previoustrack", () => step(-1));
    }
    load();
})();
