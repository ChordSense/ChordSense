import test from "node:test";
import assert from "node:assert/strict";
import { apiRequest, apiHeaders, beginTransfer, createApiClient, disconnectDevice, getConnection, handleUploadError, MAX_AUDIO_BYTES, normalizeApiUrl, pairDevice, responseError, validateAudioFile } from "./api-client.js";
import { localRequestOptions } from "./endpoint.js";
import { initConnectionSettings } from "./connection-settings.js";

const device = "http://chordsense.local:5051";
const claim = token => ({ success: true, token, token_type: "Bearer", expires_at: Date.now() / 1000 + 28800, expires_in: 28800 });

function fakeStorage() {
    const entries = new Map();
    return { entries, getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key) };
}

test("library requests use scoped bearer headers and local network annotation", async () => {
    const calls = [];
    const client = createApiClient({ token: "private-token", fetchImpl: async (url, options) => {
        calls.push({ url, options });
        return Response.json({ success: true, files: [{ name: "song.wav" }] });
    } });
    const result = await client.request("/api/uploads");
    await client.request("/api/uploads/song.wav", { method: "DELETE" });
    assert.equal(result.files[0].name, "song.wav");
    assert.equal(calls[1].options.method, "DELETE");
    for (const call of calls) {
        assert.equal(call.options.headers.Authorization, "Bearer private-token");
        assert.equal(call.options.targetAddressSpace, "local");
        assert.equal(call.options.credentials, "omit");
        assert.ok(!call.url.includes("private-token"));
    }
});

test("claiming a leading-zero code requires no bearer and stores only its tab session", async () => {
    const originalFetch = globalThis.fetch;
    const originalStorage = globalThis.sessionStorage;
    const storage = fakeStorage();
    const calls = [];
    globalThis.sessionStorage = storage;
    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return Response.json(url.endsWith("/api/pairing/claim") ? claim("session-secret") : { success: true });
    };
    try {
        await pairDevice("012345");
        await apiRequest("/health");
        assert.equal(calls[0].url, `${device}/api/pairing/claim`);
        assert.deepEqual(JSON.parse(calls[0].options.body), { code: "012345" });
        assert.equal(calls[0].options.headers.Authorization, undefined);
        assert.equal(calls[0].options.headers["Content-Type"], "application/json");
        assert.equal(calls[1].options.headers.Authorization, "Bearer session-secret");
        const saved = JSON.parse([...storage.entries.values()][0]);
        assert.equal(saved.token, "session-secret");
        assert.ok(!JSON.stringify(saved).includes("012345"));
    } finally { globalThis.fetch = originalFetch; globalThis.sessionStorage = originalStorage; }
});

test("invalid pairing codes are rejected without contacting the device", async () => {
    const originalFetch = globalThis.fetch;
    let requests = 0;
    globalThis.fetch = async () => { requests++; return Response.json(claim("unused")); };
    try {
        for (const code of [123456, "12345", "abcdef", "１２３４５６", "12 345"]) await assert.rejects(pairDevice(code), /six-digit/);
        assert.equal(requests, 0);
    } finally { globalThis.fetch = originalFetch; }
});

test("pairing uses the browser's relative lifetime despite a skewed device clock", async () => {
    const originalFetch = globalThis.fetch;
    const originalNow = Date.now;
    Date.now = () => 2_000_000_000_000;
    globalThis.fetch = async () => Response.json({ success: true, token: "clock-skew-session", expires_in: 28800, expires_at: 100 });
    try {
        await pairDevice("123456");
        assert.equal(getConnection().expiresAt, 2_000_000_000 + 28800);
        assert.equal(apiHeaders().Authorization, "Bearer clock-skew-session");
        for (const expiresIn of [0, -1, 28801, "28800"]) {
            globalThis.fetch = async () => Response.json({ success: true, token: "invalid-lifetime", expires_in: expiresIn });
            await assert.rejects(pairDevice("654321"), /could not finish pairing/);
        }
    } finally { globalThis.fetch = originalFetch; Date.now = originalNow; }
});

