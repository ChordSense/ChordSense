import test from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import discovery from "../server/discovery-store.cjs";

const { createStore, StoreError } = discovery;
const execute = promisify(execFile);
const secret = "server-only-secret-with-at-least-32-bytes";
const token = "server-only-redis-token";
const fixture = Object.freeze({ pairing_id: "A".repeat(22), code: "012345",
    api_url: "http://10.136.68.97:5051", proof_key: "P".repeat(43), expires_in: 120 });
const clientId = "C".repeat(64);
const base = { url: "https://redis.example.test", token, secret, clock: () => 60_000 };
const privateRecord = overrides => ({ pairing_id: fixture.pairing_id, api_url: fixture.api_url,
    proof_key: fixture.proof_key, original_ttl: 120, expires_at_ms: 180_000, ...overrides });
const response = result => new Response(JSON.stringify({ result }), {
    status: 200, headers: { "Content-Type": "application/json" },
});

function mockStore(reply, options = {}) {
    const calls = [];
    const store = createStore({ ...base, ...options, fetchImpl: async (url, request) => {
        const command = JSON.parse(request.body);
        calls.push({ url, request, command });
        return typeof reply === "function" ? reply(command) : response(reply);
    } });
    return { store, calls };
}

function isStoreError(code, status) {
    return error => error instanceof StoreError && error.code === code &&
        error.status === status && error.statusCode === status;
}

test("the store accepts only an HTTPS REST origin and server credentials", () => {
    for (const url of [undefined, "http://redis.example.test", "https://user:password@redis.example.test",
        "https://redis.example.test/path", "https://redis.example.test?token=secret",
        "https://redis.example.test#fragment", "https://redis.example.test/../"]) {
        assert.throws(() => createStore({ ...base, url }), isStoreError("discovery_unavailable", 503));
    }
    for (const options of [{ token: "" }, { token: "token\nsecret" }, { secret: "short" },
        { prefix: "unsafe key" }, { prefix: "" }, { clock: null }, { fetchImpl: null }]) {
        assert.throws(() => createStore({ ...base, ...options }), isStoreError("discovery_unavailable", 503));
    }
    assert.doesNotThrow(() => createStore({ ...base, url: `${base.url}/`, prefix: "isolated:test-1" }));
});

test("publication uses one atomic EVAL, a code HMAC, fixed keys and private record fields", async () => {
    const { store, calls } = mockStore(["ok", 120]);
    assert.deepEqual(await store.publish(fixture), { pairing_id: fixture.pairing_id, expires_in: 120 });
    assert.equal(calls.length, 1);
    const { url, request, command } = calls[0];
    assert.equal(url, base.url);
    assert.equal(request.method, "POST");
    assert.equal(request.redirect, "error");
    assert.equal(request.cache, "no-store");
    assert.equal(request.headers.Authorization, `Bearer ${token}`);
    assert.ok(request.signal instanceof AbortSignal);
    assert.equal(command[0], "EVAL");
    assert.equal(command[2], 1);
    assert.equal(command[3], "chordsense:discovery:v1:active");
    assert.equal(command[4], fixture.pairing_id);
    assert.equal(command[5], createHmac("sha256", secret).update(`code\0${fixture.code}`).digest("hex"));
    assert.equal(command[6], fixture.api_url);
    assert.equal(command[7], fixture.proof_key);
    assert.deepEqual(JSON.parse(command[8]), privateRecord());
    assert.equal(command[9], 120);
    assert.ok(!request.body.includes(fixture.code));
    assert.ok(!request.body.includes(secret));
    assert.ok(!request.body.includes(token));
});

