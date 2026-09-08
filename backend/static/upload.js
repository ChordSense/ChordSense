const fileInput = document.querySelector("#file-input");
const chooseFileButton = document.querySelector("#choose-file");
const dropZone = document.querySelector("#drop-zone");
const selection = document.querySelector("#selection");
const selectedName = document.querySelector("#selected-name");
const selectedSize = document.querySelector("#selected-size");
const uploadButton = document.querySelector("#upload-button");
const progressPanel = document.querySelector("#progress-panel");
const progressBar = document.querySelector("#progress-bar");
const progressPercent = document.querySelector("#progress-percent");
const statusMessage = document.querySelector("#status-message");
const connectionBadge = document.querySelector("#connection-badge");
const refreshButton = document.querySelector("#refresh-button");
const libraryList = document.querySelector("#library-list");
const libraryEmpty = document.querySelector("#library-empty");

let selectedFile = null;

function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function setStatus(message, isError = false) {
    statusMessage.textContent = message;
    statusMessage.classList.toggle("error", isError);
}

function selectFile(file) {
    selectedFile = file;
    if (!file) { selection.classList.add("hidden"); return; }
    selectedName.textContent = file.name;
    selectedSize.textContent = formatBytes(file.size);
    selection.classList.remove("hidden");
    setStatus("");
}

function openFilePicker() { fileInput.click(); }
chooseFileButton.addEventListener("click", e => { e.stopPropagation(); openFilePicker(); });
dropZone.addEventListener("click", openFilePicker);
dropZone.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFilePicker(); }
});
fileInput.addEventListener("change", () => selectFile(fileInput.files?.[0] ?? null));

for (const name of ["dragenter", "dragover"]) {
    dropZone.addEventListener(name, e => { e.preventDefault(); dropZone.classList.add("drag-over"); });
}
for (const name of ["dragleave", "drop"]) {
    dropZone.addEventListener(name, e => { e.preventDefault(); dropZone.classList.remove("drag-over"); });
}
dropZone.addEventListener("drop", e => selectFile(e.dataTransfer?.files?.[0] ?? null));

function uploadSelectedFile() {
    if (!selectedFile) { setStatus("Choose an audio file first.", true); return; }
    const formData = new FormData();
    formData.append("file", selectedFile);
    const xhr = new XMLHttpRequest();
    uploadButton.disabled = true;
    progressPanel.classList.remove("hidden");
    progressBar.style.width = "0%";
    progressPercent.textContent = "0%";
    setStatus("Uploading to ChordSense…");

    xhr.upload.addEventListener("progress", e => {
        if (!e.lengthComputable) return;
        const percent = Math.round((e.loaded / e.total) * 100);
        progressBar.style.width = `${percent}%`;
        progressPercent.textContent = `${percent}%`;
    });

    xhr.addEventListener("load", async () => {
        uploadButton.disabled = false;
        let payload;
        try { payload = JSON.parse(xhr.responseText); }
        catch { setStatus("ChordSense returned an invalid response.", true); return; }
        if (xhr.status < 200 || xhr.status >= 300 || !payload.success) {
            setStatus(payload.error ?? "Upload failed.", true); return;
        }
        progressBar.style.width = "100%";
        progressPercent.textContent = "100%";
        setStatus(`${payload.file.name} is now on ChordSense.`);
        selectedFile = null;
        fileInput.value = "";
        selection.classList.add("hidden");
        await refreshLibrary();
    });

    xhr.addEventListener("error", () => {
        uploadButton.disabled = false;
        setStatus("Could not reach ChordSense. Check that both devices are on the same network.", true);
    });

    xhr.open("POST", "/api/uploads");
    xhr.send(formData);
}
uploadButton.addEventListener("click", uploadSelectedFile);

async function deleteFile(name) {
    if (!window.confirm(`Delete "${name}" from ChordSense?`)) return;
    const response = await fetch(`/api/uploads/${encodeURIComponent(name)}`, { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok || !payload.success) { setStatus(payload.error ?? "Could not delete file.", true); return; }
    setStatus(payload.message);
    await refreshLibrary();
}

function createLibraryRow(file) {
    const row = document.createElement("div"); row.className = "library-row";
    const details = document.createElement("div");
    const name = document.createElement("div"); name.className = "library-name"; name.textContent = file.name;
    const meta = document.createElement("div"); meta.className = "library-meta"; meta.textContent = formatBytes(file.size);
    details.append(name, meta);
    const actions = document.createElement("div"); actions.className = "library-actions";
    const preview = document.createElement("a"); preview.className = "button secondary"; preview.href = file.download_url; preview.target = "_blank"; preview.rel = "noopener"; preview.textContent = "Open";
    const remove = document.createElement("button"); remove.className = "button secondary"; remove.type = "button"; remove.textContent = "Delete"; remove.addEventListener("click", () => deleteFile(file.name));
    actions.append(preview, remove); row.append(details, actions); return row;
}

async function refreshLibrary() {
    refreshButton.disabled = true;
    try {
        const response = await fetch("/api/uploads");
        const payload = await response.json();
        if (!response.ok || !payload.success) throw new Error(payload.error ?? "Could not load library.");
        connectionBadge.textContent = "ChordSense connected";
        connectionBadge.classList.add("connected");
        libraryList.replaceChildren();
        const files = payload.files ?? [];
        libraryEmpty.classList.toggle("hidden", files.length !== 0);
        for (const file of files) libraryList.append(createLibraryRow(file));
    } catch (error) {
        connectionBadge.textContent = "ChordSense unavailable";
        connectionBadge.classList.remove("connected");
        setStatus(String(error), true);
    } finally { refreshButton.disabled = false; }
}
refreshButton.addEventListener("click", refreshLibrary);
refreshLibrary();
