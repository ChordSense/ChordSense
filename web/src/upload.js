import { apiRequest, apiEndpoint, apiHeaders, beginTransfer, validateAudioFile, handleUploadError } from "./api-client.js";
import "./recordings.js";
import { initConnectionSettings } from "./connection-settings.js";

const fileInput = document.querySelector("#file-input");
const chooseButton = document.querySelector("#choose-file");
const dropZone = document.querySelector("#drop-zone");
const selection = document.querySelector("#selection");
const uploadButton = document.querySelector("#upload-button");
const progressPanel = document.querySelector("#progress-panel");
const progressBar = document.querySelector("#progress-bar");
const progressLabel = document.querySelector("#progress-label");
const progressPercent = document.querySelector("#progress-percent");
const statusMessage = document.querySelector("#status-message");
const connectionStatus = document.querySelector("#connection-status");
const refreshButton = document.querySelector("#refresh-button");
const libraryList = document.querySelector("#library-list");
const libraryEmpty = document.querySelector("#library-empty");
let selectedFile = null;
let uploading = false;
let refreshing = false;

function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function setStatus(message, error = false) {
    statusMessage.textContent = message;
    statusMessage.classList.toggle("error", error);
}

function selectFile(file) {
    selectedFile = null;
    selection.classList.add("hidden");
    if (!file) return;
    try { validateAudioFile(file); }
    catch (error) { setStatus(error.message, true); return; }
    selectedFile = file;
    document.querySelector("#selected-name").textContent = file.name;
    document.querySelector("#selected-size").textContent = formatBytes(file.size);
    selection.classList.remove("hidden");
    setStatus("");
}

chooseButton.addEventListener("click", event => { event.stopPropagation(); fileInput.click(); });
dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); fileInput.click(); }
});
fileInput.addEventListener("change", () => selectFile(fileInput.files?.[0] ?? null));
for (const name of ["dragenter", "dragover"]) {
    dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.add("drag-over"); });
}
for (const name of ["dragleave", "drop"]) {
    dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.remove("drag-over"); });
}
dropZone.addEventListener("drop", event => selectFile(event.dataTransfer?.files?.[0] ?? null));

function uploadSelectedFile() {
    if (uploading) return;
    if (!selectedFile) { setStatus("Choose a song first.", true); return; }
    const file = selectedFile;
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    let release;
    try {
        xhr.open("POST", apiEndpoint("/api/uploads"));
        for (const [name, value] of Object.entries(apiHeaders())) xhr.setRequestHeader(name, value);
        release = beginTransfer();
    } catch (error) { setStatus(error.message, true); return; }
    uploading = true;
    uploadButton.disabled = true;
    xhr.timeout = 15 * 60 * 1000;
    progressPanel.classList.remove("hidden");
    progressBar.style.width = "0%";
    progressPercent.textContent = "0%";
    progressLabel.textContent = "Sending songâ€¦";
    setStatus("Sending to ChordSenseâ€¦");
    const finish = () => { release(); uploading = false; uploadButton.disabled = false; };
    const failed = message => {
        finish();
        progressLabel.textContent = "Upload failed";
        progressPercent.textContent = "";
        setStatus(message, true);
    };
    xhr.upload.addEventListener("progress", event => {
        if (!event.lengthComputable) return;
        const percent = Math.round(event.loaded / event.total * 100);
        progressBar.style.width = `${percent}%`;
        progressPercent.textContent = `${percent}%`;
    });
    xhr.addEventListener("load", async () => {
        finish();
        let payload;
        try { payload = JSON.parse(xhr.responseText); }
        catch { failed(`Device returned an invalid upload response (HTTP ${xhr.status}).`); return; }
        if (xhr.status < 200 || xhr.status >= 300 || !payload.success) {
            failed(handleUploadError(payload, xhr.status).message);
            return;
        }
        progressBar.style.width = "100%";
        progressPercent.textContent = "100%";
        progressLabel.textContent = "Uploaded";
        setStatus(`${payload.file?.name ?? file.name} was added to ChordSense.`);
        if (selectedFile === file) { selectedFile = null; fileInput.value = ""; selection.classList.add("hidden"); }
        await refreshLibrary();
    });
    xhr.addEventListener("error", () => failed("Could not reach ChordSense. Check the device connection."));
    xhr.addEventListener("timeout", () => failed("The upload took too long. Try again on a faster connection."));
    xhr.addEventListener("abort", () => failed("Upload cancelled."));
    try { xhr.send(form); }
    catch (error) { failed(error.message || "Could not send the song."); }
}
uploadButton.addEventListener("click", uploadSelectedFile);

