# Companion website and Pi device boundary

The companion website is the **song manager previously rendered by Flask at `/upload` on port 5051**. It uploads, lists, downloads and deletes songs stored on one ChordSense device. Its static source lives in `web/`; Vercel serves `/` and `/upload` from that build.

The attached-screen Tauri interface remains on the Pi; its local repository source lives directly in `device-ui/`. Play Along, Record, chord diagrams, playback controls and live feedback use bundled assets and the native Rust bridge at `127.0.0.1:5051`. Port **5051 stays the backend API port**.

```text
Vercel CDN -- HTTPS static website --> computer / phone browser
                                                    |
                                                    | local Wi-Fi HTTP
                                                    | configured Pi address:5051
                                                    v
Pi attached-screen Tauri app -- localhost --> Flask / Waitress API :5051
                                                    |             |
                                             local Unix socket    | optional HTTPS
                                                    v             v
                                               iod ADC / DAC   separate offline
                                               live feedback   analysis worker
```

## One device on the same network

The website uses one build-configured address, defaulting to **`http://chordsense.local:5051`**. For that default, the Pi hostname must be `chordsense`, and Avahi/mDNS must resolve that name on the laptop or phone. A RFC1918 private IPv4 address on port 5051 can be configured instead; its exact address must also be permitted by the root `vercel.json` CSP. This is a known endpoint, not a device discovery service; the pairing code authorizes a browser that can already reach the device. Raspberry Pi OS supports `.local` names through Avahi. [Raspberry Pi mDNS documentation](https://www.raspberrypi.com/documentation/computers/remote-access.html#resolve-raspberrypi-local-with-mdns)

All browsers can attempt a connection; the application has no browser-brand/version/iOS gate. Allow local-network access if the browser asks. HTTPS-to-local-HTTP networking behavior varies, and short-code pairing does not bypass browser restrictions. The request is attempted first; actual network failures receive permission/connection guidance. See [observed browser tests](browser-compatibility.md). [Local Network Access](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Local_network_access)

Both clients must be on a network that allows access to the Pi. The default hostname additionally requires mDNS multicast. A guest network or Wi-Fi client isolation can prevent this even when the devices show the same Wi-Fi name. Verify the configured endpoint and TCP access on the actual target network; no LAN scanning, QR code, Bluetooth, cloud registry or tunnel is used. The current campus deployment uses `http://10.136.137.225:5051` after actual `.local` resolution failed; its browser-to-Pi transfer test passed. See the [deployment record](pi-pairing-deployment.md).

This is a **trusted-network prototype**. LAN HTTP does not encrypt pairing codes, browser session tokens or audio transfers. Use it only on a network you trust. Internet access is needed to open the Vercel website; it is not an offline PWA. The Pi's native screen and hardware paths continue working offline.

## Short-code pairing

1. On the Pi's updated Tauri screen, tap **Pair uploads** to generate a code automatically. **Generate new code** replaces an expired/locked code when needed.
2. Open the displayed Vercel website on the computer or phone. Enter the six-digit code, including any leading zero, and select **Pair device**.
3. Allow local-network access if the browser asks. A correct claim connects immediately; there is no second device approval.
4. Upload a song or manage the device library. A second browser/tab pairs separately with a new code.

A code lasts **120 seconds**, works once and closes after **five wrong attempts across all callers**. Pairing starts only from the native screen. **Cancel pairing** closes the open window; **Disconnect all** cancels the window and revokes every browser session. Closing the dialog cancels its unused code while preserving existing paired sessions.

Successful pairing issues a random 256-bit upload session token. The website stores it in the current tab's `sessionStorage` and sends it in an Authorization header; users never type or copy it. Sessions expire after **eight hours**. The backend retains at most **32 sessions** in memory, and an API restart invalidates them. The website's Disconnect action revokes its current token; other paired tabs remain independent. Re-pair after expiry, revocation, an API restart or opening a fresh tab.

Code generation, expiry and token lookup need no model work. The native screen checks status every two seconds only while its pairing panel has a pending code; there is no always-on pairing polling. Library refresh is explicit or follows a successful upload/delete. Pairing uses bounded state in the existing API process.

## API scope

The browser can claim an open pairing window and use only these library operations:

| Route | Permission |
| --- | --- |
| `POST /api/pairing/claim` with JSON `{"code":"012345"}` | Valid, unexpired one-use code |
| `GET /health` | Browser session |
| `GET /api/uploads` | Browser session |
| `POST /api/uploads` with multipart `file` | Browser session |
| `GET /api/uploads/<filename>` | Browser session |
| `DELETE /api/uploads/<filename>` | Browser session |
| `POST /api/pairing/logout` | Revoke the current browser session |

`POST /api/pairing/start`, `GET /api/pairing/status`, `POST /api/pairing/cancel` and `POST /api/pairing/revoke` belong to the native loopback connection. Remote callers cannot generate a code, revoke all sessions, record, control playback, start live feedback, run analysis or inspect jobs. CORS accepts only exact configured website origins and the permitted route/method combinations.

The website sends audio straight across the LAN to the Pi. Vercel serves static files only; it does not proxy uploads or run inference. The backend serves no companion HTML/static files. Physical controls and the local display remain described in [Architecture.md](Architecture.md); that broader specification's BLE provisioning and cloud-library interfaces are not implemented by this split.

## Vercel setup from the local working directory

These instructions describe future deployments; the current website and Pi have already been deployed and verified as recorded in [the deployment record](pi-pairing-deployment.md). An existing Vercel account and Node.js are required. CLI deployment includes local working changes without staging or committing them. [Vercel CLI deployment](https://vercel.com/docs/cli/deploying-from-cli)

1. From the **repository root**, run:

   ```sh
   npx vercel@latest login
   npx vercel@latest link
   ```

   Choose your account/team, create or select a project such as `chordsense-upload`, and choose `./` as the project directory. This creates local `.vercel` project metadata.

2. In the Vercel project settings, leave **Root Directory blank** and use the repository's [vercel.json](../vercel.json):

   | Setting | Value |
   | --- | --- |
   | Framework | Other (`framework: null`) |
   | Build command | `node web/scripts/build.mjs` |
   | Output directory | `web/dist` |
   | Install command | The no-package command in `vercel.json` |

   Add the public **Production** environment variable `CHORDSENSE_API_URL=http://chordsense.local:5051`. The website defaults to that address; setting it explicitly records the deployment configuration. This is a public address, not a secret. Do not add session tokens, pairing codes or worker secrets to Vercel.

3. Keep the root [.vercelignore](../.vercelignore) allowlist in place before uploading:

   ```text
   /*
   !web
   !vercel.json
   web/dist/
   web/node_modules/
   web/.env*
   ```

   Only website source and its root configuration are uploaded; backend code, models, runtime data and environments stay out of the source deployment. Vercel builds a fresh `web/dist`. [Vercel ignore allowlists](https://vercel.com/docs/deployments/vercel-ignore)

4. Publish the local website files:

   ```sh
   npx vercel@latest deploy --prod
   ```

   For the current campus Pi address, use this build override instead and retain its exact address in the root `vercel.json` CSP:

   ```sh
   npx vercel@latest deploy --prod --build-env CHORDSENSE_API_URL=http://10.136.137.225:5051
   ```

   If DHCP changes the Pi's address, update the override and exact CSP entry before redeploying. The end user still enters only a short code.

   Open the stable production address assigned to the project, for example `https://chordsense-upload.vercel.app`, and check `/` and `/upload`. Use that exact origin in the Pi configuration below. Vercel's first deployment is production even without `--prod`; the explicit flag makes the intended environment clear. [Vercel project setup](https://vercel.com/docs/projects/deploy-from-cli)

## Pi setup for future installation or updates

Install the updated **backend and rebuilt native Tauri app together**. Publishing Vercel alone does not add the Pair uploads button to an already installed Pi app. For uncommitted laptop changes, copy the updated files through the existing device deployment process; a Git pull cannot fetch those changes.

The local native folder is now `device-ui/`, and its executable is `device-ui`. That folder reorganization and rename have not been applied to the stopped Pi, whose installed binary and UI service still use the earlier layout. On its next update, copy/rebuild `device-ui/` and update the existing UI service's `WorkingDirectory` and `ExecStart` to the new location and executable name. Preserve the actual earlier paths in [the deployment record](pi-pairing-deployment.md); see [the device guide](../backend/deploy/README.md) for the future copy/build instructions.

Configure the device environment:

```dotenv
CHORDSENSE_API_HOST=0.0.0.0
CHORDSENSE_API_PORT=5051
CHORDSENSE_API_THREADS=8
CHORDSENSE_ALLOWED_ORIGINS=https://chordsense-upload.vercel.app
CHORDSENSE_COMPANION_URL=https://chordsense-upload.vercel.app
CHORDSENSE_IOD_SOCKET=/run/chordsense/iod.sock
```

Replace the example website origin with the actual stable production origin, without a path or trailing slash. `CHORDSENSE_COMPANION_URL` is the public website address displayed on the Pi; no shared device token is configured. If using local development or a preview deployment, add its exact origin to the comma-separated `CHORDSENSE_ALLOWED_ORIGINS` value.

The [backend deployment guide](../backend/deploy/README.md) contains the hostname, dependency installation, API startup/service and native rebuild steps. Keep one hardware-owning API process with the bounded Waitress thread pool. The native Rust bridge remains on `127.0.0.1:5051`; an Origin-free, unforwarded loopback call retains its local control permissions. LAN callers must pair. Use `python serve.py` rather than the Flask development server.

Before pairing, verify `chordsense.local` from the laptop/phone's network. On macOS/Linux, `ping -c 1 chordsense.local` should resolve the Pi's LAN address. Then `curl -i --connect-timeout 5 http://chordsense.local:5051/health` should reach the API; HTTP 403 is expected without the browser Origin header. Adding `-H 'Origin: https://your-actual-app.vercel.app'` with the configured exact origin should produce HTTP 401 before pairing. Either response confirms reachability, not an authenticated connection. A resolving hostname alone does not prove Wi-Fi peers can reach port 5051.

## Compute ownership and efficiency

| Work | Owner |
| --- | --- |
| Song-manager static serving | Vercel CDN |
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

The companion request limit defaults to **95 MiB including multipart overhead**; choose files comfortably below that size. The native application keeps its 300 MiB limit. Direct LAN transfers do not pass through Vercel Functions or a tunnel provider.

Local automated checks cover pairing expiry, attempt limits, one-use/concurrent claims, session authorization, API scope and browser requests; mocked-reader Rust tests cover idle ADC behavior. All 82 backend tests passed on the deployed Pi, and actual desktop Chrome completed pairing, upload, exact download, delete and Disconnect against the production website and Pi. The rebuilt native screen was visually verified. See the [deployment record](pi-pairing-deployment.md) for network configuration and cleanup. Android, permission rejection/retry, the native pairing-button interaction, SPI acquisition, recognition, DAC playback and thermal behavior still need device validation.