test("claim errors distinguish wrong, expired, locked, and inactive codes", async () => {
    const cases = [
        [401, "invalid_code", /doesn't match/],
        [410, "pairing_expired", /code has expired/],
        [429, "pairing_locked", /Too many incorrect/],
        [409, "pairing_unavailable", /new pairing code/],
    ];
    for (const [status, errorCode, message] of cases) {
        const client = createApiClient({ fetchImpl: async () => Response.json({ success: false, error_code: errorCode }, { status }) });
        await assert.rejects(client.request("/api/pairing/claim", { method: "POST", body: { code: "123456" } }), error => {
            assert.match(error.message, message);
            assert.equal(error.code, errorCode);
            return true;
        });
    }
});

test("a failed claim preserves an existing valid session", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => Response.json(claim("existing-session"));
    try {
        await pairDevice("123456");
        globalThis.fetch = async () => Response.json({ success: false, error_code: "invalid_code" }, { status: 401 });
        await assert.rejects(pairDevice("654321"), /doesn't match/);
        assert.equal(getConnection().token, "existing-session");
    } finally { globalThis.fetch = originalFetch; }
});

test("revoked sessions are cleared on authenticated 401 and require a fresh code", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async url => Response.json(url.endsWith("/api/pairing/claim") ? claim("revoked-session") : { success: false, error_code: "session_invalid" }, { status: url.endsWith("/api/pairing/claim") ? 200 : 401 });
    try {
        await pairDevice("123456");
        await assert.rejects(apiRequest("/health"), /connection has expired/);
        assert.equal(getConnection().token, "");
        assert.throws(apiHeaders, /six-digit code/);
    } finally { globalThis.fetch = originalFetch; }
});

test("disconnect revokes this tab's token and clears its session without revoking another tab", async () => {
    const originalFetch = globalThis.fetch;
    const originalStorage = globalThis.sessionStorage;
    const originalDocument = globalThis.document;
    const originalEvent = globalThis.CustomEvent;
    const storage = fakeStorage();
    const activeTokens = new Set(["this-tab", "other-tab"]);
    const calls = [];
    const events = [];
    globalThis.sessionStorage = storage;
    globalThis.document = { dispatchEvent: event => events.push(event.type) };
    globalThis.CustomEvent = class { constructor(type) { this.type = type; } };
    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        if (url.endsWith("/api/pairing/claim")) return Response.json(claim("this-tab"));
        const token = options.headers.Authorization?.slice(7);
        if (!activeTokens.has(token)) return Response.json({ success: false }, { status: 401 });
        if (url.endsWith("/api/pairing/logout")) activeTokens.delete(token);
        return Response.json({ success: true });
    };
    try {
        await pairDevice("123456");
        await disconnectDevice();
        const logout = calls.find(call => call.url.endsWith("/api/pairing/logout"));
        assert.equal(logout.options.method, "POST");
        assert.equal(logout.options.headers.Authorization, "Bearer this-tab");
        assert.equal(logout.options.targetAddressSpace, "local");
        assert.deepEqual([...activeTokens], ["other-tab"]);
        assert.equal(getConnection().token, "");
        assert.equal(storage.entries.size, 0);
        assert.deepEqual(events, ["device-disconnected"]);
        assert.throws(apiHeaders, /six-digit code/);
        await createApiClient({ token: "other-tab" }).request("/health");
        assert.equal(calls.filter(call => call.url.endsWith("/api/pairing/logout")).length, 1);
    } finally {
        globalThis.fetch = originalFetch; globalThis.sessionStorage = originalStorage;
        globalThis.document = originalDocument; globalThis.CustomEvent = originalEvent;
    }
});

test("disconnect waits for transfers and retains the token on network failure so revocation can be retried", async () => {
    const originalFetch = globalThis.fetch;
    const originalStorage = globalThis.sessionStorage;
    const storage = fakeStorage();
    let requests = 0;
    let offline = false;
    globalThis.sessionStorage = storage;
    globalThis.fetch = async url => {
        requests++;
        if (offline) throw new TypeError("Failed to fetch");
        return Response.json(url.endsWith("/api/pairing/claim") ? claim("retry-disconnect") : { success: true });
    };
    try {
        await pairDevice("123456");
        const finish = beginTransfer();
        await assert.rejects(disconnectDevice(), /current transfer/);
        assert.equal(requests, 1);
        finish();
        offline = true;
        await assert.rejects(disconnectDevice(), /Could not reach ChordSense/);
        assert.equal(getConnection().token, "retry-disconnect");
        assert.equal(JSON.parse([...storage.entries.values()][0]).token, "retry-disconnect");
        offline = false;
        await disconnectDevice();
        assert.equal(getConnection().token, "");
        assert.equal(storage.entries.size, 0);
    } finally { globalThis.fetch = originalFetch; globalThis.sessionStorage = originalStorage; }
});

test("disconnect clears an already expired or revoked remote token on 401", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async url => Response.json(url.endsWith("/api/pairing/claim") ? claim("already-revoked") : { success: false, error_code: "session_invalid" }, { status: url.endsWith("/api/pairing/claim") ? 200 : 401 });
    try {
        await pairDevice("123456");
        await assert.rejects(disconnectDevice(), /connection has expired/);
        assert.equal(getConnection().token, "");
    } finally { globalThis.fetch = originalFetch; }
});

