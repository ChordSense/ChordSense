import { cp, mkdir, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { DEFAULT_API_URL, normalizeApiUrl } from "../src/endpoint.js";

const project = dirname(dirname(fileURLToPath(import.meta.url)));
const output = join(project, "dist");
const configuredUrl = normalizeApiUrl(process.env.CHORDSENSE_API_URL?.trim() || DEFAULT_API_URL);
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(join(project, "src"), output, {
    recursive: true,
    filter: source => !source.endsWith(".test.js"),
});
await writeFile(join(output, "config.js"), `export const defaultApiUrl = ${JSON.stringify(configuredUrl.replace(/\/+$/, ""))};\n`);
console.log("Built ChordSense upload and library website in web/dist/");
