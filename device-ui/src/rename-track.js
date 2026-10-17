// A native text input supports the Pi keyboard; the optional keypad also works offline.
export function createTrackRenamer(invoke) {
    const dialog = document.querySelector("#rename-track-dialog");
    const form = document.querySelector("#rename-track-form");
    const input = document.querySelector("#rename-track-input");
    const error = document.querySelector("#rename-track-error");
    const keyboard = document.querySelector("#rename-keyboard");
    const toggle = document.querySelector("#rename-keyboard-toggle");
    const cancel = document.querySelector("#rename-track-cancel");
    let pending = null;
    let busy = false;
    let shifted = false;
    let result = null;

    function setBusy(value) {
        busy = value;
        for (const control of form.querySelectorAll("button, input")) control.disabled = value;
        form.setAttribute("aria-busy", String(value));
    }

    function editSelection(key) {
        let start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? start;
        if (key === "⌫" && start === end && start > 0) {
            start -= Array.from(input.value.slice(0, start)).at(-1).length;
        }
        const text = key === "⌫" ? "" : key === "Space" ? " " : key;
        const next = input.value.slice(0, start) + text + input.value.slice(end);
        if (next.length <= input.maxLength) {
            input.value = next;
            input.setSelectionRange(start + text.length, start + text.length);
            input.dispatchEvent(new Event("input", { bubbles: true }));
        }
    }

    function buildKeyboard() {
        keyboard.replaceChildren();
        for (const keys of ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm", ["Shift", "Space", "-", "'", ".", "⌫"]]) {
            const row = document.createElement("div");
            row.className = "rename-keyboard-row";
            for (const key of keys) {
                const button = document.createElement("button");
                button.type = "button";
                const label = key.length === 1 && shifted ? key.toUpperCase() : key;
                button.textContent = label;
                if (key === "⌫") button.setAttribute("aria-label", "Backspace");
                if (key === "Shift") button.setAttribute("aria-pressed", String(shifted));
                // Keep the text selection when tapping a key, without activating the OS keyboard.
                button.addEventListener("pointerdown", event => event.preventDefault());
                button.addEventListener("click", () => {
                    if (busy) return;
                    if (key === "Shift") { shifted = !shifted; buildKeyboard(); }
                    else editSelection(label);
                });
                row.append(button);
            }
            keyboard.append(row);
        }
    }

    toggle.addEventListener("click", () => {
        const visible = keyboard.classList.contains("hidden");
        keyboard.classList.toggle("hidden", !visible);
        toggle.setAttribute("aria-expanded", String(visible));
        // Suppress a second keyboard while using the built-in one.
        input.inputMode = visible ? "none" : "text";
        input.blur();
        if (!visible) input.focus();
    });
    cancel.addEventListener("click", () => { if (!busy) dialog.close(); });
    dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
    dialog.addEventListener("close", () => {
        pending?.resolve(result);
        pending = null;
    });
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (busy || !pending) return;
        const title = input.value.trim();
        if (!title) { error.textContent = "Enter a title."; return; }
        setBusy(true);
        error.textContent = "Saving…";
        try {
            result = await invoke("rename_library_track", {
                kind: pending.kind,
                id: pending.song.id ?? null,
                filename: pending.song.filename ?? null,
                title,
            });
            dialog.close();
        } catch (failure) {
            error.textContent = `Could not save: ${failure}`;
        } finally { setBusy(false); }
    });
    buildKeyboard();

    return ({ kind, song }) => new Promise(resolve => {
        if (pending) { resolve(null); return; }
        pending = { kind, song, resolve };
        result = null;
        shifted = false;
        buildKeyboard();
        error.textContent = "";
        input.value = song.name;
        input.inputMode = "text";
        keyboard.classList.add("hidden");
        toggle.setAttribute("aria-expanded", "false");
        dialog.showModal();
        input.focus();
        input.select();
    });
}
