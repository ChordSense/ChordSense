# ChordSense companion website

This companion website uploads, lists, downloads and deletes songs on one ChordSense device. Vercel serves static assets plus a small address-discovery function; audio transfers directly across Wi-Fi. Play Along, Record and live feedback remain in the Pi's attached-screen Tauri app.

## Connect with a short code

Join the same reachable Wi-Fi/LAN as the Pi. Every browser can attempt pairing. Starting **Pair uploads** publishes the Pi's current Wi-Fi address to the HTTPS discovery service for that short code. There is no build-time IP, `.local` dependency, URL entry or network scan. Tailscale is for administrative SSH only; users need no Tailscale client.

1. Tap **Pair uploads** on the updated Pi screen; a code is published and shown automatically.
2. Open the displayed website, enter all six digits and select **Pair device**.
3. Allow local-network access if your browser asks and manage the song library.

Each code lasts at most 120 seconds and the Pi consumes it once. Discovery locks the window after five wrong codes and limits each client IP to 30 lookups per 120 seconds. A correct lookup can be retried while the window remains open. A successful Pi claim creates an eight-hour upload-only token stored in the current tab's `sessionStorage`, bound to its API address and discovery context. Codes and proof keys are not placed in URLs or `sessionStorage`. Disconnect revokes that tab's session; the Pi's **Disconnect all** revokes every session. API restarts also require pairing again.

After a Wi-Fi IP change, show a fresh code and pair again. A new Pair uploads action publishes the current address, so no redeployment is required. Existing tokens do not silently migrate to another address.

Browser brands and versions are not filtered. HTTPS-to-local-HTTP support varies, and a failed request shows connection or permission guidance after the browser attempts it. See [observed browser test results](../docs/browser-compatibility.md). Guest Wi-Fi/client isolation can prevent access even on the same Wi-Fi name. Discovery uses HTTPS, but LAN HTTP does not encrypt claims, tokens or audio. Allow browser local-network access if asked. Device transfers do not pass through Vercel. [Local Network Access](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Local_network_access)

Choose or drop a WAV, MP3, OGG, FLAC or M4A file comfortably below the 95 MiB request limit. Upload progress shows transfer to the device. Refresh is on demand; there is no background polling. Changing or disconnecting a session is blocked while a transfer is active.

## Local development

Node.js is needed to build/test; the website and Node.js 22 function use built-ins without npm dependencies.

```sh
npm --prefix web run dev       # static UI preview at http://127.0.0.1:5173
npm --prefix web test
npm --prefix web run build     # web/dist and .vercel/output
```

The static preview does not run the HTTPS discovery service. Unit tests mock the directory and Pi; real pairing needs the configured HTTPS deployment and updated backend. Legacy backend pairing without directory settings is retained for isolated development/tests, not automatic production discovery.

## Vercel deployment

Activation is currently pending the user's Upstash Marketplace terms acceptance and free-store provisioning. No production discovery deployment or Pi update has been performed for this feature. Follow [the complete setup steps](../docs/web-device-architecture.md#vercel-setup-from-the-local-working-directory); historical browser results are not verification of this flow.

From the repository root, run `npx vercel@latest login` and `npx vercel@latest link`; create/select the upload project and choose `./`. Leave **Root Directory blank**, Framework Other, build `node web/scripts/build.mjs`, and clear any Output Directory override. Remove the obsolete `CHORDSENSE_API_URL` build/environment value; the build rejects it.

Connect a free Upstash Redis database with automatic upgrades disabled. In the project's **Production runtime environment**, configure:

| Setting | Value |
| --- | --- |
| `UPSTASH_REDIS_REST_URL` | Database HTTPS REST origin |
| `UPSTASH_REDIS_REST_TOKEN` | Database write-capable REST token |
| `CHORDSENSE_DISCOVERY_TOKEN` | Private token generated from 32 random bytes, base64url encoded (43 characters) |
| `CHORDSENSE_COMPANION_ORIGIN` | Exact HTTPS production origin, such as `https://chordsense-phi.vercel.app` |

`KV_REST_API_URL`/`KV_REST_API_TOKEN` are supported aliases if the integration supplies those names. Keep credentials and the publication token server-side, outside frontend/build variables. The Pi receives only the discovery URL and matching publication token, never Redis credentials.

Build/test and deploy the prepared output:

```sh
npm --prefix web test
npm --prefix web run build
npx vercel@latest deploy --prebuilt --prod
```

The build retains `web/dist` for previews and emits `.vercel/output` with static assets, the Node.js 22 function and routes. The function renders `/` and `/upload` with a CSP permitting exactly the discovered LAN origin; do not add a separate static IP allowlist. `--prebuilt` uploads this artifact without a remote rebuild. Backend code, models and Pi environments are not bundled. [Build Output API](https://vercel.com/docs/build-output-api), [prebuilt deployment](https://vercel.com/docs/cli/deploy#prebuilt)

The website submits the code to `/api/discovery/resolve`. The response sets an AES-GCM encrypted `HttpOnly; Secure; SameSite=Strict` cookie, then reloads the page. The first GET supplies the one-use pairing bootstrap and removes its code/proof key from that cookie, retaining only endpoint context for eight hours. The browser verifies the Pi's HMAC challenge before sending the local claim. Code/proof data stays in memory for that attempt; only the Pi's upload token is persisted in tab storage.

On the Pi, retain the exact `CHORDSENSE_ALLOWED_ORIGINS` and `CHORDSENSE_COMPANION_URL`, add `CHORDSENSE_DISCOVERY_URL=https://<website>/api/discovery/publish` and the same private `CHORDSENSE_DISCOVERY_TOKEN`, copy the updated backend and restart the existing API once after its tests pass. No Tauri rebuild or `iod` restart is required for an already installed Pair uploads screen. See [Pi activation steps](../backend/deploy/README.md#activate-automatic-discovery-on-an-existing-pi).