test("expired sessions are rejected locally without a network request", async () => {
    const originalFetch = globalThis.fetch;
    const originalNow = Date.now;
    let requests = 0;
    globalThis.fetch = async () => { requests++; return Response.json(claim("short-session")); };
    try {
        await pairDevice("123456");
        const expiresAt = getConnection().expiresAt;
        Date.now = () => (expiresAt + 1) * 1000;
        await assert.rejects(apiRequest("/api/uploads"), /connection has expired/);
        assert.equal(requests, 1);
        assert.equal(getConnection().token, "");
    } finally { globalThis.fetch = originalFetch; Date.now = originalNow; }
});

test("uploads clear expired authentication and active transfers block re-pairing", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => Response.json(claim("upload-session"));
    try {
        await pairDevice("123456");
        const finish = beginTransfer();
        await assert.rejects(pairDevice("654321"), /current transfer/);
        finish(); finish();
        assert.match(handleUploadError({ error_code: "session_invalid" }, 401).message, /connection has expired/);
        assert.equal(getConnection().token, "");
        await pairDevice("654321");
    } finally { globalThis.fetch = originalFetch; }
});

test("companion endpoints exclude capture, playback, analysis, and foreign hosts", () => {
    const client = createApiClient({ token: "token" });
    for (const path of ["/begin_recording", "/playback/play", "/feedback/stop", "/api/pairing/start", "/analyze", "//foreign.example.com"]) assert.throws(() => client.endpoint(path), /Invalid song library/);
    assert.equal(client.endpoint("/api/uploads/song%20name.wav"), `${device}/api/uploads/song%20name.wav`);
    assert.equal(normalizeApiUrl(device), device);
    assert.equal(normalizeApiUrl("http://localhost:5051"), "http://localhost:5051");
    assert.equal(normalizeApiUrl("https://chordsense.local:5051"), "https://chordsense.local:5051");
    assert.deepEqual(localRequestOptions("http://localhost:5051"), { targetAddressSpace: "loopback" });
    for (const url of ["https://device.example.com", "http://chordsense.local:5052", "http://user:secret@chordsense.local:5051", `${device}?token=secret`, `${device}/api`]) assert.throws(() => normalizeApiUrl(url), /local address/);
});

test("configured private IPv4 devices use local access while public and ambiguous addresses are rejected", async () => {
    for (const address of ["10.136.137.225", "10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.1.10"]) {
        const endpoint = `http://${address}:5051`;
        assert.equal(normalizeApiUrl(`${endpoint}/`), endpoint);
        assert.equal(normalizeApiUrl(`https://${address}:5051`), `https://${address}:5051`);
        assert.deepEqual(localRequestOptions(endpoint), { targetAddressSpace: "local" });
    }
    const rejected = [
        "8.8.8.8", "172.15.255.255", "172.32.0.1", "192.167.1.1", "192.169.1.1", "169.254.1.1", "0.0.0.0",
        "10.136.137.256", "10.136.137", "10.136.137.025", "010.136.137.225", "0x0a.136.137.225", "176719585", "127.1",
    ];
    for (const address of rejected) assert.throws(() => normalizeApiUrl(`http://${address}:5051`), /local address/);
    for (const endpoint of ["http://10.136.137.225:5052", "http://user:secret@10.136.137.225:5051", "http://10.136.137.225:5051/path", "http://10.136.137.225:5051?token=value", "http://10.136.137.225:5051#fragment"]) {
        assert.throws(() => normalizeApiUrl(endpoint), /local address/);
    }
    let options;
    const client = createApiClient({ baseUrl: "http://10.136.137.225:5051", token: "paired-token", fetchImpl: async (_url, requestOptions) => { options = requestOptions; return Response.json({ success: true }); } });
    await client.request("/health");
    assert.equal(options.targetAddressSpace, "local");
    assert.equal(options.headers.Authorization, "Bearer paired-token");
});

