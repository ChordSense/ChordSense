# ChordSense attached-screen device app

This directory contains the **Tauri app that runs on the Pi's attached screen**. It provides Play Along, Record, synchronized chord diagrams, playback controls and live feedback. It remains part of the device and is not the Vercel website.

Tauri loads local assets from `src/` (`src-tauri/tauri.conf.json` sets `frontendDist` to `../src`). The native Rust bridge connects to the Pi backend at `http://127.0.0.1:5051`. The backend talks to `iod` over its local Unix socket for ADC capture and DAC playback. These local paths continue working without Vercel or an Internet connection; optional remote analysis requires its worker online, while cached results and unconfigured local analysis support offline use.

With the existing Rust/Tauri and system dependencies installed, use this directory's Tauri command:

```sh
npm run tauri dev
npm test
```

For a packaged device build, use `npm run tauri build` and the existing Pi deployment process. The repository keeps this app in `device-ui/`, named for its role on the device; Tauri provides its native window and Rust bridge. The built executable is `src-tauri/target/release/device-ui`, and the packaged application is named **ChordSense**. The Pi's installed executable and service still use the older nested checkout path until a separate device migration; see the [deployment record](../docs/pi-pairing-deployment.md). Its UI user service sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` to correct the observed Pi display corruption.

Install the updated backend and rebuilt Linux/Pi screen app together to use short-code pairing. Tap **Pair uploads** in the screen header to generate a six-digit code automatically; the dialog shows the configured companion website and countdown. The user enters that code in the website's **Pair device** form. **Generate new code** replaces an expired/locked code, **Cancel pairing** invalidates a pending code, and **Disconnect all** revokes all browser sessions. Closing the dialog cancels an unused code without disconnecting already paired browsers. Native status checks run every two seconds only while the open panel has a pending code.

Set the public `CHORDSENSE_COMPANION_URL` to the stable Vercel origin shown in the dialog, and put the same exact origin in `CHORDSENSE_ALLOWED_ORIGINS`. Pairing start/status/cancel/revoke calls remain on the native loopback bridge. The companion uses its configured LAN endpoint, defaulting to `http://chordsense.local:5051`, and permits every browser to attempt a connection. Allow local-network access if prompted; see [observed browser tests](../docs/browser-compatibility.md). The current campus website uses a configured private IP after `.local` resolution failed. See [backend deployment](../backend/deploy/README.md) for hostname/Avahi, environment and both API/native startup steps.

Pi playback stays on the device DAC/headphone output. The macOS preview can use its local playback implementation for development. Screen visual updates stop while paused or hidden, and the backend serializes playback-speed rendering to avoid overlapping FFmpeg work. Publishing the companion website does not offload the attached screen's rendering or live-recognition work.

Uploaded-song analysis now passes the filename to local `/analyze_uploaded`; the native bridge no longer downloads and re-uploads audio that is already stored on the device.

The separate song-manager website lives in [web/](../web/), with its architecture described in [web-device-architecture.md](../docs/web-device-architecture.md). That website replaces the Flask `/upload` page formerly served on port 5051. Its browser only uploads, lists, downloads and deletes songs; it does not replace this interface or expose Record/Play Along controls. Vercel builds `web/` only, using the repository-root configuration.