test("publication validation rejects malformed identity, proof, lifetime and non-LAN endpoints before Redis", async () => {
    const { store, calls } = mockStore(["ok", 120]);
    for (const override of [{ pairing_id: "short" }, { pairing_id: "A".repeat(65) },
        { proof_key: "P".repeat(42) }, { proof_key: "P".repeat(44) }, { proof_key: "+".repeat(43) },
        { code: 12345 }, { code: "12345" }, { code: "1234567" }, { code: "12345x" },
        { expires_in: 0 }, { expires_in: 121 }, { expires_in: 1.5 }]) {
        await assert.rejects(store.publish({ ...fixture, ...override }), isStoreError("invalid_request", 400));
    }
    for (const api_url of ["http://8.8.8.8:5051", "http://100.71.233.14:5051",
        "http://127.0.0.1:5051", "http://chordsense.local:5051", "https://10.1.2.3:5051",
        "http://010.1.2.3:5051", "http://10.1.2.256:5051", "http://10.1.2.3:5051/",
        "http://10.1.2.3:5051?code=123456", "http://user:secret@10.1.2.3:5051"]) {
        await assert.rejects(store.publish({ ...fixture, api_url }), isStoreError("invalid_request", 400));
    }
    assert.equal(calls.length, 0);
});

test("resolution returns only a matched record and clamps TTL to its original lifetime and deadline", async () => {
    const { store, calls } = mockStore(["ok", JSON.stringify(privateRecord({ original_ttl: 30,
        expires_at_ms: 75_000 })), 100], { prefix: "test:isolated" });
    assert.deepEqual(await store.resolve({ code: fixture.code, clientId }), {
        pairing_id: fixture.pairing_id, api_url: fixture.api_url, proof_key: fixture.proof_key,
        expires_in: 15,
    });
    assert.equal(calls[0].command[2], 2);
    assert.equal(calls[0].command[3], "test:isolated:active");
    assert.match(calls[0].command[4], /^test:isolated:rate:[a-f0-9]{64}$/);
    assert.ok(!calls[0].request.body.includes(clientId));
    assert.ok(!calls[0].request.body.includes(fixture.code));
    const expired = mockStore(["ok", JSON.stringify(privateRecord({ expires_at_ms: 59_000 })), 100]);
    await assert.rejects(expired.store.resolve({ code: fixture.code, clientId }), isStoreError("pairing_unavailable", 409));
});

test("resolution and revocation validate input without a Redis request", async () => {
    const { store, calls } = mockStore(["ok", 0]);
    for (const fields of [{ code: 123456, clientId }, { code: "12345", clientId },
        { code: fixture.code, clientId: "short" }, { code: fixture.code, clientId: "x:".repeat(32) }]) {
        await assert.rejects(store.resolve(fields), isStoreError("invalid_request", 400));
    }
    await assert.rejects(store.revoke("short"), isStoreError("invalid_request", 400));
    for (const input of [null, [], "malformed"]) {
        await assert.rejects(store.publish(input), isStoreError("invalid_request", 400));
        await assert.rejects(store.resolve(input), isStoreError("invalid_request", 400));
    }
    assert.equal(calls.length, 0);
});

test("Redis lookup statuses map to stable typed errors without exposing stored inputs", async () => {
    for (const [code, status] of [["pairing_unavailable", 409], ["invalid_code", 401], ["pairing_locked", 429], ["rate_limited", 429]]) {
        const { store } = mockStore([code]);
        await assert.rejects(store.resolve({ code: fixture.code, clientId }), error => {
            assert.ok(isStoreError(code, status)(error));
            for (const value of [secret, token, fixture.code, fixture.proof_key, fixture.api_url]) {
                assert.ok(!error.message.includes(value));
            }
            return true;
        });
    }
});

test("conditional revocation is idempotent and has an isolated configurable namespace", async () => {
    for (const deleted of [0, 1]) {
        const { store, calls } = mockStore(["ok", deleted], { prefix: "test:owned-record" });
        assert.deepEqual(await store.revoke(fixture.pairing_id), {
            pairing_id: fixture.pairing_id, revoked: deleted === 1,
        });
        assert.equal(calls[0].command[3], "test:owned-record:active");
        assert.equal(calls[0].command[4], fixture.pairing_id);
    }
});