test("local permission denial is diagnosed only after a failed fetch with browser-neutral guidance", async () => {
    const originalNavigator = globalThis.navigator;
    const events = [];
    globalThis.navigator = { permissions: { query: async () => { events.push("permission-query"); return { state: "denied" }; } } };
    try {
        const client = createApiClient({ token: "token", fetchImpl: async () => { events.push("fetch"); throw new TypeError("Failed to fetch"); } });
        await assert.rejects(client.request("/health"), error => {
            assert.match(error.message, /Allow local network access.*browser's site settings/);
            assert.equal(error.code, "permission_denied");
            return true;
        });
        assert.deepEqual(events, ["fetch", "permission-query"]);
    } finally { globalThis.navigator = originalNavigator; }
});

test("network errors tolerate missing or unsupported permission APIs", async () => {
    const originalNavigator = globalThis.navigator;
    try {
        for (const navigator of [undefined, {}, { permissions: {} }, { permissions: { query: async () => { throw new TypeError("Unknown permission name"); } } }]) {
            globalThis.navigator = navigator;
            let requests = 0;
            const client = createApiClient({ token: "token", fetchImpl: async () => { requests++; throw new TypeError("Failed to fetch"); } });
            await assert.rejects(client.request("/health"), error => {
                assert.match(error.message, /Could not reach ChordSense/);
                assert.match(error.message, /browser may be blocking.*HTTP connection/);
                assert.equal(error.code, "offline");
                return true;
            });
            assert.equal(requests, 1);
        }
    } finally { globalThis.navigator = originalNavigator; }
});

test("the pairing form attempts claim and health for Safari, Firefox, iOS, and older browser user agents", async () => {
    const originalFetch = globalThis.fetch;
    const originalNavigator = globalThis.navigator;
    const originalLocation = globalThis.location;
    const originalDocument = globalThis.document;
    const originalEvent = globalThis.CustomEvent;
    class TestEvent extends Event { constructor(type, options) { super(type, options); } }
    const agents = ["Version/18.0 Safari/605.1.15", "Firefox/132.0", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Version/18.0 Mobile/15E148 Safari/604.1", "Chrome/90.0"];
    try {
        globalThis.location = { protocol: "https:" };
        globalThis.CustomEvent = TestEvent;
        for (const userAgent of agents) {
            globalThis.document = undefined;
            handleUploadError({}, 401);
            let permissionQueries = 0;
            globalThis.navigator = { userAgent, permissions: { query: async () => { permissionQueries++; throw new Error("Permission diagnostics should not run on success"); } } };
            const elements = new Map();
            for (const selector of ["form", "#device-pairing-code", "#device-connection-state", "#device-connection-message", "details", "#device-disconnect", "button"]) elements.set(selector, Object.assign(new EventTarget(), { value: "", textContent: "" }));
            const form = elements.get("form");
            form.querySelector = selector => elements.get(selector);
            const host = new EventTarget();
            host.querySelector = selector => elements.get(selector);
            const document = new EventTarget();
            document.querySelector = selector => selector === "#device-connection" ? host : null;
            globalThis.document = document;
            const calls = [];
            globalThis.fetch = async (url, options) => {
                calls.push({ url, options });
                return Response.json(url.endsWith("/api/pairing/claim") ? claim("browser-neutral-session") : { success: true });
            };
            const connected = new Promise((resolve, reject) => {
                host.addEventListener("device-connected", resolve, { once: true });
                host.addEventListener("device-connection-failed", () => reject(new Error(elements.get("#device-connection-message").textContent)), { once: true });
            });
            initConnectionSettings();
            elements.get("#device-pairing-code").value = "012345";
            form.dispatchEvent(new Event("submit", { cancelable: true }));
            await connected;
            assert.equal(calls[0].url, `${device}/api/pairing/claim`);
            assert.deepEqual(JSON.parse(calls[0].options.body), { code: "012345" });
            assert.equal(calls[1].url, `${device}/health`);
            assert.equal(elements.get("#device-connection-state").textContent, "Paired");
            assert.equal(permissionQueries, 0);
            await disconnectDevice();
        }
    } finally {
        globalThis.fetch = originalFetch; globalThis.navigator = originalNavigator; globalThis.location = originalLocation;
        globalThis.document = originalDocument; globalThis.CustomEvent = originalEvent;
    }
});

test("downloads preserve audio and file validation rejects unsupported or oversized songs", async () => {
    const client = createApiClient({ token: "token", fetchImpl: async () => new Response(new Blob(["audio"], { type: "audio/wav" })) });
    assert.equal(await (await client.request("/api/uploads/song.wav", { blob: true })).text(), "audio");
    assert.doesNotThrow(() => validateAudioFile({ name: "song.MP3", size: MAX_AUDIO_BYTES - 1 }));
    assert.throws(() => validateAudioFile({ name: "song.wav", size: MAX_AUDIO_BYTES }), /smaller than 95/);
    assert.throws(() => validateAudioFile({ name: "script.js", size: 10 }), /WAV, MP3/);
    assert.match(responseError({}, 429).message, /Too many attempts/);
});
