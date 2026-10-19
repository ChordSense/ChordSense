import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { webcrypto } from "node:crypto";
import { createCompanionSession } from "./api-client.js";

const { createHandler } = createRequire(import.meta.url)("../server/discovery.cjs");
const origin = "https://chordsense.example";
const secret = "integration-publisher-key-" + "s".repeat(32);
const emptyBootstrap = { discovery: true, api_url: null, context_id: null, pairing: null };
const storageKey = "chordsense.companion.pairing-session";
const pythonAdapter = String.raw`
import json, sys
import pairing
from pairing import PairingManager, PairingError
pairing.secrets.randbelow = lambda upper: 42
class Publisher:
    def __init__(self): self.endpoint, self.records = None, []
    def publish(self, **record): self.records.append(dict(record, api_url=self.endpoint))
    def delete_later(self, pairing_id): pass
publisher = Publisher()
manager = PairingManager(publisher=publisher)
for line in sys.stdin:
    command = json.loads(line)
    try:
        body, action = command.get("body", {}), command["action"]
        if action == "start":
            publisher.endpoint = body["api_url"]
            result = {"native": manager.start(), "publication": publisher.records[-1]}
        elif action == "proof": result = {"success": True, **manager.proof(body.get("pairing_id"), body.get("challenge"))}
        elif action == "claim": result = {"success": True, **manager.claim(body.get("code"), "https://chordsense.example", pairing_id=body.get("pairing_id"), proof_key=body.get("proof_key"))}
        elif action == "authenticate": result = {"success": manager.authenticate(body.get("token"), "https://chordsense.example")}
        else: raise ValueError("Unknown integration command")
        print(json.dumps({"id": command["id"], "result": result}), flush=True)
    except PairingError as error:
        print(json.dumps({"id": command["id"], "error": error.code}), flush=True)
`;

function startPython(t) {
    const child = spawn("python3", ["-u", "-c", pythonAdapter], {
        cwd: fileURLToPath(new URL("../../backend/", import.meta.url)), stdio: ["pipe", "pipe", "pipe"],
    });
    const exited = once(child, "exit");
    const lines = createInterface({ input: child.stdout });
    const pending = new Map();
    let nextId = 0;
    let stderr = "";
    child.stderr.on("data", chunk => { stderr = (stderr + chunk).slice(-4096); });
    lines.on("line", line => {
        const message = JSON.parse(line);
        const request = pending.get(message.id);
        pending.delete(message.id);
        if (message.error) request.reject(new Error(message.error));
        else request.resolve(message.result);
    });
    const fail = () => {
        for (const request of pending.values()) request.reject(new Error(`Python adapter stopped: ${stderr}`));
        pending.clear();
    };
    child.once("error", fail);
    child.once("exit", fail);
    t.after(async () => {
        child.stdin.end();
        const timer = setTimeout(() => child.kill("SIGKILL"), 1000);
        try { await exited; } finally { clearTimeout(timer); lines.close(); }
    });
    return (action, body = {}) => new Promise((resolve, reject) => {
        const id = ++nextId;
        pending.set(id, { resolve, reject });
        child.stdin.write(JSON.stringify({ id, action, body }) + "\n");
    });
}

