import test from "node:test";
import assert from "node:assert/strict";
import { createHmac, webcrypto } from "node:crypto";
import { createApiClient, createCompanionSession, MAX_AUDIO_BYTES, readDeviceBootstrap, responseError, validateAudioFile } from "./api-client.js";
import { initConnectionSettings } from "./connection-settings.js";

const device = "http://10.136.137.226:5051";
const contextId = "C".repeat(22);
const pairingId = "P".repeat(22);
const proofKey = "K".repeat(43);
const code = "012345";
const storageKey = "chordsense.companion.pairing-session";
const emptyBootstrap = { discovery: true, api_url: null, context_id: null, pairing: null };
const bootstrap = (changes = {}) => ({ discovery: true, api_url: device, context_id: contextId,
    pairing: { code, pairing_id: pairingId, proof_key: proofKey, expires_in: 120 }, ...changes });
const claim = (token = "test-session", changes = {}) => ({ success: true, token, token_type: "Bearer", expires_in: 28800, expires_at: 100, permissions: ["uploads"], ...changes });
function fakeStorage() {
    const entries = new Map();
    return { entries, getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key) };
}
function fixture(options = {}) {
    const storage = options.storage || fakeStorage();
    const calls = [];
    const events = [];
    const navigations = [];
    let time = 2_000_000_000;
    const defaultFetch = async (url, request) => {
        if (url === "/api/discovery/resolve") return Response.json({ success: true, navigate_to: "/" });
        if (url.endsWith("/api/pairing/proof")) {
            const { pairing_id, challenge } = JSON.parse(request.body);
            const proof = createHmac("sha256", proofKey).update(`chordsense-pairing-v1\n${pairing_id}\n${challenge}`).digest("hex");
            return Response.json({ success: true, pairing_id, proof });
        }
        if (url.endsWith("/api/pairing/claim")) return Response.json(claim());
        return Response.json({ success: true, files: [] });
    };
    const session = createCompanionSession({ bootstrap: bootstrap(), storage, cryptoImpl: webcrypto, now: () => time,
        navigate: path => navigations.push(path), notify: event => events.push(event), ...options,
        fetchImpl: async (url, request) => {
            calls.push({ url, options: request });
            return (options.fetchImpl || defaultFetch)(url, request, defaultFetch);
        } });
    return { session, calls, events, navigations, storage, setTime: value => { time = value; } };
}

test("the initial page has no Pi client, no automatic requests, and no local fallback", async () => {
    const f = fixture({ bootstrap: emptyBootstrap });
    assert.equal(f.session.getConnection().baseUrl, null);
    assert.equal(f.session.hasPendingPairing(), false);
    await assert.rejects(f.session.apiRequest("/health"), /six-digit code/);
    assert.throws(() => f.session.apiEndpoint("/api/uploads"), /six-digit code/);
    assert.equal(f.calls.length, 0);
});

test("a code goes only in the same-origin discovery POST body and navigation stays at the root", async () => {
    const f = fixture({ bootstrap: emptyBootstrap });
    assert.deepEqual(await f.session.pairDevice(` ${code} `), { redirecting: true });
    assert.deepEqual(f.navigations, ["/"]);
    assert.equal(f.calls.length, 1);
    const request = f.calls[0];
    assert.equal(request.url, "/api/discovery/resolve");
    assert.deepEqual(JSON.parse(request.options.body), { code });
    assert.equal(request.options.credentials, "same-origin");
    assert.equal(request.options.redirect, "error");
    assert.equal(request.options.cache, "no-store");
    assert.equal(request.options.headers.Authorization, undefined);
    assert.equal(request.options.targetAddressSpace, undefined);
    assert.equal(f.storage.entries.size, 0);
    assert.ok(!request.url.includes(code));
});

test("malformed codes never contact the directory or a device", async () => {
    const f = fixture();
    for (const value of [123456, "12345", "abcdef", "１２３４５６", "12 345"]) await assert.rejects(f.session.pairDevice(value), /six-digit/);
    assert.equal(f.calls.length, 0);
});

