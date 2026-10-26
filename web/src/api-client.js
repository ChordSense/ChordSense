import { discoveryPath } from "./config.js";
import { normalizeApiUrl, localRequestOptions } from "./endpoint.js";
export { normalizeApiUrl } from "./endpoint.js";

const storageKey = "chordsense.companion.pairing-session";
const identifier = /^[A-Za-z0-9_-]{22,64}$/;
const proofKeyPattern = /^[A-Za-z0-9_-]{43}$/;
const hexProof = /^[a-f0-9]{64}$/;
const emptyBootstrap = () => ({ discovery: true, api_url: null, context_id: null, pairing: null });
export const MAX_AUDIO_BYTES = 95 * 1024 * 1024;
const expiredMessage = "Your connection has expired. Get a new code on ChordSense and pair again.";
const discoveryMessage = "Could not find ChordSense right now. Check your internet connection and try again. No device connection was attempted.";

function productError(message, code, status) {
    const error = new Error(message);
    error.code = code;
    error.status = status;
    return error;
}

export function responseError(payload, status) {
    const code = payload?.error_code;
    const messages = {
        recording_not_ready: "Stop recording before downloading. If a take remains unavailable, check it on ChordSense.",
        recording_not_found: "A recording was removed. Refresh the recordings list and try again.",
        recordings_empty: "No recordings are available to download yet.",
        invalid_code: "That code doesn't match. Check the six digits on ChordSense.",
        pairing_expired: "That code has expired. Show a new code on ChordSense and try again.",
        pairing_locked: "Too many incorrect codes. Show a new code on ChordSense and try again.",
        pairing_unavailable: "Show a new pairing code on ChordSense, then try again.",
        rate_limited: "Too many pairing attempts. Wait a moment, then try again.",
        invalid_code_format: "Enter the six-digit code shown on ChordSense.",
        discovery_unavailable: discoveryMessage,
        session_invalid: expiredMessage,
        session_expired: expiredMessage,
    };
    const fallback = status === 429 ? "Too many attempts. Show a new code on ChordSense and try again."
        : status === 410 ? "That code has expired. Show a new code on ChordSense and try again."
        : status === 409 ? "Show a new pairing code on ChordSense, then try again."
        : `ChordSense could not complete this request (HTTP ${status}).`;
    return productError(messages[code] || fallback, code || "device_error", status);
}

async function localNetworkError(error, address) {
    const connectionHelp = "Check that ChordSense is on and both devices are on the same Wi-Fi network. The network must allow connections between devices.";
    if (error.name === "TimeoutError") return productError(`ChordSense at ${address} took too long to respond. ${connectionHelp}`, "offline");
    const permissions = globalThis.navigator?.permissions;
    if (typeof permissions?.query === "function") {
        const permissionName = localRequestOptions(address).targetAddressSpace === "loopback" ? "loopback-network" : "local-network";
        for (const name of [permissionName, "local-network-access"]) {
            try {
                const permission = await permissions.query({ name });
                if (permission?.state === "denied") return productError("Allow local network access for this website in your browser's site settings, then try again.", "permission_denied");
                if (permission) break;
            } catch {}
        }
    }
    const httpHelp = new URL(address).protocol === "http:" ? " Your browser may be blocking the device's HTTP connection." : "";
    return productError(`Could not reach ChordSense at ${address}. ${connectionHelp} Allow local network access if prompted.${httpHelp}`, "offline");
}

export function validateAudioFile(file) {
    if (!file || !/\.(wav|mp3|ogg|flac|m4a)$/i.test(file.name)) throw new Error("Choose a WAV, MP3, OGG, FLAC, or M4A song.");
    if (file.size >= MAX_AUDIO_BYTES) throw new Error("Choose a song smaller than 95 MiB.");
}

