'use strict';

const { createCipheriv, createDecipheriv, createHash, createHmac, hkdfSync, randomBytes, timingSafeEqual } = require('node:crypto');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { createStore } = require('./discovery-store.cjs');

const COOKIE = '__Host-chordsense-device';
const SESSION_SECONDS = 8 * 60 * 60;
const ID = /^[A-Za-z0-9_-]{22,64}$/;
const KEY = /^[A-Za-z0-9_-]{43}$/;
const CODE = /^[0-9]{6}$/;

class RequestError extends Error {
    constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

function normalizeLanUrl(value) {
    const match = typeof value === 'string' && /^http:\/\/((?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}):5051$/.exec(value);
    const octets = match && match[1].split('.').map(Number);
    if (!octets || octets.some(n => n > 255) || !(octets[0] === 10 ||
        (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
        (octets[0] === 192 && octets[1] === 168))) {
        throw new RequestError(400, 'invalid_request', 'The device must publish its private Wi-Fi address on port 5051.');
    }
    return value;
}

function configuration(env) {
    const secret = env.CHORDSENSE_DISCOVERY_TOKEN || '';
    const origin = env.CHORDSENSE_COMPANION_ORIGIN || '';
    let parsed;
    try { parsed = new URL(origin); } catch {}
    if (!/^[A-Za-z0-9_-]{32,128}$/.test(secret) || !parsed || parsed.protocol !== 'https:' ||
        parsed.origin !== origin || parsed.username || parsed.password) {
        throw new RequestError(503, 'discovery_unavailable', 'ChordSense discovery is not configured.');
    }
    return { secret, origin };
}

function cookieKey(secret) {
    return Buffer.from(hkdfSync('sha256', secret, 'chordsense-discovery-v1', 'browser-context', 32));
}

function sealContext(context, secret, entropy = randomBytes) {
    const iv = entropy(12);
    const cipher = createCipheriv('aes-256-gcm', cookieKey(secret), iv);
    const encrypted = Buffer.concat([cipher.update(JSON.stringify(context), 'utf8'), cipher.final()]);
    return Buffer.concat([iv, cipher.getAuthTag(), encrypted]).toString('base64url');
}

function openContext(value, secret, now) {
    try {
        if (typeof value !== 'string' || value.length > 3500 || !/^[A-Za-z0-9_-]+$/.test(value)) return null;
        const data = Buffer.from(value, 'base64url');
        if (data.length < 29) return null;
        const cipher = createDecipheriv('aes-256-gcm', cookieKey(secret), data.subarray(0, 12));
        cipher.setAuthTag(data.subarray(12, 28));
        const result = JSON.parse(Buffer.concat([cipher.update(data.subarray(28)), cipher.final()]).toString('utf8'));
        normalizeLanUrl(result.api_url);
        if (result.version !== 1 || !ID.test(result.context_id) || !ID.test(result.pairing_id) ||
            !Number.isFinite(result.until) || result.until <= now || result.until > now + SESSION_SECONDS * 1000) return null;
        if (result.pending && (!CODE.test(result.pending.code) || !KEY.test(result.pending.proof_key) ||
            !Number.isFinite(result.pending.until) || result.pending.until > now + 120_000)) return null;
        return result;
    } catch { return null; }
}

function contextCookie(value, seconds) {
    return `${COOKIE}=${value}; Path=/; Max-Age=${seconds}; HttpOnly; Secure; SameSite=Strict`;
}

function readCookie(headers) {
    const candidates = String(headers.cookie || '').split(';').map(s => s.trim()).filter(s => s.startsWith(`${COOKIE}=`));
    return candidates.length === 1 ? candidates[0].slice(COOKIE.length + 1) : '';
}

function csp(endpoint = null) {
    return `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'${endpoint ? ` ${normalizeLanUrl(endpoint)}` : ''}; object-src 'none'; base-uri 'self'; frame-ancestors 'none'`;
}

function renderPage(template, bootstrap) {
    const json = JSON.stringify(bootstrap).replace(/[<>&\u2028\u2029]/g, character => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`);
    const node = /<script\b(?=[^>]*\bid=["']device-bootstrap["'])[^>]*>[\s\S]*?<\/script>/g;
    const matches = template.match(node);
    if (!matches || matches.length !== 1) throw new Error('The companion template must contain one discovery bootstrap.');
    return template.replace(node, () => `<script id="device-bootstrap" type="application/json">${json}</script>`);
}

async function readBody(req) {
    const contentType = req.headers['content-type'] || '';
    if (!/^application\/json(?:\s*;|$)/i.test(contentType)) throw new RequestError(415, 'invalid_request', 'Send a JSON object.');
    let body = req.body;
    if (body === undefined) {
        const chunks = [];
        let total = 0;
        for await (const chunk of req) {
            total += Buffer.byteLength(chunk);
            if (total > 2048) throw new RequestError(413, 'invalid_request', 'Pairing request is too large.');
            chunks.push(Buffer.from(chunk));
        }
        body = Buffer.concat(chunks).toString('utf8');
    }
    if (Buffer.isBuffer(body)) body = body.toString('utf8');
    try {
        if (typeof body === 'string') {
            if (Buffer.byteLength(body) > 2048) throw new Error();
            body = JSON.parse(body);
        }
        if (!body || typeof body !== 'object' || Array.isArray(body) || Buffer.byteLength(JSON.stringify(body)) > 2048) throw new Error();
    } catch { throw new RequestError(400, 'invalid_request', 'Send a bounded JSON object.'); }
    return body;
}

function json(res, status, payload) {
    res.statusCode = status;
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.end(JSON.stringify(payload));
}

function validatePublication(body) {
    if (typeof body.pairing_id !== 'string' || typeof body.proof_key !== 'string' || typeof body.code !== 'string' ||
        !ID.test(body.pairing_id) || !KEY.test(body.proof_key) || !CODE.test(body.code) ||
        !Number.isInteger(body.expires_in) || body.expires_in < 1 || body.expires_in > 120) {
        throw new RequestError(400, 'invalid_request', 'Invalid pairing window.');
    }
    return { pairing_id: body.pairing_id, proof_key: body.proof_key, code: body.code,
        api_url: normalizeLanUrl(body.api_url), expires_in: body.expires_in };
}

function createHandler({ env = process.env, store: suppliedStore, now = Date.now, entropy = randomBytes, template } = {}) {
    let store = suppliedStore;
    const getStore = secret => {
        if (!store) store = createStore({ url: env.UPSTASH_REDIS_REST_URL || env.KV_REST_API_URL,
            token: env.UPSTASH_REDIS_REST_TOKEN || env.KV_REST_API_TOKEN, secret });
        return store;
    };
    return async function handler(req, res) {
        res.setHeader('Cache-Control', 'private, no-store');
        res.setHeader('X-Content-Type-Options', 'nosniff');
        res.setHeader('Referrer-Policy', 'no-referrer');
        res.setHeader('Vary', 'Cookie');
        try {
            const { secret, origin } = configuration(env);
            const action = new URL(req.url, origin).searchParams.get('action');
            if (action === 'publish') {
                const supplied = createHash('sha256').update(String(req.headers.authorization || '')).digest();
                const expected = createHash('sha256').update(`Bearer ${secret}`).digest();
                if (!timingSafeEqual(supplied, expected)) throw new RequestError(401, 'unauthorized', 'Device publication is not authorized.');
                if (!['POST', 'DELETE'].includes(req.method)) throw new RequestError(405, 'invalid_request', 'Unsupported discovery method.');
                const body = await readBody(req);
                if (req.method === 'POST') {
                    const record = validatePublication(body);
                    await getStore(secret).publish(record);
                    return json(res, 200, { success: true, pairing_id: record.pairing_id });
                }
                if (!ID.test(body.pairing_id)) throw new RequestError(400, 'invalid_request', 'Invalid pairing window.');
                await getStore(secret).revoke(body.pairing_id);
                return json(res, 200, { success: true, pairing_id: body.pairing_id });
            }
            if (action === 'resolve') {
                if (req.method !== 'POST') throw new RequestError(405, 'invalid_request', 'Submit the pairing code with POST.');
                if (req.headers.origin !== origin) throw new RequestError(403, 'origin_denied', 'Open the configured ChordSense website to pair.');
                const body = await readBody(req);
                if (typeof body.code !== 'string' || !CODE.test(body.code)) throw new RequestError(400, 'invalid_code_format', 'Enter the six-digit code shown on ChordSense.');
                const ip = String(req.headers['x-real-ip'] || req.socket?.remoteAddress || 'unknown').slice(0, 128);
                const clientId = createHmac('sha256', secret).update(`discovery-client\0${ip}`).digest('hex');
                const record = await getStore(secret).resolve({ code: body.code, clientId });
                normalizeLanUrl(record.api_url);
                if (!ID.test(record.pairing_id) || !KEY.test(record.proof_key) ||
                    !Number.isFinite(record.expires_in) || record.expires_in <= 0 || record.expires_in > 120) throw new Error('Invalid directory result.');
                const time = now();
                // One publisher represents this device. Keep its address context
                // stable across pairing windows so another tab does not invalidate
                // an existing session on reload. A changed IP or credential gives
                // a different context; browser endpoint matching remains strict.
                const contextId = createHmac('sha256', cookieKey(secret)).update(`device-context\0${record.api_url}`).digest('base64url');
                const context = { version: 1, api_url: record.api_url, pairing_id: record.pairing_id,
                    context_id: contextId, until: time + SESSION_SECONDS * 1000,
                    pending: { code: body.code, proof_key: record.proof_key, until: time + Math.floor(record.expires_in * 1000) } };
                res.setHeader('Set-Cookie', contextCookie(sealContext(context, secret, entropy), SESSION_SECONDS));
                return json(res, 200, { success: true, navigate_to: '/' });
            }
            if (action === 'page' && ['GET', 'HEAD'].includes(req.method)) {
                const time = now();
                const context = openContext(readCookie(req.headers), secret, time);
                let pairing = null;
                if (context?.pending && context.pending.until > time) {
                    pairing = { code: context.pending.code, pairing_id: context.pairing_id,
                        proof_key: context.pending.proof_key, expires_in: Math.min(120, (context.pending.until - time) / 1000) };
                }
                const bootstrap = { discovery: true, api_url: context?.api_url || null, context_id: context?.context_id || null, pairing };
                res.setHeader('Content-Security-Policy', csp(bootstrap.api_url));
                res.setHeader('Content-Type', 'text/html; charset=utf-8');
                if (req.method !== 'HEAD') {
                    if (context?.pending) {
                        delete context.pending;
                        res.setHeader('Set-Cookie', contextCookie(sealContext(context, secret, entropy), Math.max(1, Math.floor((context.until - time) / 1000))));
                    } else if (!context && readCookie(req.headers)) res.setHeader('Set-Cookie', contextCookie('', 0));
                }
                res.statusCode = 200;
                return res.end(req.method === 'HEAD' ? '' : renderPage(template || readFileSync(join(__dirname, 'index.html'), 'utf8'), bootstrap));
            }
            throw new RequestError(404, 'not_found', 'Discovery route not found.');
        } catch (error) {
            const known = error instanceof RequestError || (Number.isInteger(error.status) &&
                ['invalid_code', 'pairing_locked', 'pairing_unavailable', 'rate_limited', 'discovery_unavailable'].includes(error.code));
            return json(res, known ? error.status : 503, { success: false,
                error_code: known ? error.code : 'discovery_unavailable',
                error: known ? error.message : 'ChordSense discovery is temporarily unavailable. Try again.' });
        }
    };
}

module.exports = createHandler();
Object.assign(module.exports, { createHandler, normalizeLanUrl, sealContext, openContext, renderPage, csp, COOKIE });