test("directory failures and untrusted navigation never fall back to any LAN address", async () => {
    const cases = [
        [async () => { throw new TypeError("Failed to fetch"); }, /Could not find ChordSense/],
        [async () => new Response("offline", { status: 503 }), /Could not find ChordSense/],
        [async () => Response.json({ success: false }, { status: 503 }), /Could not find ChordSense/],
        [async () => Response.json(null), /ChordSense could not complete/],
        [async () => Response.json({ success: false, error_code: "invalid_code" }, { status: 401 }), /doesn't match/],
        [async () => Response.json({ success: false, error_code: "pairing_expired" }, { status: 410 }), /code has expired/],
        [async () => Response.json({ success: false, error_code: "pairing_locked" }, { status: 429 }), /Too many incorrect/],
        [async () => Response.json({ success: false, error_code: "rate_limited" }, { status: 429 }), /Wait a moment/],
        ...["https://other.example/", "//other.example/", "/?code=012345", "/upload", undefined].map(navigate_to => [async () => Response.json({ success: true, navigate_to }), /Could not find ChordSense/]),
    ];
    for (const [fetchImpl, expected] of cases) {
        const f = fixture({ fetchImpl });
        await assert.rejects(f.session.pairDevice(code), expected);
        assert.deepEqual(f.calls.map(call => call.url), ["/api/discovery/resolve"]);
        assert.equal(f.navigations.length, 0);
        assert.equal(f.storage.entries.size, 0);
    }
});

test("proof verifies the registered device before the leading-zero code or key is sent to it", async () => {
    const f = fixture();
    await f.session.completeDiscoveredPairing();
    await f.session.apiRequest("/health");
    assert.deepEqual(f.calls.map(call => call.url), [`${device}/api/pairing/proof`, `${device}/api/pairing/claim`, `${device}/health`]);
    const proof = f.calls[0];
    const proofBody = JSON.parse(proof.options.body);
    assert.equal(proofBody.pairing_id, pairingId);
    assert.match(proofBody.challenge, /^[a-f0-9]{64}$/);
    assert.ok(!proof.options.body.includes(code));
    assert.ok(!proof.options.body.includes(proofKey));
    assert.deepEqual(JSON.parse(f.calls[1].options.body), { code, pairing_id: pairingId, proof_key: proofKey });
    for (const call of f.calls) {
        assert.equal(call.options.credentials, "omit");
        assert.equal(call.options.redirect, "error");
        assert.equal(call.options.targetAddressSpace, "local");
        assert.ok(!call.url.includes(code));
        assert.ok(!call.url.includes(proofKey));
    }
    assert.equal(proof.options.headers.Authorization, undefined);
    assert.equal(f.calls[1].options.headers.Authorization, undefined);
    assert.equal(f.calls[2].options.headers.Authorization, "Bearer test-session");
    const saved = JSON.parse(f.storage.entries.get(storageKey));
    assert.deepEqual(saved, { baseUrl: device, contextId, token: "test-session", expiresAt: 2_000_028_800 });
    assert.ok(!JSON.stringify(saved).includes(code));
    assert.ok(!JSON.stringify(saved).includes(proofKey));
    assert.ok(!JSON.stringify(saved).includes(pairingId));
    assert.equal(f.session.hasPendingPairing(), false);
});

test("a mismatched, malformed, or foreign proof never sends the code or key", async () => {
    for (const change of [{ proof: "0".repeat(64) }, { proof: "not-a-proof" }, { pairing_id: "X".repeat(22) }]) {
        const f = fixture({ fetchImpl: async (_url, _request, defaults) => {
            const response = await defaults(_url, _request);
            return Response.json({ ...await response.json(), ...change });
        } });
        await assert.rejects(f.session.completeDiscoveredPairing(), error => error.code === "device_proof_invalid");
        assert.equal(f.calls.length, 1);
        assert.ok(!f.calls[0].options.body.includes(code));
        assert.ok(!f.calls[0].options.body.includes(proofKey));
        assert.equal(f.storage.entries.size, 0);
        assert.equal(f.session.hasPendingPairing(), false);
    }
});

test("proof fetch failures can retry in memory with a fresh challenge until the pending lease expires", async () => {
    let fail = true;
    const f = fixture({ fetchImpl: async (url, request, defaults) => {
        if (fail) { fail = false; throw new TypeError("Failed to fetch"); }
        return defaults(url, request);
    } });
    await assert.rejects(f.session.completeDiscoveredPairing(), /Could not reach ChordSense/);
    assert.equal(f.session.hasPendingPairing(), true);
    assert.equal(f.storage.entries.size, 0);
    await f.session.completeDiscoveredPairing();
    assert.notEqual(JSON.parse(f.calls[0].options.body).challenge, JSON.parse(f.calls[1].options.body).challenge);
    const expired = fixture();
    expired.setTime(2_000_000_121);
    await assert.rejects(expired.session.completeDiscoveredPairing(), /code has expired/);
    assert.equal(expired.calls.length, 0);
});

test("a claim is not replayed after a lost response", async () => {
    const f = fixture({ fetchImpl: async (url, request, defaults) => {
        if (url.endsWith("/claim")) throw new TypeError("Lost claim response");
        return defaults(url, request);
    } });
    await assert.rejects(f.session.completeDiscoveredPairing(), /Could not reach ChordSense/);
    await assert.rejects(f.session.completeDiscoveredPairing(), /new pairing code/);
    assert.equal(f.calls.length, 2);
});

test("missing verification support fails before any local request, without a browser-brand restriction", async () => {
    const f = fixture({ cryptoImpl: {} });
    await assert.rejects(f.session.completeDiscoveredPairing(), error => error.code === "verification_unavailable");
    assert.equal(f.calls.length, 0);
    assert.equal(f.storage.entries.size, 0);
});

test("pairing uses a bounded browser-relative session lifetime despite a skewed Pi clock", async () => {
    const f = fixture();
    await f.session.completeDiscoveredPairing();
    assert.equal(f.session.getConnection().expiresAt, 2_000_028_800);
    for (const changes of [{ expires_in: 0 }, { expires_in: -1 }, { expires_in: 28801 }, { expires_in: "28800" }, { token_type: "Other" }, { token: "bad\ntoken" }]) {
        const invalid = fixture({ fetchImpl: async (url, request, defaults) => url.endsWith("/claim") ? Response.json(claim("token", changes)) : defaults(url, request) });
        await assert.rejects(invalid.session.completeDiscoveredPairing(), /could not finish pairing/);
        assert.equal(invalid.storage.entries.size, 0);
    }
});

test("restored sessions require an exact endpoint and context match", async () => {
    const original = fixture();
    await original.session.completeDiscoveredPairing();
    const restored = fixture({ bootstrap: bootstrap({ pairing: null }), storage: original.storage });
    assert.equal(restored.session.apiHeaders().Authorization, "Bearer test-session");
    await restored.session.apiRequest("/health");
    assert.equal(restored.calls[0].url, `${device}/health`);
    for (const changed of [bootstrap({ api_url: "http://10.136.137.227:5051", pairing: null }), bootstrap({ context_id: "N".repeat(22), pairing: null }), emptyBootstrap]) {
        const storage = fakeStorage();
        storage.setItem(storageKey, JSON.stringify(original.session.getConnection()));
        const f = fixture({ bootstrap: changed, storage });
        assert.equal(f.session.getConnection().token, "");
        await assert.rejects(f.session.apiRequest("/health"), /six-digit/);
        assert.equal(f.calls.length, 0);
        assert.equal(storage.entries.size, 0);
    }
});

test("fresh pairing always uses discovery and a DHCP change never receives an old bearer", async () => {
    const old = fixture();
    await old.session.completeDiscoveredPairing();
    await old.session.pairDevice(code);
    assert.equal(old.calls.at(-1).url, "/api/discovery/resolve");
    assert.equal(old.calls.at(-1).options.headers.Authorization, undefined);
    assert.equal(old.session.getConnection().token, "test-session");
    const moved = fixture({ bootstrap: bootstrap({ api_url: "http://10.136.137.227:5051", context_id: "N".repeat(22) }), storage: old.storage });
    await moved.session.completeDiscoveredPairing();
    assert.ok(moved.calls.every(call => call.url.startsWith("http://10.136.137.227:5051/")));
    assert.ok(moved.calls.every(call => call.options.headers.Authorization === undefined));
});

test("malformed bootstrap data cannot cause any local request", async () => {
    const invalid = [
        { ...emptyBootstrap, discovery: false }, bootstrap({ api_url: "http://8.8.8.8:5051" }), bootstrap({ api_url: "http://100.64.0.1:5051" }),
        bootstrap({ api_url: "https://device.tail123.ts.net" }), bootstrap({ context_id: "short" }), bootstrap({ context_id: [contextId] }),
        bootstrap({ pairing: { code, pairing_id: pairingId, proof_key: proofKey, expires_in: 121 } }),
        bootstrap({ pairing: { code, pairing_id: pairingId, proof_key: "short", expires_in: 120 } }),
        bootstrap({ pairing: { code: 123456, pairing_id: pairingId, proof_key: proofKey, expires_in: 120 } }),
    ];
    for (const value of invalid) {
        const f = fixture({ bootstrap: value });
        assert.equal(f.session.getBootstrapError().code, "invalid_bootstrap");
        assert.equal(f.session.getConnection().baseUrl, null);
        await assert.rejects(f.session.apiRequest("/health"), /six-digit/);
        await assert.rejects(f.session.completeDiscoveredPairing(), /new pairing code/);
        assert.equal(f.calls.length, 0);
    }
});

test("the inert bootstrap node is removed promptly even if its JSON is unreadable", () => {
    for (const source of [JSON.stringify(bootstrap()), "invalid json"]) {
        let removed = false;
        const node = { textContent: source, remove: () => { removed = true; } };
        const document = { getElementById: id => { assert.equal(id, "device-bootstrap"); return node; } };
        if (source.startsWith("{")) assert.deepEqual(readDeviceBootstrap(document), bootstrap());
        else assert.throws(() => readDeviceBootstrap(document), /Could not read/);
        assert.equal(removed, true);
    }
});

test("expired or revoked sessions clear only this tab's saved connection", async () => {
    const f = fixture({ fetchImpl: async (url, request, defaults) => url.endsWith("/health") ? Response.json({ success: false, error_code: "session_invalid" }, { status: 401 }) : defaults(url, request) });
    await f.session.completeDiscoveredPairing();
    await assert.rejects(f.session.apiRequest("/health"), /connection has expired/);
    assert.equal(f.session.getConnection().token, "");
    assert.equal(f.storage.entries.size, 0);
    assert.deepEqual(f.events, ["device-session-expired"]);
    const expired = fixture();
    await expired.session.completeDiscoveredPairing();
    expired.setTime(2_000_028_801);
    await assert.rejects(expired.session.apiRequest("/api/uploads"), /connection has expired/);
    assert.equal(expired.calls.length, 2);
});

test("disconnect logs out only the current token and retains it on network failure for retry", async () => {
    let offline = false;
    const active = new Set(["test-session", "other-tab"]);
    const f = fixture({ fetchImpl: async (url, request, defaults) => {
        if (url.endsWith("/logout")) {
            if (offline) throw new TypeError("Failed to fetch");
            active.delete(request.headers.Authorization.slice(7));
        }
        return defaults(url, request);
    } });
    await f.session.completeDiscoveredPairing();
    offline = true;
    await assert.rejects(f.session.disconnectDevice(), /Could not reach/);
    assert.equal(f.session.getConnection().token, "test-session");
    assert.equal(f.storage.entries.size, 1);
    offline = false;
    await f.session.disconnectDevice();
    assert.deepEqual([...active], ["other-tab"]);
    assert.equal(f.session.getConnection().token, "");
    assert.equal(f.storage.entries.size, 0);
    assert.deepEqual(f.events, ["device-disconnected"]);
});

test("disconnect clears a token already revoked by the device", async () => {
    const f = fixture({ fetchImpl: async (url, request, defaults) => url.endsWith("/logout") ? Response.json({ success: false, error_code: "session_invalid" }, { status: 401 }) : defaults(url, request) });
    await f.session.completeDiscoveredPairing();
    await assert.rejects(f.session.disconnectDevice(), /connection has expired/);
    assert.equal(f.session.getConnection().token, "");
    assert.equal(f.storage.entries.size, 0);
});

test("active transfers block discovery, pairing completion, and disconnect", async () => {
    const f = fixture();
    const release = f.session.beginTransfer();
    await assert.rejects(f.session.pairDevice(code), /current transfer/);
    await assert.rejects(f.session.completeDiscoveredPairing(), /current transfer/);
    await assert.rejects(f.session.disconnectDevice(), /current transfer/);
    assert.equal(f.calls.length, 0);
    release(); release();
    await f.session.completeDiscoveredPairing();
    assert.equal(f.session.apiEndpoint("/api/uploads"), `${device}/api/uploads`);
    assert.match(f.session.handleUploadError({ error_code: "session_invalid" }, 401).message, /connection has expired/);
    assert.throws(f.session.apiHeaders, /six-digit/);
});

test("the local API remains upload-scoped with proof and claim always unauthenticated", async () => {
    const calls = [];
    const client = createApiClient({ baseUrl: device, token: "private-token", fetchImpl: async (url, options) => { calls.push({ url, options }); return Response.json({ success: true }); } });
    for (const path of ["/begin_recording", "/playback/play", "/feedback/stop", "/api/pairing/start", "/analyze", "//foreign.example"]) assert.throws(() => client.endpoint(path), /Invalid song library/);
    await client.request("/api/pairing/proof", { method: "POST", body: { pairing_id: pairingId, challenge: "0".repeat(64) } });
    assert.equal(calls[0].options.headers.Authorization, undefined);
    await assert.rejects(client.request("/api/pairing/proof", { authenticated: true }), /authentication scope/);
    await assert.rejects(client.request("/api/pairing/proof", { authenticated: "false" }), /authentication scope/);
    await assert.rejects(client.request("/api/uploads", { authenticated: false }), /authentication scope/);
    await client.request("/api/uploads/song%20name.wav", { method: "DELETE" });
    assert.equal(calls[1].options.headers.Authorization, "Bearer private-token");
    assert.equal(calls[1].options.method, "DELETE");
    assert.equal(calls[1].options.targetAddressSpace, "local");
    assert.throws(() => createApiClient(), /six-digit/);
});

test("local failure diagnostics are browser-neutral and run only after actual fetch failure", async () => {
    const originalNavigator = globalThis.navigator;
    try {
        const events = [];
        globalThis.navigator = { permissions: { query: async ({ name }) => { events.push(name); return { state: "denied" }; } } };
        const denied = createApiClient({ baseUrl: device, token: "token", fetchImpl: async () => { events.push("fetch"); throw new TypeError("Failed to fetch"); } });
        await assert.rejects(denied.request("/health"), error => { assert.match(error.message, /Allow local network access.*site settings/); return error.code === "permission_denied"; });
        assert.deepEqual(events, ["fetch", "local-network"]);
        for (const navigator of [undefined, {}, { permissions: {} }, { permissions: { query: async () => { throw new TypeError("Unsupported permission"); } } }]) {
            globalThis.navigator = navigator;
            const client = createApiClient({ baseUrl: device, token: "secret-token", fetchImpl: async () => { throw new TypeError("Failed to fetch"); } });
            await assert.rejects(client.request("/health"), error => {
                assert.ok(error.message.includes(device));
                assert.match(error.message, /same Wi-Fi/);
                assert.match(error.message, /browser may be blocking.*HTTP connection/);
                assert.ok(!error.message.includes("secret-token"));
                return error.code === "offline";
            });
        }
    } finally { globalThis.navigator = originalNavigator; }
});

function fakeConnectionUi() {
    const elements = new Map();
    for (const selector of ["form", "#device-pairing-code", "#device-connection-state", "#device-connection-message", "details", "#device-retry", "#device-disconnect", "button"]) elements.set(selector, Object.assign(new EventTarget(), { value: "", textContent: "" }));
    elements.get("form").querySelector = selector => elements.get(selector);
    const host = new EventTarget();
    host.querySelector = selector => elements.get(selector);
    const document = new EventTarget();
    document.querySelector = selector => selector === "#device-connection" ? host : null;
    return { elements, host, document };
}

test("the pairing UI navigates after discovery without issuing health on the old document for any browser brand", async () => {
    const originalDocument = globalThis.document;
    const originalNavigator = globalThis.navigator;
    try {
        for (const userAgent of ["Version/18.0 Safari/605.1.15", "Firefox/157.0", "iPhone OS 18_0 Mobile Safari/604.1", "Chrome/90.0"]) {
            const ui = fakeConnectionUi();
            globalThis.document = ui.document;
            globalThis.navigator = { userAgent };
            let navigated;
            const navigation = new Promise(resolve => { navigated = resolve; });
            const f = fixture({ bootstrap: emptyBootstrap, navigate: path => { assert.equal(path, "/"); navigated(); } });
            initConnectionSettings({ session: f.session });
            assert.match(ui.host.innerHTML, /same Wi-Fi/);
            ui.elements.get("#device-pairing-code").value = code;
            ui.elements.get("form").dispatchEvent(new Event("submit", { cancelable: true }));
            assert.equal(ui.elements.get("#device-pairing-code").value, "");
            await navigation;
            assert.deepEqual(f.calls.map(call => call.url), ["/api/discovery/resolve"]);
        }
    } finally { globalThis.document = originalDocument; globalThis.navigator = originalNavigator; }
});

test("a pending bootstrap automatically verifies, claims, and checks health through the UI", async () => {
    const originalDocument = globalThis.document;
    const originalCustomEvent = globalThis.CustomEvent;
    try {
        const ui = fakeConnectionUi();
        globalThis.document = ui.document;
        globalThis.CustomEvent = class extends Event {};
        const connected = new Promise((resolve, reject) => {
            ui.host.addEventListener("device-connected", resolve, { once: true });
            ui.host.addEventListener("device-connection-failed", () => reject(new Error(ui.elements.get("#device-connection-message").textContent)), { once: true });
        });
        const f = fixture();
        initConnectionSettings({ session: f.session });
        await connected;
        assert.deepEqual(f.calls.map(call => call.url), [`${device}/api/pairing/proof`, `${device}/api/pairing/claim`, `${device}/health`]);
        assert.equal(ui.elements.get("#device-connection-state").textContent, "Paired");
        assert.equal(ui.elements.get("form").hidden, true);
        assert.equal(ui.elements.get("#device-disconnect").hidden, false);
    } finally { globalThis.document = originalDocument; globalThis.CustomEvent = originalCustomEvent; }
});

test("the UI retries a failed proof locally from memory without another code or directory lookup", async () => {
    const originalDocument = globalThis.document;
    const originalCustomEvent = globalThis.CustomEvent;
    try {
        const ui = fakeConnectionUi();
        globalThis.document = ui.document;
        globalThis.CustomEvent = class extends Event {};
        let fail = true;
        const f = fixture({ fetchImpl: async (url, request, defaults) => {
            if (fail) { fail = false; throw new TypeError("Failed to fetch"); }
            return defaults(url, request);
        } });
        const failed = new Promise(resolve => ui.host.addEventListener("device-connection-failed", resolve, { once: true }));
        initConnectionSettings({ session: f.session });
        await failed;
        assert.equal(ui.elements.get("#device-retry").hidden, false);
        assert.equal(ui.elements.get("#device-pairing-code").value, "");
        const connected = new Promise(resolve => ui.host.addEventListener("device-connected", resolve, { once: true }));
        ui.elements.get("#device-retry").dispatchEvent(new Event("click"));
        await connected;
        assert.ok(f.calls.every(call => call.url.startsWith(device)));
        assert.equal(f.calls.filter(call => call.url.endsWith("/proof")).length, 2);
        assert.equal(f.calls.filter(call => call.url.endsWith("/claim")).length, 1);
        assert.equal(ui.elements.get("#device-retry").hidden, true);
    } finally { globalThis.document = originalDocument; globalThis.CustomEvent = originalCustomEvent; }
});

test("downloads preserve audio and validation rejects unsupported or oversized songs", async () => {
    const client = createApiClient({ baseUrl: device, token: "token", fetchImpl: async () => new Response(new Blob(["audio"], { type: "audio/wav" })) });
    assert.equal(await (await client.request("/api/uploads/song.wav", { blob: true })).text(), "audio");
    assert.doesNotThrow(() => validateAudioFile({ name: "song.MP3", size: MAX_AUDIO_BYTES - 1 }));
    assert.throws(() => validateAudioFile({ name: "song.wav", size: MAX_AUDIO_BYTES }), /smaller than 95/);
    assert.throws(() => validateAudioFile({ name: "script.js", size: 10 }), /WAV, MP3/);
    assert.match(responseError({}, 429).message, /Too many attempts/);
});
