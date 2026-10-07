export async function api(url, options = {}) {
    const init = { ...options, headers: { ...(options.headers || {}) } };
    if (init.body !== undefined && typeof init.body !== "string") {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(init.body);
    }
    let response;
    try {
        response = await fetch(url, init);
    } catch {
        throw new Error("PyLoader не отвечает — проверь, что окно сервера открыто");
    }
    let data;
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
}
