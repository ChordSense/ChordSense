import { defaultApiUrl } from "./config.js";
import { normalizeApiUrl, localRequestOptions } from "./endpoint.js";
export { normalizeApiUrl } from "./endpoint.js";

const storageKey = "chordsense.companion.pairing-session";
const baseUrl = normalizeApiUrl(defaultApiUrl);
export const MAX_AUDIO_BYTES = 95 * 1024 * 1024;
const expiredMessage = "Your connection has expired. Get a new code on ChordSense and pair again.";

function productError(message, code, status) {
    const error = new Error(message);
    error.code = code;
    error.status = status;
    return error;
}

export function responseError(payload, status) {
    const code = payload?.error_code;
    const messages = {
        invalid_code: "That code doesn't match. Check the six digits on ChordSense.",
        pairing_expired: "That code has expired. Show a new code on ChordSense and try again.",
        pairing_locked: "Too many incorrect codes. Show a new code on ChordSense and try again.",
        pairing_unavailable: "Show a new pairing code on ChordSense, then try again.",
        session_invalid: expiredMessage,
        session_expired: expiredMessage,
    };
    const fallback = status === 429 ? "Too many attempts. Show a new code on ChordSense and try again."
        : status === 410 ? "That code has expired. Show a new code on ChordSense and try again."
        : status === 409 ? "Show a new pairing code on ChordSense, then try again."
        : payload?.error || `ChordSense could not complete this request (HTTP ${status}).`;
    return productError(messages[code] || fallback, code || "device_error", status);
}

async function localNetworkError(error) {
    if (error.name === "TimeoutError") return productError("ChordSense took too long to respond. Check that it is on and connected to the same Wi-Fi.", "offline");
    const permissions = globalThis.navigator?.permissions;
    if (typeof permissions?.query === "function") {
        for (const name of ["local-network", "local-network-access"]) {
            try {
                const permission = await permissions.query({ name });
                if (permission?.state === "denied") return productError("Allow local network access for this website in your browser's site settings, then try again.", "permission_denied");
                if (permission) break;
            } catch {}
        }
    }
    return productError("Could not reach ChordSense. Check that it is on and connected to the same Wi-Fi, and allow local network access if prompted. Your browser may be blocking the device's HTTP connection.", "offline");
}

export function validateAudioFile(file) {
    if (!file || !/\.(wav|mp3|ogg|flac|m4a)$/i.test(file.name)) throw new Error("Choose a WAV, MP3, OGG, FLAC, or M4A song.");
    if (file.size >= MAX_AUDIO_BYTES) throw new Error("Choose a song smaller than 95 MiB.");
}

