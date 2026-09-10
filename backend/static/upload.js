const fileInput =
    document.querySelector("#file-input");

const chooseFileButton =
    document.querySelector("#choose-file");

const dropZone =
    document.querySelector("#drop-zone");

const selection =
    document.querySelector("#selection");

const selectedName =
    document.querySelector("#selected-name");

const selectedSize =
    document.querySelector("#selected-size");

const uploadButton =
    document.querySelector("#upload-button");

const progressPanel =
    document.querySelector("#progress-panel");

const progressBar =
    document.querySelector("#progress-bar");

const progressLabel =
    document.querySelector("#progress-label");

const progressPercent =
    document.querySelector("#progress-percent");

const statusMessage =
    document.querySelector("#status-message");

const connectionStatus =
    document.querySelector(
        "#connection-status"
    );

const refreshButton =
    document.querySelector("#refresh-button");

const libraryList =
    document.querySelector("#library-list");

const libraryEmpty =
    document.querySelector("#library-empty");


let selectedFile = null;


function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) {
        return "0 B";
    }

    const units = [
        "B",
        "KB",
        "MB",
        "GB"
    ];

    const index = Math.min(
        Math.floor(
            Math.log(bytes) /
            Math.log(1024)
        ),
        units.length - 1
    );

    const value =
        bytes /
        Math.pow(1024, index);

    return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}


function setStatus(message, isError = false) {
    statusMessage.textContent =
        message;

    statusMessage.classList.toggle(
        "error",
        isError
    );
}


function selectFile(file) {
    selectedFile = file;

    if (!file) {
        selection.classList.add(
            "hidden"
        );

        return;
    }

    selectedName.textContent =
        file.name;

    selectedSize.textContent =
        formatBytes(file.size);

    selection.classList.remove(
        "hidden"
    );

    setStatus("");
}


function openFilePicker() {
    fileInput.click();
}


chooseFileButton.addEventListener(
    "click",
    event => {
        event.stopPropagation();
        openFilePicker();
    }
);


dropZone.addEventListener(
    "click",
    openFilePicker
);


dropZone.addEventListener(
    "keydown",
    event => {
        if (
            event.key === "Enter" ||
            event.key === " "
        ) {
            event.preventDefault();
            openFilePicker();
        }
    }
);


fileInput.addEventListener(
    "change",
    () => {
        selectFile(
            fileInput.files?.[0] ?? null
        );
    }
);


for (const eventName of [
    "dragenter",
    "dragover"
]) {
    dropZone.addEventListener(
        eventName,
        event => {
            event.preventDefault();

            dropZone.classList.add(
                "drag-over"
            );
        }
    );
}


for (const eventName of [
    "dragleave",
    "drop"
]) {
    dropZone.addEventListener(
        eventName,
        event => {
            event.preventDefault();

            dropZone.classList.remove(
                "drag-over"
            );
        }
    );
}


dropZone.addEventListener(
    "drop",
    event => {
        selectFile(
            event.dataTransfer
                ?.files?.[0] ?? null
        );
    }
);


