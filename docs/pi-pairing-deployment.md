# Pi pairing deployment — 2026-10-11

The source prepared for the pairing deployment was copied to the Pi through its Tailscale SSH name `chordsense`. The API and native screen application were built/installed for testing. No Git changes were staged or committed on either machine.

## Installed state

- Checkout: `/home/chordsense/workspace/ChordSense`. The Pi remains on `tc/UI-fix` at `5c6d767`; deployment used the laptop's uncommitted source, without moving the Pi's Git branch.
- Backup: `/home/chordsense/deployment-backups/web-separation-20261011-6bc11uhg/before-update.tar.gz` contains replaced source and previous native/I/O binaries.
- Existing models, virtual environments, recordings, runtime files, and model-repository edits were preserved. 149 source files were verified after copying.
- Waitress 3.0.2 was added without dependency upgrades to the existing `backend/.venv`, which retains system Hailo bindings.
- Native ARM64 executable: `frontend-tauri/frontend-tauri/src-tauri/target/release/frontend-tauri`. Build completed with two Cargo jobs and no packaging step.
- The I/O daemon's source already matched the laptop, so its existing binary was reused.

## Later local native-app reorganization

The laptop repository now contains the native app directly in `device-ui/`, with a `device-ui` executable. This folder flattening and rename have not been copied to the stopped Pi. Its installed executable and `chordsense-ui.service` still refer to the nested layout recorded above; that historical executable path remains accurate for the device.

For a future migration, copy the native source into the Pi checkout's `device-ui/` and rebuild there. Update the existing user UI unit's `WorkingDirectory` to `/home/chordsense/workspace/ChordSense/device-ui` and `ExecStart` to `/home/chordsense/workspace/ChordSense/device-ui/src-tauri/target/release/device-ui` before reloading/restarting it. Preserve the unit's existing desktop environment and `WEBKIT_DISABLE_DMABUF_RENDERER=1`. The [device update guide](../backend/deploy/README.md) uses the new paths and executable name for future copies/builds; no Pi file, binary or service was changed by this local reorganization.

## Running services

| Service | Scope | Configuration |
| --- | --- | --- |
| `chordsense-api.service` | System | `/etc/systemd/system/chordsense-api.service`, `/etc/chordsense/api.env` |
| `chordsense-iod.service` | System | Existing device I/O service |
| `avahi-daemon.service` | System | Advertises `chordsense.local` |
| `chordsense-ui.service` | User `chordsense` | `/home/chordsense/.config/systemd/user/chordsense-ui.service` |

All four were active and enabled at verification. The user UI unit contains `Environment=WEBKIT_DISABLE_DMABUF_RENDERER=1`: the default WebKit buffer path produced corrupted output on this device, and that setting restored a clear screen. Accelerated compositing was not explicitly disabled. This is a runtime setting, not a change to the native source. [Tauri graphics troubleshooting](https://v2.tauri.app/develop/debug/linux-graphics/)

During the later browser compatibility checks, the Pi had rebooted and its desktop/display manager was inactive, leaving the user UI unit retrying. Starting the existing `display-manager.service` restored LightDM, labwc, the Wayland socket and the native screen. No startup configuration or API restart was needed; the running UI was stable afterward.

From a terminal in the Pi desktop session:

```sh
systemctl status chordsense-api.service chordsense-iod.service avahi-daemon.service --no-pager
systemctl --user status chordsense-ui.service --no-pager
```

## Website and pairing configuration

The verified public production website is [chordsense-phi.vercel.app](https://chordsense-phi.vercel.app). Its `/` and `/upload` routes returned HTTP 200 anonymously. The current production build's `config.js` points to `http://10.136.137.225:5051`, the Pi's private campus LAN address. Its Content Security Policy permits that exact endpoint.

The production deployment is `dpl_8JHjjeQxTQXPkALvcVqTwUPGzpi2`, including removal of browser-brand/version/iOS restrictions. The stable website address above follows the production deployment; the generated deployment URL is not the address displayed on the Pi. See [browser test results](browser-compatibility.md).

The device configuration contains:

```dotenv
CHORDSENSE_API_HOST=0.0.0.0
CHORDSENSE_API_PORT=5051
CHORDSENSE_ALLOWED_ORIGINS=https://chordsense-phi.vercel.app,https://chordsense-thomas11-e47b.vercel.app
CHORDSENSE_COMPANION_URL=https://chordsense-phi.vercel.app
```

No separate analysis worker was configured. Live feedback, playback, and uncached inference retain their local device paths.

## Validation and campus network configuration

- All 82 backend tests passed in the Pi's actual Python environment.
- Live API checks verified short-code claims, one-use rejection, upload-only permissions, exact website-origin checks, and session revocation. Test codes and sessions were cleared.
- A fresh Chrome 155 session on the actual public production website completed code pairing, authenticated health/library requests, upload (HTTP 201), an exact byte-for-byte 4,454-byte WAV download, UI-confirmed deletion, and UI Disconnect. Local-network permission was granted without browser security bypasses. The owned test file, session, browser profile and temporary code file were removed; the Pi afterward had zero sessions and no pending code.
- The rebuilt screen app was launched and its Play Along view and **Pair uploads** button were visually verified. No synthetic input tool was installed, so an actual native pairing-button click remains a manual check.
- No guitar capture, DAC playback, recognition-quality, or inference test was performed. No thermal benchmark was performed.

The Pi is on `eduroam` at LAN IP `10.136.137.225`. Avahi advertises `chordsense.local`, and the name resolves on the Pi. The laptop can reach the API through the private LAN IP.

However, this laptop and a fresh Chrome session could not resolve `chordsense.local`. Chrome's local-network permission was granted; its request failed with `ERR_NAME_NOT_RESOLVED` before reaching the API. Campus mDNS filtering or network segmentation is a possible explanation, not a confirmed diagnosis. Tailscale SSH access does not establish mDNS reachability.

The website now supports a configured RFC1918 private IPv4 endpoint on port 5051. The campus production deployment uses this override, preserving the six-digit-only user flow without requiring `.local` resolution. The source default remains `http://chordsense.local:5051` for networks where mDNS works.

For the next deployment on the current campus network, run from the laptop repository root:

```sh
npx vercel@latest deploy --prod --build-env CHORDSENSE_API_URL=http://10.136.137.225:5051
```

This override belongs to the build, not to a secret or browser pairing code. A plain deployment without this override uses the project environment variable or source default, so retain the override while testing on this network. No static address or DHCP reservation was configured. If the Pi's address changes, update the exact private-IP entries in the root `vercel.json` CSP and the build override, then redeploy. A new network may instead support the default `.local` address.

For manual testing, open the production website in your browser on the same reachable network, tap **Pair uploads** on the Pi, enter the six-digit code, and allow local-network access if prompted. Upload a small song and confirm it appears in **Browse Tracks** on the Pi. All browsers can attempt connection; see [observed browser results](browser-compatibility.md). The native button click and normal audio/model functions still need manual testing.

For a later custom website domain, add its exact HTTPS origin to `CHORDSENSE_ALLOWED_ORIGINS`, update `CHORDSENSE_COMPANION_URL`, and restart `chordsense-api.service`. Existing browser sessions will require pairing again.
