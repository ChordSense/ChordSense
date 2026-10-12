# ChordSense

ChordSense combines a local Pi device interface with a separate companion website:

- **Attached-screen app:** `device-ui/` provides Play Along, Record, chord diagrams, playback controls and live feedback. Its native bridge calls `127.0.0.1:5051`.
- **Companion website:** `web/` is the static Vercel song manager. A computer or phone on the same reachable LAN pairs with a short code and uploads, lists, downloads or deletes songs.
- **Backend and hardware:** `backend/` retains API port **5051**; Rust `iod` owns ADC recording and DAC playback. Browser permissions cover pairing and the upload library only.
- **Optional analysis worker:** `backend/analysis_worker.py` can move whole-song/recording inference to another host. Live feedback remains local.

The companion replaces the Flask-rendered **`/upload` page formerly served on port 5051**. Vercel serves `/` and `/upload`; it does not host the Pi screen or proxy audio. The native screen retains bundled assets and offline device use.

## Pair a laptop or phone

The website uses a configured Pi address, defaulting to `http://chordsense.local:5051`. The current campus deployment uses the Pi's private LAN IP because `.local` resolution failed on that network. Tap **Pair uploads** on the updated Pi screen to generate a six-digit code, open the displayed website, enter the code and select **Pair device**. Allow local-network access if your browser asks. No device URL or long token needs to be typed.

Codes last 120 seconds, work once and close after five wrong attempts. A successful claim creates an eight-hour upload-only session for the current browser tab. Closing the Pi pairing dialog cancels an unused code; **Disconnect all** revokes every browser session. API restarts require pairing again.

The website lets every browser attempt a connection. HTTPS-to-local-HTTP behavior varies by browser, so report observed results rather than excluding browser brands. Wi-Fi must allow peer communication; the default `.local` endpoint additionally requires Avahi/mDNS. Guest Wi-Fi/client isolation can block access. This is a trusted-network prototype: HTTP does not encrypt codes, tokens or audio. See [browser test results](docs/browser-compatibility.md).

See [the architecture and Vercel guide](docs/web-device-architecture.md), [Pi deployment steps](backend/deploy/README.md), and [verified deployment record](docs/pi-pairing-deployment.md). The matching backend **and rebuilt native screen app** are required; publishing the website alone does not update an installed Pi app. The current website and Pi are deployed, with the actual browser pairing/upload/download/delete/disconnect cycle verified.

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

Configure the exact Vercel production origin in `CHORDSENSE_ALLOWED_ORIGINS`, and set `CHORDSENSE_COMPANION_URL` to that public website address displayed by the Pi. The API listens on `0.0.0.0:5051` for LAN uploads; its native bridge still uses loopback. Pairing issues browser tokens automatically; there is no shared device token.

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

The local native folder is now flat and named `device-ui/`, with a `device-ui` executable. The stopped Pi has not received this reorganization or rename; its installed binary and UI service still use the prior layout recorded in [the deployment record](docs/pi-pairing-deployment.md). A future device update must copy/rebuild `device-ui/` and adjust that service's `WorkingDirectory` and `ExecStart` before restarting it.

Start the matching backend separately with `python serve.py` from its configured environment, or restart its existing configured service. Use one API process; native hardware/session ownership is process-local. Follow [backend/deploy/README.md](backend/deploy/README.md) for environment loading, hostname setup and both startup steps. Remote companion sessions cannot record, play, seek, run live feedback or submit analysis jobs.

## Companion development and Vercel

From the repository root:

```sh
npm --prefix web run dev
npm --prefix web test
npm --prefix web run build
```

Open `http://127.0.0.1:5173/` and add that exact development origin to the backend allowlist when testing. Generate a pairing code through the native app.

Vercel uses the **repository root**: leave Root Directory blank, Framework Other, build `node web/scripts/build.mjs`, output `web/dist`. The root [vercel.json](vercel.json) supplies those settings and the root [.vercelignore](.vercelignore) uploads only website source/configuration, excluding backend/models/data/environments. The public `CHORDSENSE_API_URL=http://chordsense.local:5051` may be set explicitly; it is also the default.

With a Vercel account, run `npx vercel@latest login`, `npx vercel@latest link` (project directory `./`), configure Production settings/environment, then `npx vercel@latest deploy --prod`. This uses current local files without staging or committing. Use the resulting stable production origin on the Pi; keep codes, session tokens and worker secrets out of frontend source/build variables.

For the current campus configuration, use `npx vercel@latest deploy --prod --build-env CHORDSENSE_API_URL=http://10.136.137.225:5051`. Its exact private-IP endpoint is allowed in `vercel.json`; if DHCP changes that address, update the CSP and build override before redeploying. The source default remains `.local` for networks where mDNS works.

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
