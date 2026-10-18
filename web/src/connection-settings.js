import { companionSession } from "./api-client.js";

export function initConnectionSettings({ session = companionSession } = {}) {
    const { apiRequest, completeDiscoveredPairing, disconnectDevice, getConnection, pairDevice } = session;
    const host = document.querySelector("#device-connection");
    if (!host) return;
    host.innerHTML = `
        <details class="device-connection-details">
            <summary>Pair with ChordSense <span id="device-connection-state">Not paired</span></summary>
            <form id="device-connection-form">
                <label>Pairing code <input id="device-pairing-code" type="text" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" placeholder="123456" required autocomplete="one-time-code" aria-describedby="pairing-help"></label>
                <button type="submit">Pair device</button>
                <p id="pairing-help" class="device-connection-help">Connect to the same Wi-Fi as ChordSense. Tap Pair uploads on its screen, enter the six-digit code, and allow local network access if prompted.</p>
            </form>
            <button id="device-retry" type="button" hidden>Try connection again</button>
            <button id="device-disconnect" type="button" hidden>Disconnect</button>
            <p id="device-connection-message" role="status" aria-live="polite"></p>
        </details>`;
    const form = host.querySelector("form");
    const code = host.querySelector("#device-pairing-code");
    const state = host.querySelector("#device-connection-state");
    const message = host.querySelector("#device-connection-message");
    const details = host.querySelector("details");
    const pairButton = form.querySelector("button");
    const retryButton = host.querySelector("#device-retry");
    const disconnectButton = host.querySelector("#device-disconnect");
    details.open = !getConnection().token;
    let checking = false;
    function updateActions() {
        const paired = Boolean(getConnection().token);
        form.hidden = paired;
        disconnectButton.hidden = !paired;
        pairButton.disabled = checking;
        retryButton.hidden = !session.hasPendingPairing();
        retryButton.disabled = checking;
        disconnectButton.disabled = checking;
    }
    async function checkConnection(pair = false, complete = false) {
        if (checking) return;
        checking = true;
        updateActions();
        state.textContent = pair || complete ? "Pairing…" : "Connecting…";
        try {
            if (pair) {
                const enteredCode = code.value;
                code.value = "";
                const result = await pairDevice(enteredCode);
                if (result?.redirecting) {
                    state.textContent = "Connecting…";
                    message.textContent = "Found ChordSense. Connecting to the device…";
                    return;
                }
            }
            if (complete) await completeDiscoveredPairing();
            await apiRequest("/health");
            state.textContent = "Paired";
            message.textContent = "Connected. Upload a song to make it available on ChordSense.";
            details.open = false;
            host.dispatchEvent(new CustomEvent("device-connected", { bubbles: true }));
        } catch (error) {
            state.textContent = getConnection().token ? "Unavailable" : "Not paired";
            message.textContent = error.message || String(error);
            details.open = true;
            host.dispatchEvent(new CustomEvent("device-connection-failed", { bubbles: true }));
        } finally { checking = false; updateActions(); }
    }
    form.addEventListener("submit", event => { event.preventDefault(); void checkConnection(true); });
    retryButton.addEventListener("click", () => { void checkConnection(false, true); });
    disconnectButton.addEventListener("click", async () => {
        if (checking) return;
        checking = true;
        state.textContent = "Disconnecting…";
        updateActions();
        try { await disconnectDevice(); }
        catch (error) {
            state.textContent = getConnection().token ? "Unavailable" : "Not paired";
            message.textContent = error.message || String(error);
            details.open = true;
        } finally { checking = false; updateActions(); }
    });
    document.addEventListener("device-session-expired", () => {
        state.textContent = "Not paired";
        message.textContent = "Your connection has expired. Show a new code on ChordSense and pair again.";
        details.open = true;
        code.value = "";
        updateActions();
    });
    document.addEventListener("device-disconnected", () => {
        state.textContent = "Not paired";
        message.textContent = "Disconnected. Show a new code on ChordSense when you want to pair again.";
        code.value = "";
        details.open = true;
        updateActions();
    });
    updateActions();
    const bootstrapError = session.getBootstrapError();
    if (bootstrapError) {
        message.textContent = bootstrapError.message;
        details.open = true;
    } else if (session.hasPendingPairing()) void checkConnection(false, true);
    else if (getConnection().token) void checkConnection();
}