function uploadSelectedFile() {
    if (!selectedFile) {
        setStatus(
            "Choose a song first.",
            true
        );

        return;
    }

    const formData =
        new FormData();

    formData.append(
        "file",
        selectedFile
    );

    const xhr =
        new XMLHttpRequest();

    uploadButton.disabled = true;

    progressPanel.classList.remove(
        "hidden"
    );

    progressBar.style.width =
        "0%";

    progressPercent.textContent =
        "0%";

    progressLabel.textContent =
        "Sending song...";

    setStatus(
        "Sending to ChordSense..."
    );


    xhr.upload.addEventListener(
        "progress",
        event => {
            if (!event.lengthComputable) {
                return;
            }

            const percent =
                Math.round(
                    (
                        event.loaded /
                        event.total
                    ) * 100
                );

            progressBar.style.width =
                `${percent}%`;

            progressPercent.textContent =
                `${percent}%`;
        }
    );


    xhr.addEventListener(
        "load",
        async () => {
            uploadButton.disabled =
                false;

            let payload;

            try {
                payload =
                    JSON.parse(
                        xhr.responseText
                    );
            } catch {
                setStatus(
                    "Upload failed.",
                    true
                );

                return;
            }

            if (
                xhr.status < 200 ||
                xhr.status >= 300 ||
                !payload.success
            ) {
                progressLabel.textContent =
                    "Something went wrong...";

                progressPercent.textContent =
                    "";

                setStatus(
                    payload.error ??
                    "Upload failed.",
                    true
                );

                return;
            }

            progressBar.style.width =
                "100%";

            progressPercent.textContent =
                "100%";

            progressLabel.textContent =
                "Uploaded";

            setStatus(
                `${payload.file.name} was added to ChordSense.`
            );

            selectedFile = null;

            fileInput.value = "";

            selection.classList.add(
                "hidden"
            );

            await refreshLibrary();
        }
    );


    xhr.addEventListener(
        "error",
        () => {
            uploadButton.disabled =
                false;

            progressLabel.textContent =
                "Something went wrong...";

            progressPercent.textContent =
                "";

            setStatus(
                "Could not reach ChordSense.",
                true
            );
        }
    );


    xhr.open(
        "POST",
        "/api/uploads"
    );

    xhr.send(
        formData
    );
}


uploadButton.addEventListener(
    "click",
    uploadSelectedFile
);


async function deleteFile(name) {
    const confirmed =
        window.confirm(
            `Delete "${name}" from ChordSense?`
        );

    if (!confirmed) {
        return;
    }

    const response =
        await fetch(
            `/api/uploads/${encodeURIComponent(name)}`,
            {
                method: "DELETE"
            }
        );

    const payload =
        await response.json();

    if (
        !response.ok ||
        !payload.success
    ) {
        setStatus(
            payload.error ??
            "Could not delete song.",
            true
        );

        return;
    }

    setStatus(
        `${name} was deleted.`
    );

    await refreshLibrary();
}


function createLibraryRow(file) {
    const row =
        document.createElement("div");

    row.className =
        "library-row";


    const details =
        document.createElement("div");


    const name =
        document.createElement("div");

    name.className =
        "library-name";

    name.textContent =
        file.name;


    const meta =
        document.createElement("div");

    meta.className =
        "library-meta";

    meta.textContent =
        formatBytes(file.size);


    details.append(
        name,
        meta
    );


    const actions =
        document.createElement("div");

    actions.className =
        "library-actions";


    const openButton =
        document.createElement("a");

    openButton.className =
        "button secondary";

    openButton.href =
        file.download_url;

    openButton.target =
        "_blank";

    openButton.rel =
        "noopener";

    openButton.textContent =
        "Open";


    const deleteButton =
        document.createElement("button");

    deleteButton.className =
        "button secondary";

    deleteButton.type =
        "button";

    deleteButton.textContent =
        "Delete";

    deleteButton.addEventListener(
        "click",
        () => deleteFile(file.name)
    );


    actions.append(
        openButton,
        deleteButton
    );

    row.append(
        details,
        actions
    );

    return row;
}


async function refreshLibrary() {
    refreshButton.disabled =
        true;

    try {
        const response =
            await fetch(
                "/api/uploads"
            );

        const payload =
            await response.json();

        if (
            !response.ok ||
            !payload.success
        ) {
            throw new Error(
                payload.error ??
                "Could not load songs."
            );
        }

        connectionStatus.textContent =
            "Connected to ChordSense";

        connectionStatus.classList.add(
            "connected"
        );

        connectionStatus.classList.remove(
            "error"
        );

        const files =
            payload.files ?? [];

        libraryList.replaceChildren();

        libraryEmpty.classList.toggle(
            "hidden",
            files.length !== 0
        );

        for (const file of files) {
            libraryList.append(
                createLibraryRow(file)
            );
        }

    } catch (error) {

        connectionStatus.textContent =
            "ChordSense unavailable";

        connectionStatus.classList.remove(
            "connected"
        );

        connectionStatus.classList.add(
            "error"
        );
        setStatus(
            String(error),
            true
        );

    } finally {
        refreshButton.disabled =
            false;
    }
}


refreshButton.addEventListener(
    "click",
    refreshLibrary
);


refreshLibrary();