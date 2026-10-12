import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { DEFAULT_API_URL } from "./endpoint.js";

const project = fileURLToPath(new URL("..", import.meta.url));
const buildScript = join(project, "scripts", "build.mjs");
const output = join(project, "dist");
const build = endpoint => execFileSync(process.execPath, [buildScript], {
    env: { ...process.env, CHORDSENSE_API_URL: endpoint }, stdio: "pipe",
});

test("static build keeps the LAN hostname default and emits a configured private IPv4 without test files", () => {
    build("");
    assert.equal(readFileSync(join(output, "config.js"), "utf8"), `export const defaultApiUrl = "${DEFAULT_API_URL}";\n`);
    build("http://10.136.137.225:5051/");
    const configured = 'export const defaultApiUrl = "http://10.136.137.225:5051";\n';
    assert.equal(readFileSync(join(output, "config.js"), "utf8"), configured);
    const published = readdirSync(output);
    assert.ok(published.includes("index.html"));
    assert.ok(published.includes("endpoint.js"));
    assert.ok(!published.some(filename => filename.endsWith(".test.js")));
    assert.throws(() => build("http://8.8.8.8:5051"), /local address/);
    assert.equal(readFileSync(join(output, "config.js"), "utf8"), configured);
});
