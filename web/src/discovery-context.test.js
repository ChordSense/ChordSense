import test from "node:test";
import assert from "node:assert/strict";
import { createHmac, webcrypto } from "node:crypto";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { createCompanionSession } from "./api-client.js";

const { createHandler } = createRequire(import.meta.url)("../server/discovery.cjs");
const origin = "https://chordsense.example";
const endpoint = "http://10.20.30.40:5051";
const storageKey = "chordsense.companion.pairing-session";
const seconds = 2_000_000_000;
const template = readFileSync(new URL("./index.html", import.meta.url), "utf8");

function tabStorage() {
    const values = new Map();
    return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}

function directory(secret = "s".repeat(43)) {
    let activeRecord;
    let cookie = "";
    const handler = createHandler({ env: { CHORDSENSE_DISCOVERY_TOKEN: secret, CHORDSENSE_COMPANION_ORIGIN: origin },
        now: () => seconds * 1000, template,
        store: { async resolve({ code }) { assert.equal(code, activeRecord.code); return { ...activeRecord }; } } });
    async function request(action, method = "GET", body) {
        const response = { headers: {}, setHeader(name, value) { this.headers[name.toLowerCase()] = value; }, end(value) { this.body = value; } };
        await handler({ url: `/?action=${action}`, method, body, headers: { origin, cookie, "content-type": "application/json" } }, response);
        assert.equal(response.statusCode, 200);
        assert.equal(response.headers["cache-control"], "private, no-store");
        if (response.headers["set-cookie"]) cookie = response.headers["set-cookie"].split(";")[0];
        return response;
    }
    async function page() {
        const response = await request("page");
        const match = response.body.match(/<script id="device-bootstrap" type="application\/json">(.*?)<\/script>/s);
        assert.ok(match);
        const bootstrap = JSON.parse(match[1]);
        const connect = response.headers["content-security-policy"].split(";").find(value => value.trim().startsWith("connect-src"));
        assert.equal(connect.trim(), `connect-src 'self' ${bootstrap.api_url}`);
        return bootstrap;
    }
    return {
        async resolve(record) {
            activeRecord = record;
            const response = await request("resolve", "POST", { code: record.code });
            assert.deepEqual(JSON.parse(response.body), { success: true, navigate_to: "/" });
            return page();
        },
        page,
    };
}

function browser(bootstrap, token, storage = tabStorage()) {
    const calls = [];
    const session = createCompanionSession({ bootstrap, storage, cryptoImpl: webcrypto, now: () => seconds, notify: () => {},
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            assert.ok(url.startsWith(`${bootstrap.api_url}/`));
            if (url.endsWith("/api/pairing/proof")) {
                const body = JSON.parse(options.body);
                assert.equal(body.pairing_id, bootstrap.pairing.pairing_id);
                const proof = createHmac("sha256", bootstrap.pairing.proof_key)
                    .update(`chordsense-pairing-v1\n${body.pairing_id}\n${body.challenge}`).digest("hex");
                return Response.json({ success: true, pairing_id: body.pairing_id, proof });
            }
            if (url.endsWith("/api/pairing/claim")) {
                assert.deepEqual(JSON.parse(options.body), { code: bootstrap.pairing.code, pairing_id: bootstrap.pairing.pairing_id, proof_key: bootstrap.pairing.proof_key });
                return Response.json({ success: true, token, token_type: "Bearer", expires_in: 28800 });
            }
            assert.equal(options.headers.Authorization, `Bearer ${token}`);
            return Response.json({ success: true });
        } });
    return { session, storage, calls };
}

const record = (letter, api_url = endpoint) => ({ api_url, code: letter === "A" ? "000001" : "000002",
    pairing_id: letter.repeat(22), proof_key: letter.repeat(43), expires_in: 120 });

test("tab A restores its own token after tab B pairs at the same endpoint, but rejects a changed endpoint", async () => {
    const cloud = directory();
    const firstBootstrap = await cloud.resolve(record("A"));
    const first = browser(firstBootstrap, "tab-a-token");
    await first.session.completeDiscoveredPairing();
    const savedA = first.storage.getItem(storageKey);

    const secondBootstrap = await cloud.resolve(record("B"));
    const second = browser(secondBootstrap, "tab-b-token");
    await second.session.completeDiscoveredPairing();
    assert.equal(secondBootstrap.api_url, firstBootstrap.api_url);
    assert.equal(secondBootstrap.context_id, firstBootstrap.context_id);
    assert.notEqual(secondBootstrap.pairing.pairing_id, firstBootstrap.pairing.pairing_id);
    assert.notEqual(secondBootstrap.pairing.proof_key, firstBootstrap.pairing.proof_key);
    assert.equal(first.storage.getItem(storageKey), savedA);

    const afterCookieConsumption = await cloud.page();
    assert.equal(afterCookieConsumption.pairing, null);
    assert.equal(afterCookieConsumption.context_id, firstBootstrap.context_id);
    const reloadedA = browser(afterCookieConsumption, "tab-a-token", first.storage);
    assert.equal(reloadedA.session.getConnection().token, "tab-a-token");
    assert.equal(reloadedA.storage.getItem(storageKey), savedA);
    assert.equal(reloadedA.session.hasPendingPairing(), false);
    await reloadedA.session.apiRequest("/health");
    assert.equal(reloadedA.calls.length, 1);
    assert.equal(reloadedA.calls[0].options.headers.Authorization, "Bearer tab-a-token");
    assert.equal(second.session.getConnection().token, "tab-b-token");

    const movedBootstrap = await cloud.resolve(record("C", "http://10.20.30.41:5051"));
    assert.notEqual(movedBootstrap.context_id, firstBootstrap.context_id);
    const movedReloadA = browser(movedBootstrap, "unused", first.storage);
    assert.equal(movedReloadA.session.getConnection().token, "");
    assert.equal(first.storage.values.size, 0);
    await assert.rejects(movedReloadA.session.apiRequest("/health"), /six-digit code/);
    assert.equal(movedReloadA.calls.length, 0, "the previous bearer must never reach the new address");
});

test("a different configured publisher identity cannot restore the old endpoint context", async () => {
    const originalCloud = directory("s".repeat(43));
    const original = browser(await originalCloud.resolve(record("A")), "old-identity-token");
    await original.session.completeDiscoveredPairing();
    const replacementCloud = directory("r".repeat(43));
    const replacement = await replacementCloud.resolve(record("B"));
    assert.equal(replacement.api_url, original.session.getConnection().baseUrl);
    assert.notEqual(replacement.context_id, original.session.getConnection().contextId);
    const reloaded = browser(replacement, "unused", original.storage);
    assert.equal(reloaded.session.getConnection().token, "");
    await assert.rejects(reloaded.session.apiRequest("/health"), /six-digit code/);
    assert.equal(reloaded.calls.length, 0);
});
