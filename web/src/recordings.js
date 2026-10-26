import { apiRequest, beginTransfer } from "./api-client.js";

const list = document.querySelector("#recordings-list");
const empty = document.querySelector("#recordings-empty");
const count = document.querySelector("#recordings-count");
const status = document.querySelector("#recordings-status");
const refresh = document.querySelector("#refresh-recordings");
const downloadAll = document.querySelector("#download-recordings");
let refreshing = false;
let downloading = false;
let recordings = [];
let generation = 0;

function message(text, error = false) {
    status.textContent = text;
    status.classList.toggle("error", error);
}

function updateButtons() {
    refresh.disabled = refreshing || downloading;
    downloadAll.disabled = refreshing || downloading || !recordings.length || recordings.some(item => !item.ready);
    for (const button of list.querySelectorAll("button")) {
        button.disabled = downloading || button.dataset.ready !== "true";
    }
}

async function download(path, filename) {
    if (downloading) return;
    downloading = true;
    const release = beginTransfer();
    updateButtons();
    message("Preparing download…");
    try {
        const blob = await apiRequest(path, { blob: true });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10_000);
        message("Download sent to your browser. Check your Downloads folder before deleting any originals.");
    } catch (error) { message(error.message, true); }
    finally { release(); downloading = false; updateButtons(); }
}

function row(item) {
    const element = document.createElement("div");
    element.className = "library-row";
    const details = document.createElement("div");
    const title = document.createElement("div");
    title.className = "library-name";
    title.textContent = item.name;
    const meta = document.createElement("div");
    meta.className = "library-meta";
    meta.textContent = `${item.filename} · ${(item.size / 1024 / 1024).toFixed(1)} MB · ${item.ready ? `${item.duration.toFixed(1)} seconds` : "Not ready — stop recording or check this take"}`;
    details.append(title, meta);
    const button = document.createElement("button");
    button.className = "button secondary";
    button.type = "button";
    button.textContent = "Download WAV";
    button.dataset.ready = String(item.ready);
    button.addEventListener("click", () => download(item.download_url, item.filename));
    element.append(details, button);
    return element;
}

async function refreshRecordings() {
    if (refreshing || downloading) return;
    const current = generation;
    refreshing = true;
    updateButtons();
    message("Loading recordings…");
    try {
        const payload = await apiRequest("/api/recordings");
        if (current !== generation) return;
        if (!Array.isArray(payload.recordings)) throw new Error("Device returned an invalid recordings list.");
        recordings = payload.recordings;
        list.replaceChildren(...recordings.map(row));
        empty.classList.toggle("hidden", recordings.length !== 0);
        count.textContent = `${recordings.length} / ${payload.limit} recordings`;
        message(recordings.some(item => !item.ready) ? "Some takes are not ready. Stop recording and refresh. Finished takes can still be downloaded individually." : "");
    } catch (error) {
        if (current !== generation) return;
        recordings = [];
        list.replaceChildren();
        empty.classList.add("hidden");
        count.textContent = "";
        message(error.status === 403 || error.status === 404 ? "Update the ChordSense backend to enable recording downloads." : error.message, true);
    } finally { refreshing = false; updateButtons(); }
}

function clearRecordings() {
    generation += 1;
    recordings = [];
    list.replaceChildren();
    count.textContent = "";
    empty.classList.add("hidden");
    message("Pair with ChordSense to download recordings.");
    updateButtons();
}
refresh.addEventListener("click", refreshRecordings);
downloadAll.addEventListener("click", () => download("/api/recordings/export", "chordsense-recordings.zip"));
document.addEventListener("device-connected", refreshRecordings);
for (const name of ["device-disconnected", "device-session-expired", "device-connection-failed"]) {
    document.addEventListener(name, clearRecordings);
}