export function createApiClient({ baseUrl: deviceUrl = defaultApiUrl, token = "", fetchImpl = globalThis.fetch, onUnauthorized } = {}) {
    const address = normalizeApiUrl(deviceUrl);
    return {
        endpoint(path) {
            if (!["/health", "/api/uploads", "/api/pairing/claim", "/api/pairing/logout"].includes(path) && !/^\/api\/uploads\/[^/?#]+$/.test(path)) {
                throw new Error("Invalid song library API path.");
            }
            return `${address}${path}`;
        },
        headers() { return token ? { Authorization: `Bearer ${token}` } : {}; },
        async request(path, { method = "GET", blob = false, body, authenticated = path !== "/api/pairing/claim" } = {}) {
            if (authenticated && !token) throw productError("Enter the six-digit code shown on ChordSense to connect.", "not_paired");
            const headers = authenticated ? this.headers() : {};
            if (body !== undefined) headers["Content-Type"] = "application/json";
            let response;
            try {
                response = await fetchImpl(this.endpoint(path), {
                    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
                    credentials: "omit", cache: "no-store", ...localRequestOptions(address),
                    signal: AbortSignal.timeout(blob ? 15 * 60 * 1000 : 30_000),
                });
            } catch (error) {
                if (error instanceof TypeError || error.name === "TimeoutError") throw await localNetworkError(error);
                throw error;
            }
            if (authenticated && response.status === 401) {
                onUnauthorized?.();
                throw productError(expiredMessage, "session_expired", 401);
            }
            if (blob && response.ok) return response.blob();
            let payload;
            try { payload = await response.json(); }
            catch { throw new Error(`ChordSense returned an unreadable response (HTTP ${response.status}).`); }
            if (!response.ok || payload.success === false) throw responseError(payload, response.status);
            return payload;
        },
    };
}

function initialConnection() {
    try {
        const saved = JSON.parse(globalThis.sessionStorage?.getItem(storageKey) || "null");
        if (saved?.baseUrl === baseUrl && typeof saved.token === "string" && saved.token &&
            Number.isFinite(saved.expiresAt) && saved.expiresAt > Date.now() / 1000) return saved;
        globalThis.sessionStorage?.removeItem(storageKey);
    } catch {}
    return { baseUrl, token: "", expiresAt: 0 };
}
let connection = initialConnection();
let pendingTransfers = 0;
function notifySessionCleared(eventName) {
    if (globalThis.document && globalThis.CustomEvent) document.dispatchEvent(new CustomEvent(eventName));
}
function clearSession(eventName = "device-session-expired") {
    connection = { baseUrl, token: "", expiresAt: 0 };
    client = makeClient();
    try { globalThis.sessionStorage?.removeItem(storageKey); } catch {}
    notifySessionCleared(eventName);
}
function makeClient() {
    let instance;
    instance = createApiClient({ ...connection, onUnauthorized: () => { if (client === instance) clearSession(); } });
    return instance;
}
let client = makeClient();
function requireSession() {
    if (!connection.token) throw productError("Enter the six-digit code shown on ChordSense to connect.", "not_paired");
    if (connection.expiresAt <= Date.now() / 1000) {
        clearSession();
        throw productError(expiredMessage, "session_expired", 401);
    }
}
export function getConnection() { return { ...connection }; }
export function beginTransfer() {
    pendingTransfers += 1;
    let finished = false;
    return () => { if (!finished) { pendingTransfers -= 1; finished = true; } };
}
export async function pairDevice(code) {
    if (pendingTransfers) throw new Error("Wait for the current transfer to finish before pairing again.");
    if (typeof code !== "string" || !/^[0-9]{6}$/.test(code.trim())) throw productError("Enter the six-digit code shown on ChordSense.", "invalid_code_format");
    const finish = beginTransfer();
    try {
        const payload = await createApiClient().request("/api/pairing/claim", { method: "POST", body: { code: code.trim() }, authenticated: false });
        if (typeof payload.token !== "string" || !payload.token || !Number.isFinite(payload.expires_in) || payload.expires_in <= 0 || payload.expires_in > 8 * 60 * 60) {
            throw new Error("ChordSense could not finish pairing. Show a new code and try again.");
        }
        // Use a relative lifetime so the Pi and browser need not share a clock.
        connection = { baseUrl, token: payload.token, expiresAt: Date.now() / 1000 + payload.expires_in };
        client = makeClient();
        try { globalThis.sessionStorage?.setItem(storageKey, JSON.stringify(connection)); } catch {}
        return payload;
    } finally { finish(); }
}
export async function disconnectDevice() {
    if (pendingTransfers) throw new Error("Wait for the current transfer to finish before disconnecting.");
    requireSession();
    const finish = beginTransfer();
    try {
        // Keep the session on network failure so a retry can revoke it on the device.
        await client.request("/api/pairing/logout", { method: "POST" });
        clearSession("device-disconnected");
    } finally { finish(); }
}
export const apiEndpoint = path => { requireSession(); return client.endpoint(path); };
export const apiHeaders = () => { requireSession(); return client.headers(); };
export function handleUploadError(payload, status) {
    if (status === 401) { clearSession(); return productError(expiredMessage, "session_expired", 401); }
    return responseError(payload, status);
}
export async function apiRequest(path, options) {
    requireSession();
    const finish = beginTransfer();
    try { return await client.request(path, options); }
    finally { finish(); }
}
