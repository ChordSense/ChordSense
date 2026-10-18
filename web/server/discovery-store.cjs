"use strict";

const { createHmac } = require("node:crypto");

const DEFAULT_PREFIX = "chordsense:discovery:v1";
const MAX_RESPONSE_BYTES = 32 * 1024;
const STORE_TIMEOUT_MS = 3000;
const messages = Object.freeze({
    pairing_unavailable: [409, "Pairing is not available. Generate a new code on the device."],
    invalid_code: [401, "Pairing code is incorrect."],
    pairing_locked: [429, "Pairing is locked. Generate a new code on the device."],
    rate_limited: [429, "Too many pairing attempts. Try again later."],
    invalid_request: [400, "Invalid discovery request."],
    discovery_unavailable: [503, "Pairing discovery is temporarily unavailable."],
});

class StoreError extends Error {
    constructor(code) {
        const [status, message] = messages[code] || messages.discovery_unavailable;
        super(message);
        this.name = "StoreError";
        this.code = Object.hasOwn(messages, code) ? code : "discovery_unavailable";
        this.status = status;
        this.statusCode = status;
    }
}

// All state changes, including the initial TTL, happen in one Redis invocation.
// Repeating a publication never extends its original pairing deadline.
const PUBLISH = `
local existing = redis.call('HGET', KEYS[1], 'pairing_id')
if existing == ARGV[1] then
    if redis.call('HGET', KEYS[1], 'code_digest') ~= ARGV[2]
        or redis.call('HGET', KEYS[1], 'api_url') ~= ARGV[3]
        or redis.call('HGET', KEYS[1], 'proof_key') ~= ARGV[4] then
        return {'pairing_unavailable'}
    end
    local remaining = redis.call('TTL', KEYS[1])
    if remaining < 1 then return {'pairing_unavailable'} end
    return {'ok', remaining}
end
redis.call('DEL', KEYS[1])
redis.call('HSET', KEYS[1], 'pairing_id', ARGV[1], 'code_digest', ARGV[2],
    'api_url', ARGV[3], 'proof_key', ARGV[4], 'record', ARGV[5],
    'original_ttl', ARGV[6], 'wrong', '0')
redis.call('EXPIRE', KEYS[1], ARGV[6])
return {'ok', tonumber(ARGV[6])}
`;

// A late cancellation of an older panel cannot remove a replacement code.
const REVOKE = `
if redis.call('HGET', KEYS[1], 'pairing_id') == ARGV[1] then
    return {'ok', redis.call('DEL', KEYS[1])}
end
return {'ok', 0}
`;

// Correct lookups remain retryable: the Pi owns final one-use claiming.
// Rate checks and wrong-code accounting cannot race across Function instances.
const RESOLVE = `
local count = redis.call('INCR', KEYS[2])
if count == 1 then redis.call('EXPIRE', KEYS[2], 120) end
if count > 30 then return {'rate_limited'} end
if redis.call('EXISTS', KEYS[1]) == 0 then return {'pairing_unavailable'} end
local remaining = redis.call('TTL', KEYS[1])
if remaining < 1 then return {'pairing_unavailable'} end
local wrong = tonumber(redis.call('HGET', KEYS[1], 'wrong') or '0')
if wrong >= 5 then return {'pairing_locked'} end
if redis.call('HGET', KEYS[1], 'code_digest') ~= ARGV[1] then
    wrong = redis.call('HINCRBY', KEYS[1], 'wrong', 1)
    if wrong >= 5 then return {'pairing_locked'} end
    return {'invalid_code'}
end
local original = tonumber(redis.call('HGET', KEYS[1], 'original_ttl'))
return {'ok', redis.call('HGET', KEYS[1], 'record'), math.min(remaining, original)}
`;

function invalid() {
    throw new StoreError("invalid_request");
}

function validPairingId(value) {
    return typeof value === "string" && /^[A-Za-z0-9_-]{22,64}$/.test(value);
}

function validProofKey(value) {
    return typeof value === "string" && /^[A-Za-z0-9_-]{43}$/.test(value);
}

function validApiUrl(value) {
    if (typeof value !== "string") return false;
    const match = /^http:\/\/((?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}):5051$/.exec(value);
    if (!match) return false;
    const octets = match[1].split(".").map(Number);
    return octets.every(octet => octet <= 255) && (
        octets[0] === 10 ||
        (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
        (octets[0] === 192 && octets[1] === 168)
    );
}

async function boundedJson(response) {
    const length = response.headers.get("content-length");
    if (length && (!/^\d+$/.test(length) || Number(length) > MAX_RESPONSE_BYTES)) {
        throw new Error("Invalid store response.");
    }
    if (!response.body || typeof response.body.getReader !== "function") {
        throw new Error("Invalid store response.");
    }
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    try {
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            size += value.byteLength;
            if (size > MAX_RESPONSE_BYTES) throw new Error("Invalid store response.");
            chunks.push(value);
        }
    } catch (error) {
        await reader.cancel().catch(() => {});
        throw error;
    } finally {
        reader.releaseLock();
    }
    const body = Buffer.concat(chunks, size);
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
}

