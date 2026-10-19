import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { createHandler, openContext, normalizeLanUrl, renderPage, COOKIE } = require('../server/discovery.cjs');

const origin = 'https://chordsense.example';
const secret = 's'.repeat(43);
const pairingId = 'w'.repeat(22);
const proofKey = 'p'.repeat(43);
const apiUrl = 'http://10.23.45.67:5051';
const template = '<!doctype html><html><script id="device-bootstrap" type="application/json">{}</script><script src="/upload.js" type="module"></script></html>';
const baseRecord = { api_url: apiUrl, pairing_id: pairingId, proof_key: proofKey, expires_in: 90 };
const env = { CHORDSENSE_DISCOVERY_TOKEN: secret, CHORDSENSE_COMPANION_ORIGIN: origin };

function fixture(overrides = {}) {
    let time = 1_800_000_000_000;
    const calls = [];
    const store = {
        async publish(value) { calls.push(['publish', value]); },
        async revoke(value) { calls.push(['revoke', value]); },
        async resolve(value) { calls.push(['resolve', value]); return { ...baseRecord }; },
        ...overrides,
    };
    const handler = createHandler({ env, store, template, now: () => time });
    return {
        calls, advance(milliseconds) { time += milliseconds; },
        async request(action, { method = 'GET', body, headers = {} } = {}) {
            const request = { method, url: `/?action=${action}`, body,
                headers: { 'content-type': 'application/json', ...headers }, socket: { remoteAddress: '192.0.2.1' } };
            const response = { headers: {}, statusCode: 0, body: '',
                setHeader(name, value) { this.headers[name.toLowerCase()] = value; }, end(value) { this.body = value; } };
            await handler(request, response);
            response.json = () => JSON.parse(response.body);
            return response;
        },
    };
}
const browserHeaders = { origin };
const publisherHeaders = { authorization: `Bearer ${secret}` };
const cookieHeader = response => response.headers['set-cookie'].split(';')[0];
const bootstrap = response => JSON.parse(response.body.match(/<script id="device-bootstrap" type="application\/json">(.*?)<\/script>/)[1]);

test('device publication needs a private bearer and a canonical Wi-Fi address', async () => {
    const f = fixture();
    const record = { ...baseRecord, code: '012345' };
    const unauthorized = await f.request('publish', { method: 'POST', body: record });
    assert.equal(unauthorized.statusCode, 401);
    assert.equal(f.calls.length, 0);
    for (const api_url of ['http://100.71.233.14:5051', 'http://8.8.8.8:5051', 'http://010.1.2.3:5051', 'http://10.1.2.3:5051/path']) {
        const response = await f.request('publish', { method: 'POST', headers: publisherHeaders, body: { ...record, api_url } });
        assert.equal(response.statusCode, 400);
    }
    const numeric = await f.request('publish', { method: 'POST', headers: publisherHeaders, body: { ...record, code: 123456 } });
    assert.equal(numeric.statusCode, 400);
    assert.equal(f.calls.length, 0);
    const response = await f.request('publish', { method: 'POST', headers: publisherHeaders, body: record });
    assert.equal(response.statusCode, 200);
    assert.deepEqual(response.json(), { success: true, pairing_id: pairingId });
    assert.deepEqual(f.calls, [['publish', record]]);
});

test('generation-scoped revocation is authenticated and never accepts a caller-supplied URL', async () => {
    const f = fixture();
    const response = await f.request('publish', { method: 'DELETE', headers: publisherHeaders, body: { pairing_id: pairingId } });
    assert.equal(response.statusCode, 200);
    assert.deepEqual(f.calls, [['revoke', pairingId]]);
});

test('resolve keeps code and proof out of URLs and JSON, using an encrypted HttpOnly context', async () => {
    const f = fixture();
    const response = await f.request('resolve', { method: 'POST', headers: browserHeaders, body: { code: '012345' } });
    assert.equal(response.statusCode, 200);
    assert.deepEqual(response.json(), { success: true, navigate_to: '/' });
    assert.ok(!response.body.includes('012345'));
    assert.ok(!response.body.includes(proofKey));
    const cookie = response.headers['set-cookie'];
    for (const attribute of ['HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/']) assert.ok(cookie.includes(attribute));
    assert.ok(!cookie.includes('012345'));
    assert.ok(!cookie.includes(proofKey));
    assert.equal(f.calls[0][1].code, '012345');
    assert.match(f.calls[0][1].clientId, /^[a-f0-9]{64}$/);
    const decoded = openContext(cookieHeader(response).slice(COOKIE.length + 1), secret, 1_800_000_000_000);
    assert.equal(decoded.api_url, apiUrl);
    assert.equal(decoded.pending.code, '012345');
});

