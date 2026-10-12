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

The user verified both permission settings but normal Chrome still reported `net::ERR_ADDRESS_UNREACHABLE` for the device's claim endpoint. A direct command-line GET of `http://10.136.137.225:5051/health` returned HTTP 403, the expected response without an allowed website Origin. The Mac's route uses its Wi-Fi interface `en0`; its address is `10.136.245.197`, which the Pi sees as a reachable neighbor. The Pi's address, listener and services were unchanged. These checks establish direct Mac-to-Pi reachability but do not establish normal Chrome's app-specific access.

The next narrow manual check is to fully quit Chrome with Command-Q, reopen it normally from the Dock/Applications, and visit that HTTP health address directly. A backend JSON error/HTTP 403 confirms browser reachability; another unreachable-address error keeps the investigation below the Vercel/pairing layer. A similar [Chromium issue](https://issues.chromium.org/issues/509555633) reports this behavior on macOS with permissions enabled and recovery after Chrome restart; it is a possible explanation, not a confirmed diagnosis for this Mac.

## Manual test on another browser or device

1. Join the same reachable network as the Pi and open the production website.
2. Tap **Pair uploads** on the Pi, enter all six digits and allow local-network access if asked.
   On macOS, also allow the browser app under **System Settings → Privacy & Security → Local Network**. Use the stable website address above; generated deployment origins must be separately allowed by the device configuration.
3. Upload a small test song, download it, delete only that test song and select **Disconnect**.
4. Record the browser version, operating system, endpoint configuration and the first failed step. Check the browser console for a networking/mixed-content error if pairing fails.

Do not disable browser security settings to claim compatibility. A trusted HTTPS device endpoint or HTTPS relay would change the connection being tested and could avoid this particular HTTPS-to-HTTP failure.