function createStore({ url, token, secret, fetchImpl = globalThis.fetch, clock = Date.now,
    prefix = DEFAULT_PREFIX } = {}) {
    let endpoint;
    try {
        endpoint = new URL(url);
        if (endpoint.protocol !== "https:" || endpoint.username || endpoint.password ||
            endpoint.pathname !== "/" || endpoint.search || endpoint.hash ||
            typeof url !== "string" || !/^https:\/\/[^/?#]+\/?$/.test(url) ||
            typeof token !== "string" || !token || token.length > 8192 || /\s/.test(token) ||
            typeof secret !== "string" || secret.length < 32 || secret.length > 8192 ||
            typeof fetchImpl !== "function" || typeof clock !== "function" ||
            typeof prefix !== "string" || !/^[A-Za-z0-9:_-]{1,128}$/.test(prefix)) {
            throw new Error("Invalid store configuration.");
        }
    } catch {
        throw new StoreError("discovery_unavailable");
    }
    const activeKey = `${prefix}:active`;
    const digest = (domain, value) => createHmac("sha256", secret)
        .update(`${domain}\0${value}`).digest("hex");

    async function evaluate(script, keys, args) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), STORE_TIMEOUT_MS);
        try {
            const response = await fetchImpl(endpoint.origin, {
                method: "POST",
                headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
                body: JSON.stringify(["EVAL", script, keys.length, ...keys, ...args]),
                signal: controller.signal,
                redirect: "error",
                cache: "no-store",
            });
            if (!response || response.status !== 200 || response.redirected) {
                throw new Error("Store request failed.");
            }
            const payload = await boundedJson(response);
            if (!payload || Object.hasOwn(payload, "error") || !Array.isArray(payload.result)) {
                throw new Error("Invalid store response.");
            }
            const result = payload.result;
            if (typeof result[0] !== "string") throw new Error("Invalid store response.");
            if (result[0] !== "ok") {
                if (!Object.hasOwn(messages, result[0]) ||
                    !["pairing_unavailable", "invalid_code", "pairing_locked", "rate_limited"].includes(result[0]) ||
                    result.length !== 1) throw new Error("Invalid store response.");
                throw new StoreError(result[0]);
            }
            return result;
        } catch (error) {
            if (error instanceof StoreError) throw error;
            throw new StoreError("discovery_unavailable");
        } finally {
            controller.abort();
            clearTimeout(timer);
        }
    }

    function now() {
        const value = clock();
        if (!Number.isFinite(value) || value < 0) throw new StoreError("discovery_unavailable");
        return value;
    }

    function remaining(value) {
        if (!Number.isInteger(value) || value < 1 || value > 120) {
            throw new StoreError("discovery_unavailable");
        }
        return value;
    }

    return Object.freeze({
        async publish(input = {}) {
            if (!input || typeof input !== "object" || Array.isArray(input)) invalid();
            const { pairing_id, code, api_url, proof_key, expires_in } = input;
            if (!validPairingId(pairing_id) || typeof code !== "string" || !/^\d{6}$/.test(code) ||
                !validApiUrl(api_url) || !validProofKey(proof_key) ||
                !Number.isInteger(expires_in) || expires_in < 1 || expires_in > 120) invalid();
            const record = { pairing_id, api_url, proof_key, original_ttl: expires_in,
                expires_at_ms: now() + expires_in * 1000 };
            const result = await evaluate(PUBLISH, [activeKey], [pairing_id, digest("code", code),
                api_url, proof_key, JSON.stringify(record), expires_in]);
            if (result.length !== 2) throw new StoreError("discovery_unavailable");
            return { pairing_id, expires_in: remaining(result[1]) };
        },

        async revoke(pairing_id) {
            if (!validPairingId(pairing_id)) invalid();
            const result = await evaluate(REVOKE, [activeKey], [pairing_id]);
            if (result.length !== 2 || ![0, 1].includes(result[1])) {
                throw new StoreError("discovery_unavailable");
            }
            return { pairing_id, revoked: result[1] === 1 };
        },

        async resolve(input = {}) {
            if (!input || typeof input !== "object" || Array.isArray(input)) invalid();
            const { code, clientId } = input;
            if (typeof code !== "string" || !/^\d{6}$/.test(code) ||
                typeof clientId !== "string" || !/^[A-Za-z0-9_-]{32,128}$/.test(clientId)) invalid();
            const result = await evaluate(RESOLVE, [activeKey, `${prefix}:rate:${digest("client", clientId)}`],
                [digest("code", code)]);
            if (result.length !== 3 || typeof result[1] !== "string") {
                throw new StoreError("discovery_unavailable");
            }
            let record;
            try { record = JSON.parse(result[1]); } catch { throw new StoreError("discovery_unavailable"); }
            if (!record || !validPairingId(record.pairing_id) || !validApiUrl(record.api_url) ||
                !validProofKey(record.proof_key) || !Number.isInteger(record.original_ttl) ||
                record.original_ttl < 1 || record.original_ttl > 120 ||
                !Number.isFinite(record.expires_at_ms)) throw new StoreError("discovery_unavailable");
            const ttl = Math.min(remaining(result[2]), record.original_ttl,
                Math.ceil((record.expires_at_ms - now()) / 1000));
            if (ttl < 1) throw new StoreError("pairing_unavailable");
            return { api_url: record.api_url, pairing_id: record.pairing_id,
                proof_key: record.proof_key, expires_in: ttl };
        },
    });
}

module.exports = { createStore, StoreError };