test('page rendering sets exact runtime CSP and drops pending secrets from the subsequent cookie', async () => {
    const f = fixture();
    const discovery = await f.request('resolve', { method: 'POST', headers: browserHeaders, body: { code: '012345' } });
    const page = await f.request('page', { headers: { cookie: cookieHeader(discovery) } });
    assert.equal(page.statusCode, 200);
    assert.equal(page.headers['cache-control'], 'private, no-store');
    assert.equal(page.headers.vary, 'Cookie');
    const connect = page.headers['content-security-policy'].split(';').find(x => x.trim().startsWith('connect-src')).trim();
    assert.equal(connect, `connect-src 'self' ${apiUrl}`);
    const first = bootstrap(page);
    assert.equal(first.api_url, apiUrl);
    assert.equal(first.pairing.code, '012345');
    assert.equal(first.pairing.proof_key, proofKey);
    const reload = await f.request('page', { headers: { cookie: cookieHeader(page) } });
    assert.equal(bootstrap(reload).pairing, null);
    assert.equal(bootstrap(reload).api_url, apiUrl);
    assert.equal(bootstrap(reload).context_id, first.context_id);
    assert.ok(!reload.body.includes('012345'));
    assert.ok(!reload.body.includes(proofKey));
    f.advance(8 * 60 * 60 * 1000 + 1);
    assert.equal(bootstrap(await f.request('page', { headers: { cookie: cookieHeader(page) } })).api_url, null);
});

test('initial and tampered contexts never grant permission to contact a LAN device', async () => {
    const f = fixture();
    for (const cookie of ['', `${COOKIE}=tampered`]) {
        const response = await f.request('page', { headers: { cookie } });
        assert.deepEqual(bootstrap(response), { discovery: true, api_url: null, context_id: null, pairing: null });
        assert.match(response.headers['content-security-policy'], /connect-src 'self';/);
    }
});

test('expired pending claims are not rendered and HEAD does not consume a pending claim', async () => {
    const f = fixture();
    const discovery = await f.request('resolve', { method: 'POST', headers: browserHeaders, body: { code: '012345' } });
    const headers = { cookie: cookieHeader(discovery) };
    const head = await f.request('page', { method: 'HEAD', headers });
    assert.equal(head.body, '');
    assert.equal(head.headers['set-cookie'], undefined);
    f.advance(91_000);
    assert.equal(bootstrap(await f.request('page', { headers })).pairing, null);
});

test('cross-origin, oversized, malformed, and non-POST lookups never reach storage', async () => {
    const f = fixture();
    const requests = [
        { method: 'POST', headers: { origin: 'https://foreign.example' }, body: { code: '012345' } },
        { method: 'GET', headers: browserHeaders },
        { method: 'POST', headers: browserHeaders, body: { code: 123456 } },
        { method: 'POST', headers: browserHeaders, body: { code: '12345' } },
        { method: 'POST', headers: browserHeaders, body: { code: '012345', extra: 'x'.repeat(3000) } },
    ];
    for (const request of requests) assert.ok((await f.request('resolve', request)).statusCode >= 400);
    assert.equal(f.calls.length, 0);
});

test('directory errors fail closed without reflecting credentials or contacting a guessed IP', async () => {
    const f = fixture({ async resolve() { throw new Error('secret credentials must not appear'); } });
    const response = await f.request('resolve', { method: 'POST', headers: browserHeaders, body: { code: '012345' } });
    assert.equal(response.statusCode, 503);
    assert.equal(response.json().error_code, 'discovery_unavailable');
    assert.ok(!response.body.includes('secret credentials'));
    assert.equal(response.headers['set-cookie'], undefined);
});

test('only private canonical IPv4 publishers are accepted and template JSON cannot create executable HTML', () => {
    assert.equal(normalizeLanUrl(apiUrl), apiUrl);
    for (const value of ['http://127.0.0.1:5051', 'https://10.1.2.3:5051', 'http://chordsense.local:5051', 'http://100.64.0.1:5051']) {
        assert.throws(() => normalizeLanUrl(value));
    }
    const html = renderPage(template, { value: '</script><script>alert(1)</script>' });
    assert.ok(!html.includes('<script>alert'));
    assert.ok(html.includes('\\u003c'));
});
