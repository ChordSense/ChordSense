function ipv4Octets(hostname) {
    if (!/^(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}$/.test(hostname)) return false;
    const octets = hostname.split(".").map(Number);
    if (octets.some(octet => octet > 255)) return false;
    return octets;
}

function isPrivateIPv4(hostname) {
    const octets = ipv4Octets(hostname);
    if (!octets) return false;
    return octets[0] === 10 ||
        (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
        (octets[0] === 192 && octets[1] === 168);
}

export function normalizeApiUrl(value) {
    // Validate the written address before URL parsing can normalize abbreviated,
    // hexadecimal, or octal IPv4 forms into a different host.
    const match = typeof value === "string" && /^https?:\/\/([a-z0-9.-]+)(?::([0-9]+))?\/?$/i.exec(value);
    const hostname = match ? match[1].toLowerCase() : "";
    const local = match?.[2] === "5051" &&
        (["chordsense.local", "localhost", "127.0.0.1"].includes(hostname) || isPrivateIPv4(hostname));
    if (!local) {
        throw new Error("The device connection must use ChordSense's local address or a private LAN IPv4 address on port 5051.");
    }
    return new URL(value).origin;
}

export function localRequestOptions(baseUrl) {
    const hostname = new URL(baseUrl).hostname;
    return { targetAddressSpace: ["localhost", "127.0.0.1"].includes(hostname) ? "loopback" : "local" };
}