test("real Python pairing, HTTPS directory context, and browser HMAC survive a changed LAN address", { timeout: 10000 }, async t => {
    const python = startPython(t);
    const cloudCalls = [], deviceCalls = [], navigations = [];
    const entries = new Map();
    const storage = { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key) };
    let active = null, cookie = "", endpoint = "http://10.136.68.97:5051";
    // One active record is enough for this contract test; Redis atomicity has separate tests.
    const store = {
        async publish(record) { active = structuredClone(record); },
        async revoke(id) { if (active?.pairing_id === id) active = null; },
        async resolve({ code }) { assert.equal(code, active.code); return { ...active }; },
    };
    const handler = createHandler({ store, env: { CHORDSENSE_DISCOVERY_TOKEN: secret, CHORDSENSE_COMPANION_ORIGIN: origin },
        template: readFileSync(new URL("./index.html", import.meta.url), "utf8") });
    async function cloud(action, { method = "GET", body, headers = {} } = {}) {
        headers = { "content-type": "application/json", origin, cookie, ...headers };
        cloudCalls.push({ action, method, body, headers });
        const response = { headers: {}, statusCode: 0, body: "",
            setHeader(name, value) { this.headers[name.toLowerCase()] = value; }, end(value) { this.body = value; } };
        await handler({ method, url: `/?action=${action}`, body, headers, socket: { remoteAddress: "192.0.2.1" } }, response);
        if (response.headers["set-cookie"]) cookie = response.headers["set-cookie"].split(";")[0];
        return new Response(response.body, { status: response.statusCode, headers: response.headers });
    }
    async function fetchImpl(url, options) {
        if (url === "/api/discovery/resolve") {
            assert.equal(options.headers.Authorization, undefined);
            return cloud("resolve", options);
        }
        const parsed = new URL(url);
        assert.equal(parsed.origin, endpoint);
        assert.equal(options.credentials, "omit");
        deviceCalls.push({ url, options });
        if (["/api/pairing/proof", "/api/pairing/claim"].includes(parsed.pathname)) {
            assert.equal(options.headers.Authorization, undefined);
            return Response.json(await python(parsed.pathname.endsWith("proof") ? "proof" : "claim", JSON.parse(options.body)));
        }
        const authenticated = await python("authenticate", { token: options.headers.Authorization?.slice(7) });
        assert.equal(authenticated.success, true);
        if (parsed.pathname === "/health") return Response.json({ success: true });
        assert.equal(parsed.pathname, "/api/uploads");
        return Response.json({ success: true, files: [{ name: "integration.wav", download_url: "/api/uploads/integration.wav" }] });
    }
    const makeSession = bootstrap => createCompanionSession({ bootstrap, storage, fetchImpl, cryptoImpl: webcrypto,
        navigate: path => navigations.push(path), notify: () => {} });
    const tokens = [];
    let browser = null;
    for (const [index, address] of ["http://10.136.68.97:5051", "http://10.136.137.225:5051"].entries()) {
        endpoint = address;
        const { native, publication } = await python("start", { api_url: endpoint });
        assert.equal(native.code, "000042");
        assert.equal(publication.api_url, endpoint);
        assert.equal(native.proof_key, undefined);
        const published = await cloud("publish", { method: "POST", body: publication, headers: { authorization: `Bearer ${secret}` } });
        assert.deepEqual(await published.json(), { success: true, pairing_id: publication.pairing_id });
        const discovery = browser || makeSession(emptyBootstrap);
        await discovery.pairDevice(native.code);
        assert.equal(navigations.at(-1), "/");
        assert.ok(!cookie.includes(native.code) && !cookie.includes(publication.proof_key));
        const page = await cloud("page");
        const bootstrap = JSON.parse((await page.text()).match(/<script id="device-bootstrap" type="application\/json">(.*?)<\/script>/s)[1]);
        assert.equal(bootstrap.api_url, endpoint);
        const connect = page.headers.get("content-security-policy").split(";").find(value => value.trim().startsWith("connect-src"));
        assert.equal(connect.trim(), `connect-src 'self' ${endpoint}`);
        if (index) assert.equal(JSON.parse(entries.get(storageKey)).token, tokens[0]);
        browser = makeSession(bootstrap);
        assert.equal(browser.getConnection().token, "", "old address token must not restore into the new device context");
        if (index) assert.equal(entries.size, 0);
        const firstCall = deviceCalls.length;
        await browser.completeDiscoveredPairing();
        assert.deepEqual(deviceCalls.slice(firstCall).map(call => new URL(call.url).pathname), ["/api/pairing/proof", "/api/pairing/claim"]);
        const saved = JSON.parse(entries.get(storageKey));
        assert.deepEqual(Object.keys(saved).sort(), ["baseUrl", "contextId", "expiresAt", "token"]);
        assert.equal(saved.token, browser.getConnection().token);
        assert.equal(saved.baseUrl, endpoint);
        assert.ok(!entries.get(storageKey).includes(publication.proof_key));
        tokens.push(saved.token);
        await browser.apiRequest("/health");
        const library = await browser.apiRequest("/api/uploads");
        assert.equal(browser.apiEndpoint(library.files[0].download_url), `${endpoint}/api/uploads/integration.wav`);
        assert.ok(deviceCalls.every(call => /^http:\/\/10\.136\.(?:68\.97|137\.225):5051\//.test(call.url)));
        for (const token of tokens) assert.ok(!JSON.stringify(cloudCalls).includes(token), "Pi bearer must never reach the directory");
    }
    assert.notEqual(tokens[0], tokens[1]);
});
