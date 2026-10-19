import test from "node:test";
import assert from "node:assert/strict";
import { localRequestOptions, normalizeApiUrl } from "./endpoint.js";

test("the companion requires an explicit validated address and has no discovery fallback", () => {
    assert.throws(() => normalizeApiUrl(), /local address/);
    assert.throws(() => normalizeApiUrl(null), /local address/);
    for (const hostname of ["chordsense.local", "localhost", "127.0.0.1", "10.0.0.1", "10.255.255.254", "172.16.0.1", "172.31.255.254", "192.168.0.1", "192.168.255.254"]) {
        for (const protocol of ["http", "https"]) {
            const address = `${protocol}://${hostname}:5051`;
            assert.equal(normalizeApiUrl(`${address}/`), address);
            assert.deepEqual(localRequestOptions(address), { targetAddressSpace: ["localhost", "127.0.0.1"].includes(hostname) ? "loopback" : "local" });
        }
    }
});

test("admin Tailscale addresses cannot become the companion connection", () => {
    for (const hostname of ["100.64.0.0", "100.64.0.1", "100.71.233.14", "100.127.255.255"]) {
        for (const protocol of ["http", "https"]) assert.throws(() => normalizeApiUrl(`${protocol}://${hostname}:5051`), /private LAN IPv4 address on port 5051/);
    }
    for (const address of [
        "https://chordsense.tail1234.ts.net",
        "https://chordsense.tail1234.ts.net:443/",
        "https://my-device.yak-bebop.ts.net",
        "http://chordsense.tail1234.ts.net:5051",
        "https://chordsense.tail1234.ts.net:5051",
        "https://chordsense.ts.net",
        "https://chordsense.tail1234.ts.net.attacker.example",
        "https://chordsense.tail1234.ts.net/path",
        "https://user:secret@chordsense.tail1234.ts.net",
        "https://chordsense.tail1234.ts.net?token=secret",
        "https://chordsense.tail1234.ts.net#fragment",
        "https://-device.tail1234.ts.net",
        "https://device.tail1234-.ts.net",
    ]) assert.throws(() => normalizeApiUrl(address), /local address/);
});

test("LAN configuration rejects public, ambiguous, and non-device URLs", () => {
    for (const hostname of ["8.8.8.8", "100.63.255.255", "100.128.0.1", "172.15.255.255", "172.32.0.1", "192.167.1.1", "192.169.1.1", "169.254.1.1", "0.0.0.0", "10.0.0.256", "10.0.1", "010.0.0.1", "0x0a.0.0.1", "167772161", "127.1"]) {
        assert.throws(() => normalizeApiUrl(`http://${hostname}:5051`), /local address/);
    }
    for (const address of ["http://10.1.2.3", "http://10.1.2.3:5052", "https://chordsense.local", "http://user:secret@10.1.2.3:5051", "http://10.1.2.3:5051/path", "http://10.1.2.3:5051?token=secret", "http://10.1.2.3:5051#fragment"]) {
        assert.throws(() => normalizeApiUrl(address), /local address/);
    }
});
