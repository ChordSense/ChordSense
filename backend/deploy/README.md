# Device API and short-code pairing deployment

Vercel hosts the separate upload/library website. The Pi keeps the Tauri Play Along, Record and live-feedback screen, `iod`, storage and analysis. The companion connects to one configured LAN endpoint, defaulting to `http://chordsense.local:5051`, using a short pairing code generated on the device.

These are instructions for future installations/updates. The current Pi has already been updated and its native app rebuilt; see the [verified deployment record](../../docs/pi-pairing-deployment.md) for actual paths, services and testing. Install the **matching backend and rebuilt native Tauri app**. An old installed screen app cannot show the new **Pair uploads** button. See [Vercel setup](../../docs/web-device-architecture.md#vercel-setup-from-the-local-working-directory) before setting the production origin.

The laptop's native app now lives directly in `device-ui/`, and its executable is named `device-ui`. The stopped Pi has not received this folder reorganization or rename, and its installed UI service still uses the recorded prior layout. The new paths below are for its next source copy/build; they are not a claim that the Pi has already migrated.

## Start both updated applications

Uncommitted laptop changes are not available through a Git pull. From the **laptop repository root**, replace `user`, `<Pi-address>` and `<existingPiCheckout>` with the actual Pi login, reachable address and absolute existing checkout path, then copy the updated API and screen source:

```sh
rsync -av --exclude='models/' --exclude='venv/' --exclude='.venv/' --exclude='__pycache__/' --exclude='.env*' backend/ 'user@<Pi-address>:<existingPiCheckout>/backend/'
rsync -av --exclude='node_modules/' --exclude='src-tauri/target/' --exclude='.env*' device-ui/ 'user@<Pi-address>:<existingPiCheckout>/device-ui/'
```

These commands intentionally have no `--delete`. They preserve existing Pi model repositories, environments, local `.env` files, SDK installations and runtime data. They copy source only; the Pi native executable must still be rebuilt. The existing Pi checkout must already exist; the native copy can create its new `device-ui/` destination. The current model/SDK setup remains in place.

When updating the existing Pi, stop its UI through the existing launcher before replacing source. Copying into `device-ui/` does not remove the old nested installation. After rebuilding, update the existing `chordsense-ui.service` user unit to use `WorkingDirectory=<existingPiCheckout>/device-ui` and `ExecStart=<existingPiCheckout>/device-ui/src-tauri/target/release/device-ui`, replacing the placeholder with the actual absolute checkout path. Preserve its existing desktop/I/O environment, including `WEBKIT_DISABLE_DMABUF_RENDERER=1`. Reload the user service definitions and restart that same UI unit only when applying the device update; no Pi migration is performed by this local change.

Apply the network/environment prerequisites below first, and stop the old API process through its existing service or launcher. Run exactly one API process; an additional process would compete for hardware/session ownership. If the current backend is running in a terminal, stop it there; if a service owns it, stop that actual service. `sudo ss -ltnp 'sport = :5051'` on the Pi identifies the listening process without assuming a unit name.

For the first manual run, open a Pi desktop terminal in `<existingPiCheckout>/backend/`. The example uses the `.venv` created by `setup.py`; use `venv` instead if that is the existing environment:

```sh
.venv/bin/python -m pip install -r requirements.txt
set -a
. /etc/chordsense/api.env
set +a
.venv/bin/python serve.py
```

Keep that process running. In a **second terminal on the Pi desktop**, open `<existingPiCheckout>/device-ui/` and build/start the updated native screen:

```sh
npm ci
npm run tauri build
./src-tauri/target/release/device-ui
```

The executable path assumes Cargo's default target directory; use the build output path if `CARGO_TARGET_DIR` is configured. For an installed package or autostart launcher, replace its old application artifact with the rebuilt Linux/Pi artifact and restart that same launcher. The existing Rust/Tauri/Linux build dependencies must already be available; a macOS build is not a Pi executable.

If an existing API service runs `serve.py` with this environment, install requirements into that service's Python environment and restart the **same service** rather than running the manual API command as well. The service section below describes the supplied example.

## Network prerequisites

The companion lets every browser attempt a connection. Allow local-network access if prompted; HTTPS-to-local-HTTP behavior varies, so consult [observed browser tests](../../docs/browser-compatibility.md). Both browser and Pi must be on a trusted network that permits peer access; the default `.local` endpoint additionally requires mDNS multicast. Guest Wi-Fi or client isolation can block communication despite matching Wi-Fi names. HTTP does not encrypt codes, tokens or song files. [Local Network Access](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Local_network_access)

Set the Pi hostname to `chordsense` using Raspberry Pi configuration or, when ready on the device:

```sh
sudo hostnamectl set-hostname chordsense
sudo apt-get install avahi-daemon
sudo systemctl enable --now avahi-daemon
```

Raspberry Pi OS normally includes Avahi. Its `.local` name follows the hostname. [Raspberry Pi mDNS documentation](https://www.raspberrypi.com/documentation/computers/remote-access.html#resolve-raspberrypi-local-with-mdns)

After starting the LAN API, check from a macOS/Linux laptop on the target network:

```sh
ping -c 1 chordsense.local
curl -i --connect-timeout 5 http://chordsense.local:5051/health
```

The hostname should resolve to the Pi's LAN address. Before pairing, HTTP **403** from `/health` is expected without an Origin header and confirms API reachability. With `-H 'Origin: https://your-actual-app.vercel.app'` and the configured exact origin, HTTP **401** is expected until paired. If resolution fails, check hostname/Avahi/multicast support. If it resolves but port 5051 is unreachable, check the API listener, firewall and Wi-Fi peer isolation. On Windows use `ping -n 1 chordsense.local` for the name check. Internet access is still needed to open the Vercel website; the native Pi app continues working offline.

## Environment

Start from [chordsense-api.env.example](chordsense-api.env.example). Configure the following, replacing the website example with the stable production address assigned by Vercel:

On the Pi, create or edit the environment file without replacing an existing configuration:

```sh
sudo install -d -m 755 /etc/chordsense
sudoedit /etc/chordsense/api.env
sudo chown "$(id -un):$(id -gn)" /etc/chordsense/api.env
sudo chmod 600 /etc/chordsense/api.env
```

The ownership command is for a manual run as the current Pi account; choose the actual service owner when configuring another startup account. In the editor, add/update these values and preserve any existing model, live-recognizer and I/O settings:

```dotenv
CHORDSENSE_API_HOST=0.0.0.0
CHORDSENSE_API_PORT=5051
CHORDSENSE_API_THREADS=8
CHORDSENSE_ALLOWED_ORIGINS=https://chordsense-upload.vercel.app
CHORDSENSE_COMPANION_URL=https://chordsense-upload.vercel.app
CHORDSENSE_IOD_SOCKET=/run/chordsense/iod.sock
```

`CHORDSENSE_ALLOWED_ORIGINS` contains exact browser origins, without a path or trailing slash. Development/preview origins must be explicitly added as comma-separated entries; do not allow every `*.vercel.app` site. `CHORDSENSE_COMPANION_URL` is the public website address shown on the native pairing screen. No shared `CHORDSENSE_DEVICE_TOKEN` is used, and no pairing/session secret belongs in Vercel.

The API listens on the LAN while the native Rust bridge continues using `127.0.0.1:5051`. Only unforwarded, Origin-free loopback requests receive native control permissions. LAN callers are subject to pairing/session auth and the companion route allowlist.

Keep one production Waitress process with eight bounded HTTP threads by default. [serve.py](../serve.py) sets BLAS/OpenMP defaults to one thread before importing the app, preserves forwarding headers and performs normal cleanup on SIGTERM. The local desktop SSE bridge occupies one HTTP thread. Use `serve.py`; avoid another WSGI process or the Flask development server.

## Pairing and browser session contract

Tap **Pair uploads** on the rebuilt Pi screen to generate a code automatically. **Generate new code** replaces an expired/locked code when needed. Open the displayed website on the computer or phone, enter all six digits and select **Pair device**. The correct claim connects immediately; no separate approval or QR code is required.

- Code lifetime: **120 seconds**; five wrong claims close the window globally. Successful claiming consumes the code once.
- Session: random **256-bit** token, upload-only, **eight hours**. At most **32** sessions are kept in memory; restarting the API revokes them.
- Browser storage: `sessionStorage` in the current tab. Requests use the Authorization header; codes are sent in the claim's JSON body.
- **Cancel pairing** closes a pending code. **Disconnect all** closes it and revokes all sessions. Closing the dialog cancels its unused code while preserving already paired sessions.
- Website **Disconnect** revokes the current token. A fresh tab pairs separately; another browser's session stays valid.
- Status requests occur only while the native pairing panel has a pending code, every two seconds. There is no always-on pairing or library polling.

The short code authorizes access to the configured Pi endpoint; it is not a discovery mechanism. The website contains no manual URL/token setup flow, cloud registry, Bluetooth path or tunnel requirement. The current campus deployment uses the Pi's private IPv4 address because `.local` resolution failed. Its frontend build override and exact CSP entry must be updated if DHCP changes the address; this does not change the Pi's loopback bridge or end-user code entry.

Native-only loopback routes:

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/pairing/start` | Open a new code window |
| `GET` | `/api/pairing/status` | Read pairing/session status |
| `POST` | `/api/pairing/cancel` | Cancel the pending code |
| `POST` | `/api/pairing/revoke` | Cancel pairing and revoke all sessions |

Browser claim: `POST /api/pairing/claim` with JSON `{"code":"012345"}`. A successful result supplies `token`, `expires_in: 28800`, `expires_at` in Unix seconds and `permissions: ["uploads"]`. The claim requires a valid open code rather than an existing session. CORS preflight may proceed without a session only for permitted routes/methods and the exact allowed website origin.

Authenticated browser operations:

| Operation | Endpoint |
| --- | --- |
| Check connection and upload limit | `GET /health` |
| List uploaded songs | `GET /api/uploads` |
| Upload a song | `POST /api/uploads`, multipart `file` |
| Download a stored song | `GET /api/uploads/<filename>` |
| Delete a stored song | `DELETE /api/uploads/<filename>` |
| Revoke this browser session | `POST /api/pairing/logout` |

Other remote routes return HTTP 403, including native pairing controls, analysis/jobs, playback, recording and feedback. Uploads store complete files atomically without inference; duplicate uploads preserve both files. Audio downloads support range requests. The backend serves no website/static files.

`CHORDSENSE_MAX_UPLOAD_MB` defaults to **95 MiB for the complete companion request**. Choose files comfortably below that value to leave multipart overhead. LAN transfers go directly to the Pi, without a Vercel Function or cloud proxy. The local native application retains its 300 MiB request limit.

## Existing service or supplied example

Before changing startup, identify the process using port 5051 and the service/launcher that owns it. Update that existing startup path, or deliberately stop/disable it before choosing a replacement. Do not install a second API service alongside an old `app.py` process.

The supplied [chordsense-api.service](chordsense-api.service) assumes user/group `chordsense`, checkout `/home/chordsense/workspace/ChordSense`, and `backend/.venv/bin/python`. These are **example paths**, not a detected Pi installation. Adjust `User`, `Group`, `WorkingDirectory` and `ExecStart` to the actual device and existing environment. The unit starts after Avahi and the existing `chordsense-iod.service`.

Put the configured environment at `/etc/chordsense/api.env`, owned/readable only by the intended service owner (for example mode 600). If using the supplied unit as the sole API startup, install it after replacing/disabling the old startup deliberately. For updates to an **already configured `chordsense-api.service`**:

```sh
sudo systemctl daemon-reload
sudo systemctl restart chordsense-api.service
sudo systemctl status chordsense-api.service --no-pager
```

If the real service has another name, use that name instead. Restarting the API invalidates paired browser sessions. Rebuild/relaunch the native Tauri app as described above, and share `CHORDSENSE_IOD_SOCKET` with `iod`. To apply the retained idle-sampling change, follow the separate [iod build/service guide](../../iod/deploy/README.md); changing Python files does not update the daemon binary.

## Local application and compute

Native `/begin_recording`, `/end_recording`, `/feedback/*`, `/playback/*` and `/analyze` contracts remain local. Recorded WAVs are read through the returned `wav_path`, not a companion recording-download route. Uploaded-song analysis uses local `/analyze_uploaded` by filename, avoiding a download and re-upload.

Native callers may use `/analyze?async=1`, `/analyze_uploaded?async=1` and `/end_recording?async=1`, then inspect `/api/jobs/<job_id>`; synchronous calls remain compatible. Those jobs are unavailable to the website. One analysis runs at a time, with up to four queued jobs; a full queue returns HTTP 503 and `Retry-After: 5`. Completed job status is bounded and lost on restart, while saved audio and content-based caches remain. Temporary inputs are cleaned up; stored-song analysis retains a stable input before hashing/queuing.

ADC sampling sleeps while idle. Playback-speed FFmpeg rendering is serialized, thread counts are bounded and cached renders can be reused. Native animations stop while paused/hidden. The local screen, hardware and live-recognition compute remain on the Pi.

## Optional separate analysis worker

Set the full `CHORDSENSE_ANALYSIS_URL`, including `/analyze`, and matching `CHORDSENSE_ANALYSIS_TOKEN` to offload whole-song/recording analysis requested by the local app. A configured worker failure preserves audio and reports failure without silently starting local inference. Leave the URL unset for local song/Hailo recording analysis. Live feedback remains local.

On the separate host, use [analysis-worker.env.example](analysis-worker.env.example), install [requirements-worker.txt](../requirements-worker.txt), and provide the CNN-LSTM repository and its model environment. Run `python analysis_worker.py` behind HTTPS; the worker owns no hardware and does not expose browser CORS. Its proxy must allow the 900-second default model timeout and 960-second device gateway timeout. Do not route this long inference request through an ordinary short-timeout cloud proxy.

## Validation

Use an intact Python environment with declared dependencies for tests and `serve.py`. All 82 backend tests passed in the deployed Pi environment. Actual desktop Chrome pairing, authenticated transfers, exact download, UI deletion and Disconnect also passed; details are in the [deployment record](../../docs/pi-pairing-deployment.md). Further device checks must cover Android, permission rejection/retry, native pairing-button input and normal capture/DAC/live feedback. Temperature readings during deployment do not establish thermal performance under those workloads.