export function createApiClient({ baseUrl, token = "", fetchImpl = (...args) => globalThis.fetch(...args), onUnauthorized } = {}) {
    if (typeof baseUrl !== "string") throw productError("Enter the six-digit code shown on ChordSense to connect.", "not_paired");
    const address = normalizeApiUrl(baseUrl);
    const unauthenticatedPaths = new Set(["/api/pairing/proof", "/api/pairing/claim"]);
    return {
        endpoint(path) {
            if (!["/health", "/api/uploads", "/api/recordings", "/api/recordings/export", "/api/pairing/claim", "/api/pairing/proof", "/api/pairing/logout"].includes(path) && !/^\/api\/uploads\/[^/?#]+$/.test(path) && !/^\/api\/recordings\/[0-9]+\/audio$/.test(path)) {
                throw new Error("Invalid song library API path.");
            }
            return `${address}${path}`;
        },
        headers() { return token ? { Authorization: `Bearer ${token}` } : {}; },
        async request(path, { method = "GET", blob = false, body, authenticated = !unauthenticatedPaths.has(path) } = {}) {
            if (typeof authenticated !== "boolean" || authenticated !== !unauthenticatedPaths.has(path)) throw new Error("Invalid song library authentication scope.");
            if (authenticated && !token) throw productError("Enter the six-digit code shown on ChordSense to connect.", "not_paired");
            const headers = authenticated ? this.headers() : {};
            if (body !== undefined) headers["Content-Type"] = "application/json";
            let response;
            try {
                response = await fetchImpl(this.endpoint(path), {
                    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
                    credentials: "omit", cache: "no-store", redirect: "error", ...localRequestOptions(address),
                    signal: AbortSignal.timeout(blob ? 15 * 60 * 1000 : 30_000),
                });
            } catch (error) {
                if (error instanceof TypeError || error.name === "TimeoutError") throw await localNetworkError(error, address);
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
            if (!response.ok || payload?.success !== true) throw responseError(payload, response.status);
            return payload;
        },
    };
}

export function readDeviceBootstrap(document = globalThis.document) {
    const node = document?.getElementById?.("device-bootstrap");
    if (!node) return emptyBootstrap();
    const source = node.textContent;
    node.remove();
    try { return JSON.parse(source); }
    catch { throw productError("Could not read the device connection. Show a new pairing code and try again.", "invalid_bootstrap"); }
}

function validateBootstrap(value) {
    if (!value || value.discovery !== true) throw new Error("Invalid discovery bootstrap.");
    if (value.api_url === null && value.context_id === null && value.pairing === null) return emptyBootstrap();
    if (typeof value.api_url !== "string" || typeof value.context_id !== "string" || !identifier.test(value.context_id)) throw new Error("Invalid device context.");
    const baseUrl = normalizeApiUrl(value.api_url);
    let pairing = null;
    if (value.pairing !== null) {
        const candidate = value.pairing;
        if (!candidate || typeof candidate.code !== "string" || !/^[0-9]{6}$/.test(candidate.code) ||
            typeof candidate.pairing_id !== "string" || !identifier.test(candidate.pairing_id) || typeof candidate.proof_key !== "string" || !proofKeyPattern.test(candidate.proof_key) ||
            !Number.isFinite(candidate.expires_in) || candidate.expires_in <= 0 || candidate.expires_in > 120) throw new Error("Invalid pending pairing.");
        pairing = { code: candidate.code, pairing_id: candidate.pairing_id, proof_key: candidate.proof_key, expires_in: candidate.expires_in };
    }
    return { discovery: true, api_url: baseUrl, context_id: value.context_id, pairing };
}

function validToken(token) { return typeof token === "string" && token.length > 0 && token.length <= 4096 && !/\s/.test(token); }
function bytesFromHex(value) { return Uint8Array.from(value.match(/../g), part => parseInt(part, 16)); }
function tabStorage() { try { return globalThis.sessionStorage; } catch { return undefined; } }

export function createCompanionSession({ bootstrap, storage = tabStorage(), fetchImpl = (...args) => globalThis.fetch(...args),
    navigate = path => globalThis.location.assign(path), cryptoImpl = globalThis.crypto, now = () => Date.now() / 1000,
    notify = eventName => { if (globalThis.document && globalThis.CustomEvent) document.dispatchEvent(new CustomEvent(eventName)); } } = {}) {
    let configuration;
    let bootstrapError = null;
    try { configuration = validateBootstrap(bootstrap === undefined ? readDeviceBootstrap() : bootstrap); }
    catch {
        configuration = emptyBootstrap();
        bootstrapError = productError("Could not read the device connection. Show a new pairing code and try again.", "invalid_bootstrap");
    }
    const baseUrl = configuration.api_url;
    const contextId = configuration.context_id;
    let pending = configuration.pairing ? { ...configuration.pairing, expiresAt: now() + configuration.pairing.expires_in } : null;
    configuration.pairing = null;
    const emptyConnection = () => ({ baseUrl, contextId, token: "", expiresAt: 0 });
    let connection = emptyConnection();
    try {
        const saved = JSON.parse(storage?.getItem(storageKey) || "null");
        if (baseUrl && saved?.baseUrl === baseUrl && saved.contextId === contextId && validToken(saved.token) &&
            Number.isFinite(saved.expiresAt) && saved.expiresAt > now() && saved.expiresAt <= now() + 8 * 60 * 60) {
            connection = { baseUrl, contextId, token: saved.token, expiresAt: saved.expiresAt };
        } else storage?.removeItem(storageKey);
    } catch {}
    let pendingTransfers = 0;
    let client;
    function clearSession(eventName = "device-session-expired") {
        connection = emptyConnection();
        client = makeClient();
        try { storage?.removeItem(storageKey); } catch {}
        notify(eventName);
    }
    function makeClient() {
        if (!baseUrl) return null;
        let instance;
        instance = createApiClient({ ...connection, fetchImpl, onUnauthorized: () => { if (client === instance) clearSession(); } });
        return instance;
    }
    client = makeClient();
    function requireSession() {
        if (!connection.token || !client) throw productError("Enter the six-digit code shown on ChordSense to connect.", "not_paired");
        if (connection.expiresAt <= now()) {
            clearSession();
            throw productError(expiredMessage, "session_expired", 401);
        }
    }
    function beginTransfer() {
        pendingTransfers += 1;
        let finished = false;
        return () => { if (!finished) { pendingTransfers -= 1; finished = true; } };
    }
    function requireIdle(action) {
        if (pendingTransfers) throw new Error(`Wait for the current transfer to finish before ${action}.`);
    }
    return {
        getConnection: () => ({ ...connection }),
        getBootstrapError: () => bootstrapError,
        hasPendingPairing: () => Boolean(pending),
        beginTransfer,
        async pairDevice(code) {
            requireIdle("pairing again");
            if (typeof code !== "string" || !/^[0-9]{6}$/.test(code.trim())) throw productError("Enter the six-digit code shown on ChordSense.", "invalid_code_format");
            const finish = beginTransfer();
            try {
                let response;
                try {
                    response = await fetchImpl(discoveryPath, {
                        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: code.trim() }),
                        credentials: "same-origin", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15_000),
                    });
                } catch { throw productError(discoveryMessage, "discovery_unavailable"); }
                let payload;
                try { payload = await response.json(); }
                catch { throw productError(discoveryMessage, "discovery_unavailable", response.status); }
                if (!response.ok || payload?.success !== true) {
                    if (response.status >= 500) throw productError(discoveryMessage, "discovery_unavailable", response.status);
                    throw responseError(payload, response.status);
                }
                if (payload?.navigate_to !== "/") throw productError(discoveryMessage, "invalid_discovery_response");
                navigate("/");
                return { redirecting: true };
            } finally { finish(); }
        },
        async completeDiscoveredPairing() {
            requireIdle("pairing again");
            if (!pending || !baseUrl) throw productError("Show a new pairing code on ChordSense, then try again.", "pairing_unavailable");
            if (pending.expiresAt <= now()) {
                pending = null;
                throw responseError({ error_code: "pairing_expired" }, 410);
            }
            const finish = beginTransfer();
            try {
                if (!cryptoImpl?.subtle || typeof cryptoImpl.getRandomValues !== "function") throw productError("This browser could not securely verify ChordSense. Update your browser and try again.", "verification_unavailable");
                const pairing = pending;
                const challenge = Array.from(cryptoImpl.getRandomValues(new Uint8Array(32)), byte => byte.toString(16).padStart(2, "0")).join("");
                const unauthenticated = createApiClient({ baseUrl, fetchImpl });
                const proof = await unauthenticated.request("/api/pairing/proof", { method: "POST", body: { pairing_id: pairing.pairing_id, challenge } });
                const key = await cryptoImpl.subtle.importKey("raw", new TextEncoder().encode(pairing.proof_key), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
                const message = new TextEncoder().encode(`chordsense-pairing-v1\n${pairing.pairing_id}\n${challenge}`);
                if (proof.pairing_id !== pairing.pairing_id || !hexProof.test(proof.proof || "") ||
                    !await cryptoImpl.subtle.verify("HMAC", key, bytesFromHex(proof.proof), message)) {
                    pending = null;
                    throw productError("Could not verify this ChordSense device. Show a new pairing code on its screen and try again.", "device_proof_invalid");
                }
                if (pairing.expiresAt <= now()) {
                    pending = null;
                    throw responseError({ error_code: "pairing_expired" }, 410);
                }
                // A failed proof request may be retried in memory; a claim is sent once.
                pending = null;
                const payload = await unauthenticated.request("/api/pairing/claim", { method: "POST", body: { code: pairing.code, pairing_id: pairing.pairing_id, proof_key: pairing.proof_key } });
                if (!validToken(payload.token) || payload.token_type !== "Bearer" || !Number.isFinite(payload.expires_in) || payload.expires_in <= 0 || payload.expires_in > 8 * 60 * 60) {
                    throw new Error("ChordSense could not finish pairing. Show a new code and try again.");
                }
                connection = { baseUrl, contextId, token: payload.token, expiresAt: now() + payload.expires_in };
                client = makeClient();
                try { storage?.setItem(storageKey, JSON.stringify(connection)); } catch {}
                return { success: true };
            } finally { finish(); }
        },
        async disconnectDevice() {
            requireIdle("disconnecting");
            requireSession();
            const finish = beginTransfer();
            try {
                await client.request("/api/pairing/logout", { method: "POST" });
                clearSession("device-disconnected");
            } finally { finish(); }
        },
        apiEndpoint(path) { requireSession(); return client.endpoint(path); },
        apiHeaders() { requireSession(); return client.headers(); },
        handleUploadError(payload, status) {
            if (status === 401) { clearSession(); return productError(expiredMessage, "session_expired", 401); }
            return responseError(payload, status);
        },
        async apiRequest(path, options) {
            requireSession();
            const finish = beginTransfer();
            try { return await client.request(path, options); }
            finally { finish(); }
        },
    };
}

export const companionSession = createCompanionSession();
export const getConnection = () => companionSession.getConnection();
export const pairDevice = code => companionSession.pairDevice(code);
export const completeDiscoveredPairing = () => companionSession.completeDiscoveredPairing();
export const disconnectDevice = () => companionSession.disconnectDevice();
export const apiEndpoint = path => companionSession.apiEndpoint(path);
export const apiHeaders = () => companionSession.apiHeaders();
export const beginTransfer = () => companionSession.beginTransfer();
export const handleUploadError = (payload, status) => companionSession.handleUploadError(payload, status);
export const apiRequest = (path, options) => companionSession.apiRequest(path, options);