test("transport, Redis and malformed response failures fail closed with no sensitive error details", async () => {
    const replies = [
        async () => { throw new Error(`Network error ${token} ${secret}`); },
        async () => new Response(JSON.stringify({ error: `Redis error ${fixture.proof_key}` })),
        async () => new Response("upstream failed", { status: 502 }),
        async () => new Response("not json"),
        async () => response(["unknown_private_error"]),
        async () => response(["ok", "not json", 120]),
        async () => response(["ok", JSON.stringify(privateRecord({ proof_key: "invalid" })), 120]),
        async () => response(["ok", JSON.stringify(privateRecord()), 121]),
        async () => response(["ok", JSON.stringify(privateRecord()), 0]),
        async () => response(["pairing_locked", fixture.proof_key]),
    ];
    for (const reply of replies) {
        const { store } = mockStore(reply);
        await assert.rejects(store.resolve({ code: fixture.code, clientId }), error => {
            assert.ok(isStoreError("discovery_unavailable", 503)(error));
            assert.equal(error.message, "Pairing discovery is temporarily unavailable.");
            return true;
        });
    }
});

test("Redis response size is bounded even without a Content-Length header", async () => {
    let cancelled = false;
    const body = new ReadableStream({
        start(controller) { controller.enqueue(new Uint8Array(32 * 1024 + 1)); },
        cancel() { cancelled = true; },
    });
    const { store } = mockStore(async () => new Response(body));
    await assert.rejects(store.resolve({ code: fixture.code, clientId }), isStoreError("discovery_unavailable", 503));
    assert.equal(cancelled, true);
    const largeHeader = mockStore(async () => new Response("{}", { headers: { "Content-Length": "32769" } }));
    await assert.rejects(largeHeader.store.resolve({ code: fixture.code, clientId }), isStoreError("discovery_unavailable", 503));
});

test("Redis fetch is aborted after three seconds and fails closed", async () => {
    const hanging = createStore({ ...base, fetchImpl: async (_url, { signal }) => new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
    }) });
    await assert.rejects(hanging.resolve({ code: fixture.code, clientId }), isStoreError("discovery_unavailable", 503));
});

