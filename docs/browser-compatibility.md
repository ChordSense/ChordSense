# Observed browser compatibility

The companion website has no browser-brand, version or iOS restriction. Every browser can submit a pairing code and attempt the real connection. Failed requests receive browser-neutral connection/permission guidance; optional permission queries run only after a network failure and tolerate unsupported permission APIs.

## Test environment

Tests on 2026-10-11 used the actual public production website, [chordsense-phi.vercel.app](https://chordsense-phi.vercel.app), deployment `dpl_8JHjjeQxTQXPkALvcVqTwUPGzpi2`. The configured device API was `http://10.136.137.225:5051`, the Pi's private address on campus Wi-Fi. This tests an HTTPS website calling an HTTP LAN API.

Browser tests used installed stock browsers and isolated test sessions/profiles. Normal local-network permission was granted where supported. Mixed-content/security checks were not disabled, user-agent strings were not spoofed, and device requests were not mocked. Results apply to these versions and this connection setup; they are not a general statement that a browser cannot access LAN devices.

**Mac permission limitation:** automated Chrome was launched by a command-line process and its website permission was granted through the test driver. This validates the transfer workflow after access is allowed, but does not verify permissions for Chrome opened normally. macOS local-network privacy can treat command-line tools and their child processes differently from the normal browser app. On a Mac, check both the website's local-network permission in Chrome and **System Settings → Privacy & Security → Local Network → Google Chrome**. [Apple local-network privacy documentation](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy)

## Results

| Browser | Installed version | Observed result |
| --- | --- | --- |
| Chrome on macOS | 155.0.8059.39 | Passed pairing, authenticated health/library, upload, byte-for-byte download, UI deletion and Disconnect. |
| Firefox on macOS | 157.0.1 | Pairing failed: the browser blocked the HTTP claim as mixed active content despite local-network permission being granted. No device HTTP response, token or uploaded file was created. |
| Safari on macOS | 26.5 | Pairing failed in an isolated Safari Automation window: the claim fetch rejected with `TypeError: Load failed`; no CSP violation was recorded. The error does not establish which browser/network restriction caused it. |
| Edge, Brave, Opera and other desktop browsers | Not installed for these tests | Not tested; allowed to attempt connection. |
| Android and iPhone/iPad browsers | No device available for these tests | Not tested; allowed to attempt connection. |

Chrome's test uploaded one uniquely named 4,454-byte silent WAV, downloaded identical bytes, deleted only that file and revoked only its own session. Its temporary browser profile was removed. After Firefox's failed attempt, the device had no pending pairing window or sessions; the temporary code file was removed without any global session revocation.

Firefox's actual console error was `Blocked loading mixed active content` for the device's `/api/pairing/claim` request. The app then displayed connection guidance including `Your browser may be blocking the device's HTTP connection.` The fresh profile had no `user.js` and no explicit false overrides for mixed-content or local-network protection preferences. Its test browser and profile were removed.

Safari's pairing form remained **Not paired** and displayed the same browser-neutral connection guidance. The normal request was attempted and rejected; the browser-brand gate did not intervene. Remote Automation was enabled temporarily with user approval for this test, after observing the checkbox disabled. Its owned automation session and driver were closed after the test, and the checkbox was restored and read back as disabled. No other Safari security setting was changed.

The Pi confirmed Safari's code was still pending with all five attempts remaining and zero sessions, so no claim reached it. Only that unused test window was cancelled, and the private code file was removed. Final device state was zero sessions and no pending pairing window.

## Normal Chrome on this Mac: follow-up diagnosis

During the 2026-10-11 investigation, the user verified both permission settings but normal Chrome still reported `net::ERR_ADDRESS_UNREACHABLE` for the device's claim endpoint. A direct command-line GET of `http://10.136.137.225:5051/health` returned HTTP 403, the expected response without an allowed website Origin. The Mac's route used Wi-Fi interface `en0`; its address was `10.136.245.197`, which the Pi saw as a reachable neighbor. The Pi's address, listener and services were unchanged at that time. These checks established direct Mac-to-Pi reachability but did not establish normal Chrome's app-specific access.

A similar [Chromium issue](https://issues.chromium.org/issues/509555633) reports this behavior on macOS with permissions enabled and recovery after Chrome restart. That was a possible explanation, not a confirmed diagnosis for this Mac. It should not be used to explain a later failure without checking the endpoint again.

## Campus endpoint diagnosis on 2026-10-15

During diagnosis, the deployed website still targeted `http://10.136.137.225:5051`, but DHCP had moved the Pi's `wlan0` address to `10.136.68.97/16`. The old endpoint timed out. The current endpoint answered HTTP 403 from the same Mac (`10.136.98.125`), and the existing API was listening on `0.0.0.0:5051`. `.local` did not resolve. This confirms stale website configuration; it does not establish Wi-Fi client isolation or a new Chrome permission failure.

Administrative checks also confirmed that the existing API was reachable and accepted the configured website Origin/preflight. These were read-only network checks, not browser pairing/transfer tests. No Pi application changes, starts or restarts were made for this fix. Tailscale is used only for administrative SSH; it is not a supported upload transport or an end-user prerequisite.

The new preparation flow reads the Pi's current `wlan0` LAN address over existing administrative SSH, checks LAN API reachability and builds the browser configuration and exact CSP together:

```sh
node web/scripts/prepare-device.mjs --ssh chordsense@chordsense
```

`--network lan` is optional and is the only supported mode. Administrative SSH may use Tailscale; the website still uses the Wi-Fi address. End users join the same reachable Wi-Fi and enter six digits without installing Tailscale. Repeat preparation and deployment after DHCP changes unless stable LAN DNS or a DHCP reservation is available. A short code does not discover a replacement endpoint; no code-to-LAN-address directory is implemented. Browser local-network permissions still apply.

The build retains `web/dist` and emits `.vercel/output` with matching configuration and security headers. A separate `npx vercel@latest deploy --prebuilt --prod` publishes that artifact; preparation does not deploy. See the [dated deployment record](pi-pairing-deployment.md) for the last recorded installation and transfer test. New endpoint/build changes require browser verification after deployment; the October 11 results do not validate those changes. See [connection and deployment instructions](web-device-architecture.md#connection-options). [Vercel Build Output API](https://vercel.com/docs/build-output-api)

The corrected Wi-Fi-only website was redeployed on 2026-10-15 as `dpl_4Y4H2siygC2z9eZZd6vbYVBeZgAx`, aliased to [chordsense-phi.vercel.app](https://chordsense-phi.vercel.app). Anonymous requests to `/`, `/upload`, `/upload/`, `config.js` and the connection scripts returned HTTP 200. Live configuration selected `http://10.136.68.97:5051`; every response's CSP allowed exactly that device origin, and `config.js` used `Cache-Control: no-store`. Published connection scripts contained no Tailscale endpoint or user instructions. All 38 local web tests passed, including rejection of administrative Tailscale endpoints at build/client creation. LAN health and pairing preflight returned the expected HTTP 401/204 with the production Origin. These checks validate deployment/configuration and API reachability, not a new Chrome pairing/upload cycle. No Pi application source, service or process was changed or restarted. Reload the website before pairing with a fresh code.

## Automatic discovery implementation (activation pending)

Later work on 2026-10-15 replaced the build-time device address with an HTTPS directory lookup. The Pi publishes its current `wlan0` address when a pairing window starts; the browser resolves the code and verifies a fresh device proof before claiming directly over Wi-Fi. Runtime page responses allow exactly the discovered address in CSP. No Tailscale client, mDNS, IP entry, subnet scan or website rebuild is required after a DHCP change; show a fresh code and re-pair.

Local verification covers the Python pairing manager, browser HMAC verification and encrypted page context together, including a simulated LAN address change without rebuilding. Independent tabs retain their own tokens at the same authenticated device/address; address or publisher-identity changes reject the previous token. Redis scripts were executed against a local Lua command model. These are local checks, not a live Upstash or browser/device discovery test.

Activation is pending the account owner's Upstash Marketplace terms acceptance, free-store provisioning, runtime secrets, cloud deployment and a backend-only Pi update/API restart. The working Wi-Fi deployment above remains the last verified production deployment. The attached-screen application needs no rebuild. See the [activation guide](web-device-architecture.md#vercel-setup-from-the-local-working-directory); do not treat earlier fixed-address browser results as verification of automatic discovery.

## Manual test on another browser or device

1. Verify the website's configured LAN endpoint is current and reachable. Join the same reachable Wi-Fi/LAN as the Pi, then open the production website. Tailscale is not required.
2. Tap **Pair uploads** on the Pi, enter all six digits and allow local-network access if asked.
   On macOS, also allow the browser app under **System Settings → Privacy & Security → Local Network**. Use the stable website address above; generated deployment origins must be separately allowed by the device configuration.
3. Upload a small test song, download it, delete only that test song and select **Disconnect**.
4. Record the browser version, operating system, endpoint configuration and the first failed step. Check the browser console for a networking/mixed-content or CSP error if pairing fails. In normal Chrome, visiting the current `<configured-api-origin>/health` directly should return a backend error/HTTP 403 without an Origin header; an unreachable error is below the pairing layer. A command-line response does not establish normal Chrome's separate permission.

Do not disable browser security settings to claim compatibility. LAN reachability and the browser's permission/security behavior must both be verified.
