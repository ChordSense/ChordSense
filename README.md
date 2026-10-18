# ChordSense

ChordSense combines a local Pi device interface with a separate companion website:

- **Attached-screen app:** `device-ui/` provides Play Along, Record, chord diagrams, playback controls and live feedback. Its native bridge calls `127.0.0.1:5051`.
- **Companion website:** `web/` is the Vercel song manager. A small discovery function finds the Pi's current Wi-Fi address from a short code; uploads, lists, downloads and deletes then go directly across the LAN.
- **Backend and hardware:** `backend/` retains API port **5051**; Rust `iod` owns ADC recording and DAC playback. Browser permissions cover pairing and the upload library only.
- **Optional analysis worker:** `backend/analysis_worker.py` can move whole-song/recording inference to another host. Live feedback remains local.

The companion replaces the Flask-rendered **`/upload` page formerly served on port 5051**. Vercel serves `/` and `/upload`; it does not host the Pi screen or proxy audio. The native screen retains bundled assets and offline device use.

## Pair a laptop or phone

The Pi publishes its current Wi-Fi address to the companion's short-lived discovery directory when **Pair uploads** starts. Entering the six-digit code finds that address automatically. There is no IP address in the website build, `.local` dependency, URL entry or network scan. Tailscale is for administrative SSH only; users need no Tailscale installation.

Tap **Pair uploads** on the updated Pi screen to generate a six-digit code, open the displayed website, enter the code and select **Pair device**. Allow local-network access if your browser asks. No device URL or long token needs to be typed.

Codes last 120 seconds, work once and close after five wrong attempts. A successful claim creates an eight-hour upload-only session for the current browser tab. Closing the Pi pairing dialog cancels an unused code; **Disconnect all** revokes every browser session. API restarts require pairing again.

The website lets every browser attempt a connection. HTTPS-to-local-HTTP behavior varies by browser, and Wi-Fi must allow peer communication. Allow browser local-network access if asked. Discovery uses HTTPS, but subsequent LAN HTTP does not encrypt pairing claims, tokens or audio. After the Pi's Wi-Fi IP changes, show a fresh code and pair again; existing sessions do not silently move to another address. No website redeployment is needed. See [connection requirements](docs/web-device-architecture.md#connection-options) and [browser test results](docs/browser-compatibility.md).

