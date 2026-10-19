# Companion website and Pi device boundary

The companion website is the **song manager previously rendered by Flask at `/upload` on port 5051**. It uploads, lists, downloads and deletes songs stored on one ChordSense device. Its source lives in `web/`; Vercel serves static assets and a small discovery/page function for `/` and `/upload`.

The attached-screen Tauri interface remains on the Pi in `device-ui/`. Play Along, Record, chord diagrams, playback controls and live feedback use bundled assets and the native Rust bridge at `127.0.0.1:5051`. Port **5051 stays the device API port**.

```text
Pi -- HTTPS publish/cancel while pairing --> Vercel discovery <--> Upstash Redis
Browser -- HTTPS website + six-digit lookup --------^              <=120s record
Browser -- direct Wi-Fi HTTP :5051 --> Pi Flask / Waitress API
                                           ^            |
Pi attached-screen Tauri -- localhost -----|       local Unix socket
                                                        |
                                                   iod ADC / DAC
                                                   live feedback
```

## Activation status

Automatic discovery is implemented locally but is **not activated**. Upstash Marketplace terms acceptance is pending, no discovery store has been provisioned, and this feature has not been deployed to production or copied to the Pi. The [current terms-acceptance page](https://vercel.com/thomas11-e47b/~/integrations/accept-terms/upstash?source=cli) requires the account owner's action. The earlier [dated deployment record](pi-pairing-deployment.md) and [browser results](browser-compatibility.md) do not establish that this new flow has been tested against production hardware.

Activation needs Vercel runtime configuration and one Pi backend source update/API restart. An already installed **Pair uploads** screen needs no Tauri rebuild; `iod` needs no restart.

## Connection options

End users join the same reachable Wi-Fi/LAN as the Pi and enter six digits. When **Pair uploads** starts, the Pi reads its current RFC1918 IPv4 on `wlan0` and publishes `http://<current-address>:5051` to the companion's HTTPS directory. The website resolves the code to that address at runtime. There is no build-time IP, `.local` requirement, IP scan or URL entry. Tailscale is administrative SSH only; users do not install it.

A new Pair uploads action reads the address again. After DHCP changes, re-pair with a fresh code; **no website rebuild or redeployment is needed**. Existing tokens remain bound to their old API address/context and are not silently sent to a different endpoint. The Pi needs exactly one private IPv4 on `wlan0` and Internet access for publication. The browser needs Internet access for the website/lookup and direct Wi-Fi access to the Pi for transfers. The native screen and hardware paths remain usable offline.

All browsers may attempt a connection. Allow local-network access if asked. HTTPS-to-local-HTTP behavior varies, and discovery does not bypass browser restrictions. Guest Wi-Fi/client isolation can still prevent peers from reaching each other. During the 2026-10-15 diagnosis, the old deployed campus address timed out while the Pi's new address answered HTTP 403 from the same Mac; that established stale configuration, not client isolation. See [observed browser tests](browser-compatibility.md) and [Local Network Access](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Local_network_access).

This remains a **trusted-network HTTP prototype**. Discovery publication, lookup and page delivery use HTTPS. The subsequent local claim, upload token and audio use LAN HTTP and are not encrypted by the discovery service. Vercel never proxies audio or device control.

## Short-code pairing and address proof

1. Tap **Pair uploads** on the Pi. The API generates the code, window identity and random proof key, reads the Wi-Fi address, and publishes the window over authenticated HTTPS before showing it as ready.
2. Open the displayed companion website, enter all six digits including leading zeros, and select **Pair device**.
3. The website submits the code to its same-origin discovery function. A match sets an encrypted browser context cookie and reloads the page with an exact CSP allowance for this Pi address.
4. The browser verifies a fresh HMAC challenge response from that pairing window before sending its local claim. Allow local-network access if asked. A valid claim connects immediately, without a second physical approval.
5. Upload or manage songs directly on the Pi. A fresh browser session pairs with a fresh code.

Each directory record lives for **at most 120 seconds**. Redis holds a keyed code digest, address, window identity and proof key; it does not store raw codes or audio. Five wrong code lookups lock that active window globally. Each client IP has a **30-lookups-per-120-seconds** budget, using a keyed IP identifier. Correct lookups remain retryable until the Pi consumes the claim or the window expires. Storage failures fail closed; there is no per-Function-memory fallback. Redis TTLs and atomic operations apply across Vercel instances. [Upstash REST operations](https://upstash.com/docs/redis/features/restapi)

The function uses an **AES-GCM encrypted, HttpOnly, Secure, SameSite=Strict** context cookie. The first page GET supplies inert bootstrap data and removes its pending code/proof key from the cookie, retaining only endpoint/window context for eight hours. The browser keeps the bootstrap in memory for that attempt; it does not put codes or proof keys in URLs or `sessionStorage`. The HMAC proof prevents an unrelated host at a stale address from receiving the claim's secrets before identifying that window. It does not encrypt later HTTP traffic.

A successful claim issues a random 256-bit upload token, stored only in the current tab's `sessionStorage` with its exact API address, discovery context and expiry. Sessions last **eight hours**; at most **32** reside in Pi memory. API restarts invalidate them. **Cancel pairing** and closing the dialog cancel an unused window. **Disconnect all** cancels it and revokes browser sessions. Website **Disconnect** revokes its current token. The native screen checks status every two seconds only while the pairing panel has a pending code; there is no always-on directory, network or library polling.

## API scope

| Host and route | Purpose |
| --- | --- |
| Vercel `POST /api/discovery/publish` | Pi publication, authenticated by the private discovery token |
| Vercel `DELETE /api/discovery/publish` | Conditional cancellation of that window |
| Vercel `POST /api/discovery/resolve` | Same-origin six-digit browser lookup |
| Vercel `GET /`, `/upload` | Page with encrypted endpoint context and exact CSP |
| Pi `POST /api/pairing/proof` | Fresh challenge response for the discovered window |
| Pi `POST /api/pairing/claim` | Claim using code, window identity and verified proof key |
| Pi `GET /health`, `GET /api/uploads` | Authenticated browser connection/library |
| Pi `POST /api/uploads`, `GET/DELETE /api/uploads/<filename>` | Authenticated song transfers and deletion |
| Pi `POST /api/pairing/logout` | Revoke the current upload session |

Pi `POST /api/pairing/start`, `GET /api/pairing/status`, `POST /api/pairing/cancel` and `POST /api/pairing/revoke` remain native-only, unforwarded loopback controls. Remote browsers cannot generate codes, revoke all sessions, record, control playback, run feedback/analysis or inspect jobs. CORS accepts only exact configured website origins and permitted routes/methods. The discovery publication token grants no browser or native-control session; Redis credentials remain only in Vercel.

The Pi serves no companion HTML/static assets. Vercel's function handles small pairing metadata and page bootstrap, while assets use its CDN. Physical controls and the local display remain described in [Architecture.md](Architecture.md); that broader specification's BLE provisioning and cloud-library interfaces are not implemented by this split.

## Vercel setup from the local working directory

An existing Vercel account and Node.js are required. CLI deployment includes local working files without staging or committing them. The following commands are setup instructions, not evidence they have been run.

1. From the **repository root**, log in and link the existing companion project:

   ```sh
   npx vercel@latest login
   npx vercel@latest link
   ```

   Choose the account/team and project, with `./` as the project directory. Leave dashboard **Root Directory blank**, Framework **Other**, build command `node web/scripts/build.mjs`, and clear any Output Directory override. Keep the root [vercel.json](../vercel.json) and [.vercelignore](../.vercelignore); source deployments exclude backend/models/data/environments.

2. Open [Upstash in Vercel Marketplace](https://vercel.com/marketplace/upstash), accept its terms as the account owner, and create/connect **one free Redis database** to this project's **Production** environment. Keep automatic upgrades disabled (`autoUpgrade=false`); do not choose a paid plan. Reuse an existing appropriate database rather than creating duplicates.

   The pending attempt created no resource because terms acceptance is required. After accepting through the [current terms page](https://vercel.com/thomas11-e47b/~/integrations/accept-terms/upstash?source=cli), the optional CLI provisioning equivalent is:

   ```sh
   npx vercel@latest integration add upstash/upstash-kv \
     --name chordsense-pairing --plan free \
     --metadata primaryRegion=iad1 --metadata autoUpgrade=false \
     --environment production --no-env-pull --non-interactive
   ```

   This provisions a resource; run it only if one has not already been created in the dashboard. Provider fields can be inspected with `npx vercel@latest integration add upstash/upstash-kv --help`. [Vercel integration CLI](https://vercel.com/docs/cli/integration)

3. Configure these **Production runtime** settings in the Vercel project:

   | Variable | Value |
   | --- | --- |
   | `UPSTASH_REDIS_REST_URL` | Database HTTPS REST origin |
   | `UPSTASH_REDIS_REST_TOKEN` | Database write-capable REST token |
   | `CHORDSENSE_DISCOVERY_TOKEN` | Private 43-character base64url token generated from 32 random bytes |
   | `CHORDSENSE_COMPANION_ORIGIN` | Exact HTTPS production origin, e.g. `https://chordsense-phi.vercel.app` |

   The integration's `KV_REST_API_URL`/`KV_REST_API_TOKEN` aliases also work. Generate the publication token once with `node -e 'process.stdout.write(require("node:crypto").randomBytes(32).toString("base64url"))'`, store it securely, and put the identical value in the Pi environment below. Redis credentials stay in Vercel; do not send them to the Pi. Never expose these settings through frontend/build variables. The website origin is public and must have no path or trailing slash.

4. Remove obsolete `CHORDSENSE_API_URL` values from shell/dashboard build settings. The build rejects that variable; there is no device address in the bundle and no SSH preparation command.

   ```sh
   npm --prefix web test
   npm --prefix web run build
   npx vercel@latest deploy --prebuilt --prod
   ```

   Build output includes preview files in `web/dist`, static assets under `.vercel/output/static`, a Node.js 22 function under `.vercel/output/functions`, and routes in `.vercel/output/config.json`. The function supplies the dynamic page CSP permitting exactly the discovered RFC1918 origin. Do not add a second static CSP/IP allowlist: policies intersect. `--prebuilt` uploads the prepared output without a remote rebuild. [Build Output API](https://vercel.com/docs/build-output-api), [prebuilt deployment](https://vercel.com/docs/cli/deploy#prebuilt)

5. Update/configure the Pi backend as below, run its tests, and restart its existing API once. Then test the stable production website: generate a fresh code, pair, upload a uniquely named small file, download/compare it, delete only that file, and disconnect. Record the deployment, browser version, endpoint and result in the verification record. No successful production discovery test is claimed here.

`npm --prefix web run dev` is a static UI preview; it does not run the HTTPS directory/page function. Unit tests supply mock services. Real automatic pairing requires the configured HTTPS deployment and Pi publication.

## Pi setup for automatic discovery

For a Pi that already has **Pair uploads**, copy only the updated backend, configure discovery and restart the existing API once after its tests pass. No Tauri rebuild, native folder migration or `iod` restart is required for this feature. A fresh device still needs the compatible screen/API installation described in [the device guide](../backend/deploy/README.md).

Preserve the existing model environments, checkpoints, SDKs and hardware settings. Add these to its protected API environment file, using the actual stable website and the same private token configured in Vercel:

```dotenv
CHORDSENSE_API_HOST=0.0.0.0
CHORDSENSE_API_PORT=5051
CHORDSENSE_API_THREADS=8
CHORDSENSE_ALLOWED_ORIGINS=https://chordsense-phi.vercel.app
CHORDSENSE_COMPANION_URL=https://chordsense-phi.vercel.app
CHORDSENSE_DISCOVERY_URL=https://chordsense-phi.vercel.app/api/discovery/publish
CHORDSENSE_DISCOVERY_TOKEN=replace-with-the-same-private-43-character-token
CHORDSENSE_IOD_SOCKET=/run/chordsense/iod.sock
```

The discovery URL must be HTTPS, on the same origin as `CHORDSENSE_COMPANION_URL`, with exactly `/api/discovery/publish` as its path. Both discovery fields are required in production. Leaving both absent preserves legacy isolated development/test pairing, which does not provide automatic discovery. Partial/invalid settings fail pairing closed. A publication failure leaves no usable pending code; check the Pi's Internet connection, cloud settings and storage instead of changing the user connection to Tailscale.

Use one hardware-owning API process and `python serve.py` in its existing environment. The native Rust bridge stays at `127.0.0.1:5051`. See [the backend activation steps](../backend/deploy/README.md#activate-automatic-discovery-on-an-existing-pi) for source-copy, tests, environment editing and the existing system service restart.

The local native folder remains `device-ui/`, with executable `device-ui`; the [dated deployment record](pi-pairing-deployment.md) records the older installed Pi layout. This backend update does not migrate that folder or UI service. Any later native migration must verify and update its actual `WorkingDirectory` and `ExecStart` separately.

## Compute ownership and efficiency

| Work | Owner |
| --- | --- |
| Song-manager static serving | Vercel CDN |
| Discovery, encrypted page context and exact CSP | Small Vercel function and short-lived Redis metadata |
| Companion form and library rendering | Laptop/phone browser |
| Attached-screen Play Along/Record UI and diagrams | Pi Tauri app |
| Backing-track playback and speed processing | Pi backend and DAC |
| Guitar acquisition and live recognition | Pi iod and local recognizer |
| Whole-song/recording inference | Pi by default; optional separate worker |
| Songs, recordings and analysis caches | Pi storage |

Moving `/upload` removes that web-page serving from the backend, while the attached screen still needs Pi rendering. The retained device optimizations address larger sources of avoidable work:

- ADC acquisition sleeps without SPI reads while idle and wakes for capture/streaming. Restarts prime fresh interpolation; indices remain on the daemon-wide monotonic grid.
- Playback-speed renders are serialized, superseded waiting requests skip rendering, and FFmpeg threads are bounded. Cached renders are reused.
- The screen's animation loop stops while paused or hidden.
- Uploaded-song analysis requests local `/analyze_uploaded` by filename, avoiding a download and re-upload of a stored file.
- Whole-file analyses share a compute limit and reuse content-based caches. An optional worker can remove uncached inference from the Pi; live feedback remains local.

## Optional separate analysis worker

`backend/analysis_worker.py` runs on another host with the CNN-LSTM repository, checkpoints and its separate model environment. It opens no ADC/DAC hardware and needs no Hailo bindings. The Pi streams audio from disk to its HTTPS `/analyze` endpoint; the website neither exposes nor calls it.

Configure the worker with [analysis-worker.env.example](../backend/deploy/analysis-worker.env.example), install `backend/requirements-worker.txt`, and set `CHORDSENSE_ANALYSIS_URL` plus the matching `CHORDSENSE_ANALYSIS_TOKEN` on the Pi. Keep the worker secret out of Vercel. Its proxy must allow long responses: the model timeout defaults to 900 seconds and the device gateway timeout to 960 seconds. A normal Cloudflare proxy has a shorter origin response limit. [Cloudflare origin timeout](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/)

The worker serializes inference, caches results and removes temporary inputs. Offloaded recordings use CNN-LSTM rather than the local custom CNN/Hailo model, so recognition quality can differ. Configured worker failures preserve recordings and report failure without silently starting expensive local inference. Leave worker configuration unset to retain local offline analysis; cached results remain usable in either mode.

## Limits and validation

The companion request limit defaults to **95 MiB including multipart overhead**; choose files comfortably below that size. The native application keeps its 300 MiB limit. Uploads go to the Pi rather than Vercel Functions. The separate analysis worker is the compute-offload option.

Earlier checks covered pairing expiry, attempt limits, one-use/concurrent claims, session authorization, API scope and browser requests; mocked-reader Rust tests covered idle ADC behavior. All 82 backend tests passed on the earlier deployed Pi, and desktop Chrome completed pairing, upload, exact download, delete and Disconnect against the earlier fixed endpoint. The rebuilt native screen was visually verified. See the [dated deployment record](pi-pairing-deployment.md) for those results and cleanup. They do not validate automatic discovery. Redis provisioning, actual Redis integration, production discovery deployment, the backend update and end-to-end browser verification are pending. Android, permission rejection/retry, SPI acquisition, recognition, DAC playback and thermal behavior also need device validation.