test("the actual Lua scripts enforce retryable claims, TTLs, shared wrong limits and conditional revoke", async context => {
    try { await execute("luajit", ["-e", "io.write(_VERSION)"]); } catch (error) {
        if (error.code === "ENOENT") return context.skip("LuaJIT is not installed; Redis integration remains a separate check.");
        throw error;
    }
    const { store, calls } = mockStore(command => {
        if (command[2] === 2) return response(["ok", JSON.stringify(privateRecord()), 120]);
        if (command.length === 5) return response(["ok", 1]);
        return response(["ok", 120]);
    });
    await store.publish(fixture);
    await store.revoke(fixture.pairing_id);
    await store.resolve({ code: fixture.code, clientId });
    const [publishScript, revokeScript, resolveScript] = calls.map(call => call.command[1]);
    // The Redis-command model is independent of the scripts. LuaJIT executes the
    // production script text, catching syntax/control-flow mistakes without a server.
    const harness = `
local publish = ${JSON.stringify(publishScript)}
local revoke = ${JSON.stringify(revokeScript)}
local resolve = ${JSON.stringify(resolveScript)}
local values, deadlines = {}, {}
local time = 0
local function expire(key)
    if deadlines[key] and deadlines[key] <= time then values[key] = nil; deadlines[key] = nil end
end
redis = { call = function(command, ...)
    local a = {...}; local key = a[1]; expire(key)
    if command == 'HGET' then return values[key] and values[key][a[2]] or false end
    if command == 'HSET' then
        values[key] = values[key] or {}
        for index = 2, #a, 2 do values[key][a[index]] = tostring(a[index + 1]) end
        return 1
    end
    if command == 'HINCRBY' then
        local count = tonumber(values[key][a[2]] or '0') + tonumber(a[3])
        values[key][a[2]] = tostring(count); return count
    end
    if command == 'INCR' then
        local count = tonumber(values[key] or '0') + 1; values[key] = tostring(count); return count
    end
    if command == 'EXPIRE' then
        if not values[key] then return 0 end
        deadlines[key] = time + tonumber(a[2]); return 1
    end
    if command == 'TTL' then
        if not values[key] then return -2 end
        if not deadlines[key] then return -1 end
        return math.floor(deadlines[key] - time)
    end
    if command == 'EXISTS' then return values[key] and 1 or 0 end
    if command == 'DEL' then
        local count = values[key] and 1 or 0; values[key] = nil; deadlines[key] = nil; return count
    end
    error('Unexpected Redis command: ' .. command)
end }
local function run(script, keys, args)
    KEYS = keys; ARGV = {}
    for index, value in ipairs(args) do ARGV[index] = tostring(value) end
    return assert(loadstring(script))()
end
local function put(id, digest, lifetime)
    return run(publish, {'active'}, {id, digest, 'http://10.1.2.3:5051', 'proof', 'private-json', lifetime})
end
local function lookup(digest, client)
    return run(resolve, {'active', client}, {digest})
end
assert(put('window-a', 'digest-a', 120)[2] == 120)
time = 20
assert(put('window-a', 'digest-a', 120)[2] == 100)
assert(deadlines.active == 120, 'idempotent publication must not extend TTL')
assert(put('window-a', 'different', 120)[1] == 'pairing_unavailable')
for attempt = 1, 2 do
    local result = lookup('digest-a', 'retry-client')
    assert(result[1] == 'ok' and result[2] == 'private-json' and result[3] == 100)
end
assert(values.active.wrong == '0', 'successful lookups must not consume or count as wrong')
for attempt = 1, 4 do assert(lookup('wrong', 'wrong-client-' .. attempt)[1] == 'invalid_code') end
assert(lookup('wrong', 'fifth-client')[1] == 'pairing_locked')
assert(lookup('digest-a', 'correct-after-lock')[1] == 'pairing_locked', 'wrong count must be global')
assert(values.active.wrong == '5')
assert(deadlines.active == 120, 'lookups must not extend original TTL')
assert(put('window-b', 'digest-b', 120)[1] == 'ok')
assert(values.active.wrong == '0', 'a new pairing panel resets only its wrong count')
assert(run(revoke, {'active'}, {'window-a'})[2] == 0)
assert(values.active.pairing_id == 'window-b', 'stale revoke must preserve replacement')
assert(run(revoke, {'active'}, {'window-b'})[2] == 1)
assert(run(revoke, {'active'}, {'window-b'})[2] == 0)
assert(lookup('digest-b', 'empty-client')[1] == 'pairing_unavailable')
assert(put('window-c', 'digest-c', 1)[1] == 'ok')
time = 21
assert(lookup('digest-c', 'expired-client')[1] == 'pairing_unavailable')
assert(put('window-d', 'digest-d', 120)[1] == 'ok')
for attempt = 1, 30 do assert(lookup('digest-d', 'limited-client')[1] == 'ok') end
local rateDeadline = deadlines['limited-client']
time = 31
assert(lookup('digest-d', 'limited-client')[1] == 'rate_limited')
assert(deadlines['limited-client'] == rateDeadline, 'rate attempts must not extend their window')
assert(lookup('digest-d', 'other-client')[1] == 'ok')
assert(values.active.wrong == '0', 'rate rejection must not count as a wrong code')
deadlines.active = time + 500
assert(lookup('digest-d', 'ttl-clamp-client')[3] == 120)
time = rateDeadline
assert(lookup('digest-d', 'limited-client')[1] == 'ok', 'rate window must expire')
io.write('atomic-script-model-ok')
`;
    const result = await execute("luajit", ["-e", harness], { timeout: 5000, maxBuffer: 4096 });
    assert.equal(result.stdout, "atomic-script-model-ok");
});