Automatic discovery is implemented locally but **not activated**: Upstash Marketplace terms acceptance and storage provisioning are pending, and this feature has not been deployed or copied to the Pi. It needs one backend source update, discovery environment settings and one API restart; the existing Pair uploads screen needs no rebuild, and `iod` needs no restart. See [the setup guide](docs/web-device-architecture.md#vercel-setup-from-the-local-working-directory), [Pi update steps](backend/deploy/README.md#activate-automatic-discovery-on-an-existing-pi) and [dated deployment record](docs/pi-pairing-deployment.md). Earlier browser results do not validate the new discovery flow.

## Prerequisites

- Git with submodule support.
- Python 3.10+ with `venv`; the optional Windows/Linux `setup.py` helper specifically requires Python 3.12.
- Rust/Cargo, Node.js/npm and the platform's Tauri build dependencies.
- Linux audio tooling/development libraries, FFmpeg and the Pi's existing system HailoRT installation for Hailo inference.

## Clone the repository

For a fresh checkout:

```sh
git clone --recurse-submodules https://github.com/ChordSense/ChordSense.git
cd ChordSense
```

If an existing checkout has missing submodules, initialize them with `git submodule update --init --recursive`. Keep the current working branch when updating an existing project.

## Project structure

```text
ChordSense/
├── backend/                  # device API, model integration, optional worker
│   ├── deploy/               # API/worker configuration and deployment guides
│   └── models/               # separate model repositories/environments
├── device-ui/                # local attached-screen Tauri app
├── web/                      # Vercel companion website
├── iod/                      # Pi ADC/DAC daemon
├── docs/
└── setup.py
```

## Backend setup

Use the Pi's existing compatible backend environment for updates. For a new developer environment, from `backend/`:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`setup.py` uses `backend/.venv`; some existing manual installations use `backend/venv`. Match the real Python environment in the API launcher/service rather than creating another running process. The hardware deployment also needs the existing system Hailo bindings and `iod` socket configuration.

Configure the exact Vercel production origin in `CHORDSENSE_ALLOWED_ORIGINS`, and set `CHORDSENSE_COMPANION_URL` to that public website address displayed by the Pi. Set `CHORDSENSE_DISCOVERY_URL` to its `/api/discovery/publish` endpoint and share the private `CHORDSENSE_DISCOVERY_TOKEN` only between the Pi and Vercel function. The API listens on `0.0.0.0:5051` for LAN uploads; its native bridge still uses loopback. Browser upload tokens are issued by pairing and are separate from this server publication secret.

## Model repository setup

The CNN-LSTM repository keeps a **separate Python environment** at `backend/models/chord-cnn-lstm-model/venv`. Its requirements and the reviewed `backend/patches/chord-model-compat.patch` are part of the existing model setup. For a fresh supported Windows/Linux installation, `python3.12 setup.py` initializes submodules, environments and the model compatibility patch. Existing installations should retain their model environments/checkpoints while updating the API/UI files.

The local custom CNN/Hailo recording path lives in `backend/models/chordsense_cnn/`. Optional worker inference uses CNN-LSTM and may produce different results. Do not assume the training environment is the same as the device API environment.

## Rust and Tauri installation

Install Rust through [the official Rust instructions](https://rust-lang.org/tools/install/), then use [Tauri's platform prerequisites](https://v2.tauri.app/start/prerequisites/) for the screen build. Linux audio builds also need `pkg-config` and ALSA development headers such as `libasound2-dev`. If Rust was just installed, reload the shell or source its environment:

```sh
source "$HOME/.cargo/env"
cargo --version
rustc --version
```

## Build and run the native app

From `device-ui/`, with the required system dependencies:

```sh
npm ci
npm run tauri build
./src-tauri/target/release/device-ui
```

Build/install the matching Linux/Pi artifact and relaunch the existing screen launcher to obtain **Pair uploads**. The executable path above assumes the default Cargo target directory. For development, use `npm run tauri dev`.

The local native folder is flat and named `device-ui/`, with a `device-ui` executable. The [dated deployment record](docs/pi-pairing-deployment.md) uses the earlier Pi layout. Verify the installed service before a future native update, and adjust its `WorkingDirectory` and `ExecStart` when migrating it to `device-ui/`. Automatic discovery needs an API update/restart, but no native rebuild or folder migration.

Start the matching backend separately with `python serve.py` from its configured environment, or restart its existing configured service. Use one API process; native hardware/session ownership is process-local. Follow [backend/deploy/README.md](backend/deploy/README.md) for environment loading, hostname setup and both startup steps. Remote companion sessions cannot record, play, seek, run live feedback or submit analysis jobs.

## Companion development and Vercel

From the repository root:

```sh
npm --prefix web run dev
npm --prefix web test
npm --prefix web run build
```

`npm run dev` previews the static interface at `http://127.0.0.1:5173/`; it does not run the HTTPS discovery function. Unit tests supply mocked discovery/device services. Test real automatic pairing through the configured HTTPS deployment and updated Pi backend.

Vercel uses the **repository root**: leave Root Directory blank, Framework Other, build `node web/scripts/build.mjs`, and clear any Output Directory override. The build keeps `web/dist` and emits static assets, a Node.js 22 discovery function and routing in `.vercel/output` using the [Build Output API](https://vercel.com/docs/build-output-api). The function generates the page's exact CSP from its discovered endpoint at request time. Remove `CHORDSENSE_API_URL`; builds reject that obsolete setting.

With a Vercel account, run `npx vercel@latest login` and `npx vercel@latest link` (project directory `./`). Provision the free Upstash store with automatic upgrades disabled, then set the server environment described in [the setup guide](docs/web-device-architecture.md#vercel-setup-from-the-local-working-directory). Build/test and deploy local files without a Git commit:

```sh
npm --prefix web test
npm --prefix web run build
npx vercel@latest deploy --prebuilt --prod
```

Vercel runtime settings are `UPSTASH_REDIS_REST_URL`/`UPSTASH_REDIS_REST_TOKEN` (or their `KV_REST_API_URL`/`KV_REST_API_TOKEN` aliases), `CHORDSENSE_DISCOVERY_TOKEN` and the exact `CHORDSENSE_COMPANION_ORIGIN`. Keep these server-side; neither Redis credentials nor the publication secret belongs in frontend assets. The old SSH preparation CLI is removed because the Pi publishes its address for each new pairing window.

## Pi efficiency and audio

`iod` sleeps without SPI reads while idle and wakes for capture/live streaming. Playback-speed renders are serialized with bounded FFmpeg threads; screen animations stop while paused/hidden. Uploaded-song analysis uses local `/analyze_uploaded` by filename instead of downloading/re-uploading a stored file. These changes reduce avoidable Pi work while the attached screen, DAC playback and live recognition remain local.

See [iod deployment](iod/deploy/README.md) for daemon builds/service configuration, [audio output](docs/audio-output.md) for the DAC path and troubleshooting, and [live-feedback implementation](docs/live-feedback-implementation-plan.md) for the recognizer. Updating Python does not update an installed daemon or Tauri binary.

## Optional separate analysis host

Install `backend/requirements-worker.txt` into the worker HTTP environment and the model requirements into its separate model environment on another host. Use `backend/deploy/analysis-worker.env.example`, then run `python analysis_worker.py` from `backend/` behind HTTPS with a timeout suitable for inference. Set the Pi's `CHORDSENSE_ANALYSIS_URL` to its `/analyze` endpoint and match `CHORDSENSE_ANALYSIS_TOKEN` to the worker secret.

The worker serializes inference, caches results and deletes temporary inputs. The Pi streams files from disk. Configured worker failures preserve recordings and report failure without silently running expensive local inference. Leave worker configuration unset for local offline analysis; cached results remain useful. The companion website does not expose the worker or analysis jobs.

## Local verification

Use an intact backend environment with declared dependencies:

```sh
cd backend
.venv/bin/python -m unittest discover -s tests
```

Run `npm test` in `web/` and `device-ui/`, and `cargo test --offline` in `iod/` when dependencies are cached. ADC tests use a mock reader rather than hardware. The [deployment record](docs/pi-pairing-deployment.md) records actual Pi backend tests and desktop Chrome LAN transfers. Android, native pairing-button input, capture pitch/duration, DAC output, live feedback and thermal performance require further device validation.