async function deleteFile(name, button) {
    if (!window.confirm(`Delete "${name}" from ChordSense?`)) return;
    button.disabled = true;
    try {
        await apiRequest(`/api/uploads/${encodeURIComponent(name)}`, { method: "DELETE" });
        setStatus(`${name} was deleted.`);
        await refreshLibrary();
    } catch (error) { setStatus(error.message, true); }
    finally { button.disabled = false; }
}

async function downloadFile(file, button) {
    button.disabled = true;
    try {
        const blob = await apiRequest(`/api/uploads/${encodeURIComponent(file.name)}`, { blob: true });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = file.name;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (error) { setStatus(error.message, true); }
    finally { button.disabled = false; }
}

function createLibraryRow(file) {
    const row = document.createElement("div");
    row.className = "library-row";
    const details = document.createElement("div");
    const name = document.createElement("div");
    name.className = "library-name";
    name.textContent = file.name;
    const meta = document.createElement("div");
    meta.className = "library-meta";
    meta.textContent = formatBytes(file.size);
    details.append(name, meta);
    const actions = document.createElement("div");
    actions.className = "library-actions";
    const download = document.createElement("button");
    download.className = "button secondary";
    download.type = "button";
    download.textContent = "Download";
    download.addEventListener("click", () => downloadFile(file, download));
    const remove = document.createElement("button");
    remove.className = "button secondary";
    remove.type = "button";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => deleteFile(file.name, remove));
    actions.append(download, remove);
    row.append(details, actions);
    return row;
}

async function refreshLibrary() {
    if (refreshing) return;
    refreshing = true;
    refreshButton.disabled = true;
    try {
        const payload = await apiRequest("/api/uploads");
        const files = payload.files ?? [];
        if (!Array.isArray(files)) throw new Error("Device returned an invalid song library.");
        connectionStatus.textContent = "Connected to ChordSense";
        connectionStatus.classList.add("connected");
        connectionStatus.classList.remove("error");
        libraryList.replaceChildren(...files.map(createLibraryRow));
        libraryEmpty.classList.toggle("hidden", files.length !== 0);
    } catch (error) {
        connectionStatus.textContent = "ChordSense unavailable";
        connectionStatus.classList.remove("connected");
        connectionStatus.classList.add("error");
        libraryList.replaceChildren();
        setStatus(error.message, true);
    } finally { refreshing = false; refreshButton.disabled = false; }
}

refreshButton.addEventListener("click", refreshLibrary);
document.addEventListener("device-connected", refreshLibrary);
document.addEventListener("device-connection-failed", () => {
    connectionStatus.textContent = "ChordSense unavailable";
    connectionStatus.classList.remove("connected");
    connectionStatus.classList.add("error");
    libraryList.replaceChildren();
});
document.addEventListener("device-session-expired", () => {
    connectionStatus.textContent = "Pair with ChordSense again to manage songs and recordings.";
    connectionStatus.classList.remove("connected");
    connectionStatus.classList.add("error");
    libraryList.replaceChildren();
    libraryEmpty.classList.add("hidden");
});
document.addEventListener("device-disconnected", () => {
    connectionStatus.textContent = "Pair with ChordSense to manage songs and recordings.";
    connectionStatus.classList.remove("connected", "error");
    libraryList.replaceChildren();
    libraryEmpty.classList.add("hidden");
    progressPanel.classList.add("hidden");
    setStatus("");
});
initConnectionSettings();
