import { cp, copyFile, mkdir, mkdtemp, readdir, rename, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, extname, join } from "node:path";

const project = dirname(dirname(fileURLToPath(import.meta.url)));
const output = join(project, "dist");
const vercelDirectory = join(dirname(project), ".vercel");
const deploymentOutput = join(vercelDirectory, "output");
// Discovery publishes the Wi-Fi address at pairing time, never at build time.
if (process.env.CHORDSENSE_API_URL?.trim()) {
    throw new Error("Automatic discovery does not use CHORDSENSE_API_URL. Remove that build variable.");
}
const publicExtensions = new Set([".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2"]);
const privateDirectories = new Set(["test", "tests", "__tests__", "fixtures", "__fixtures__", "node_modules", "src-tauri"]);

async function copyPublicAssets(source, destination) {
    await mkdir(destination, { recursive: true });
    for (const entry of await readdir(source, { withFileTypes: true })) {
        if (entry.name.startsWith(".") || privateDirectories.has(entry.name)) continue;
        const sourcePath = join(source, entry.name);
        const destinationPath = join(destination, entry.name);
        if (entry.isDirectory()) {
            await copyPublicAssets(sourcePath, destinationPath);
        } else if (entry.isFile() && publicExtensions.has(extname(entry.name)) && !/\.(?:test|spec)\./.test(entry.name)) {
            await copyFile(sourcePath, destinationPath);
        }
    }
}

const securityHeaders = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
};
const deploymentConfig = {
    version: 3,
    routes: [
        // Dynamic documents set their own exact CSP after validating the device
        // address. A second static connect-src policy would block that address.
        { src: "^/(?:index\\.html|upload/?)?$", dest: "/api/discovery?action=page" },
        { src: "^/api/discovery/(publish|resolve)$", dest: "/api/discovery?action=$1" },
        { src: "^/.*$", headers: securityHeaders, continue: true },
        { src: "^/config\\.js$", headers: { "Cache-Control": "no-store" }, continue: true },
        { handle: "filesystem" },
    ],
};

await mkdir(vercelDirectory, { recursive: true });
const staging = await mkdtemp(join(vercelDirectory, "companion-build-"));
try {
    const preview = join(staging, "dist");
    const deployment = join(staging, "output");
    await copyPublicAssets(join(project, "src"), preview);
    await cp(preview, join(deployment, "static"), { recursive: true });
    const server = join(deployment, "functions", "api", "discovery.func");
    await mkdir(server, { recursive: true });
    await copyFile(join(project, "server", "discovery.cjs"), join(server, "index.js"));
    await copyFile(join(project, "server", "discovery-store.cjs"), join(server, "discovery-store.cjs"));
    await copyFile(join(preview, "index.html"), join(server, "index.html"));
    await writeFile(join(server, ".vc-config.json"), `${JSON.stringify({ runtime: "nodejs22.x", handler: "index.js", launcherType: "Nodejs", maxDuration: 10 }, null, 2)}\n`);
    await writeFile(join(deployment, "config.json"), `${JSON.stringify(deploymentConfig, null, 2)}\n`);
    await rm(output, { recursive: true, force: true });
    await rename(preview, output);
    await rm(deploymentOutput, { recursive: true, force: true });
    await rename(deployment, deploymentOutput);
} finally {
    await rm(staging, { recursive: true, force: true });
}
console.log("Built companion preview in web/dist/ and Vercel deployment in .vercel/output/.");
