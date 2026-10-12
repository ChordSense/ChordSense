export function initUploadPairing() {
    const dialog = document.querySelector("#upload-pairing-dialog");
    const openButton = document.querySelector("#pair-uploads");
    if (!dialog || !openButton) return;

    const invoke = window.__TAURI__.core.invoke;
    const code = document.querySelector("#upload-pairing-code");
    const status = document.querySelector("#upload-pairing-status");
    const timer = document.querySelector("#upload-pairing-timer");
    const website = document.querySelector("#upload-pairing-website");
    const newButton = document.querySelector("#new-upload-pairing");
    const cancelButton = document.querySelector("#cancel-upload-pairing");
    const revokeButton = document.querySelector("#revoke-upload-sessions");
    let phase = "idle";
    let deadline = 0;
    let interval = null;
    let epoch = 0;
    let pollPending = false;
    let nextPollAt = 0;
    let actionQueue = Promise.resolve();

    // A close queued during start must cancel the code after start finishes.
    // Serializing requests also prevents an old status response replacing a
    // newly generated code or a revocation result.
    function request(command) {
        const operation = actionQueue.then(() => invoke(command));
        actionQueue = operation.catch(() => {});
        return operation;
    }

    function stopWatching() {
        if (interval !== null) clearInterval(interval);
        interval = null;
    }

    function setBusy(busy) {
        newButton.disabled = busy;
        cancelButton.disabled = busy || phase !== "pending";
        revokeButton.disabled = busy;
    }

    function render(result) {
        stopWatching();
        phase = result.state;
        website.textContent = result.website_url || "";
        website.hidden = !result.website_url;
        code.textContent = "—";
        timer.textContent = "";
        if (phase === "pending" && /^\d{6}$/.test(result.code)) {
            code.textContent = `${result.code.slice(0, 3)} ${result.code.slice(3)}`;
            deadline = performance.now() + Math.max(0, Number(result.expires_in) || 0) * 1000;
            status.textContent = "Waiting for your browser to pair…";
            nextPollAt = performance.now() + 2000;
            updateCountdown();
            if (!document.hidden && phase === "pending") interval = setInterval(tick, 1000);
        } else {
            const messages = {
                paired: "Browser paired. You can upload songs from the website.",
                expired: "This code expired. Generate a new code to pair.",
                locked: "Pairing closed after five incorrect attempts. Generate a new code to try again.",
                cancelled: "Pairing cancelled. Paired browsers keep their existing sessions.",
                idle: "Generate a new code to pair a browser.",
            };
            status.textContent = messages[phase] || "Pairing is closed. Generate a new code to pair.";
        }
        setBusy(false);
    }

    function updateCountdown() {
        const remaining = Math.max(0, Math.ceil((deadline - performance.now()) / 1000));
        timer.textContent = `Code expires in ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
        if (remaining === 0) render({ state: "expired", website_url: website.textContent });
    }

    async function refreshStatus() {
        if (pollPending || !dialog.open || phase !== "pending") return;
        const currentEpoch = epoch;
        pollPending = true;
        try {
            const result = await request("get_upload_pairing_status");
            if (currentEpoch === epoch && dialog.open) render(result);
        } catch (error) {
            if (currentEpoch === epoch && dialog.open) {
                stopWatching();
                code.textContent = "—";
                timer.textContent = "";
                status.textContent = String(error);
            }
        } finally {
            pollPending = false;
        }
    }

    function tick() {
        if (!dialog.open || document.hidden || phase !== "pending") {
            stopWatching();
            return;
        }
        updateCountdown();
        if (phase === "pending" && performance.now() >= nextPollAt) {
            nextPollAt = performance.now() + 2000;
            void refreshStatus();
        }
    }

    async function perform(command) {
        const currentEpoch = ++epoch;
        stopWatching();
        setBusy(true);
        code.textContent = "—";
        timer.textContent = "";
        status.textContent = "Updating pairing…";
        try {
            const result = await request(command);
            if (currentEpoch !== epoch || !dialog.open) return;
            render(result);
            if (command === "revoke_upload_sessions") {
                status.textContent = "All browser sessions disconnected. Generate a new code to pair again.";
            }
        } catch (error) {
            if (currentEpoch === epoch && dialog.open) {
                status.textContent = String(error);
                setBusy(false);
            }
        }
    }

    function close() {
        ++epoch;
        stopWatching();
        dialog.close();
        openButton.focus();
        // Cancelling an unused code does not revoke an already paired browser.
        void request("cancel_upload_pairing").catch(() => {});
    }

    openButton.addEventListener("click", () => {
        if (dialog.open) return;
        dialog.showModal();
        void perform("start_upload_pairing");
    });
    newButton.addEventListener("click", () => void perform("start_upload_pairing"));
    cancelButton.addEventListener("click", () => void perform("cancel_upload_pairing"));
    revokeButton.addEventListener("click", () => void perform("revoke_upload_sessions"));
    document.querySelector("#close-upload-pairing").addEventListener("click", close);
    dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) stopWatching();
        else if (dialog.open && phase === "pending") void refreshStatus();
    });
}
