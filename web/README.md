# ChordSense companion website

This static website uploads, lists, downloads and deletes songs on one ChordSense device. Play Along, Record and live feedback remain in the Pi's attached-screen Tauri app.

## Connect with a short code

Use your browser with the browser and Pi on the same reachable Wi-Fi/LAN. Every browser can attempt pairing. The website knows its configured API address; users enter no device URL or long token. The source default is `http://chordsense.local:5051`; the current campus deployment uses the Pi's private LAN IP because this network did not resolve its `.local` name.

1. Tap **Pair uploads** on the updated Pi screen; a code is generated automatically.
2. Open the displayed website, enter all six digits and select **Pair device**.
3. Allow local-network access if your browser asks and manage the song library.

Each code lasts 120 seconds, works once and closes after five wrong attempts. A successful claim creates an eight-hour upload-only session stored in the current tab's `sessionStorage`. Disconnect revokes that tab's session; the Pi's **Disconnect all** revokes every session. API restarts also require pairing again.

Browser brands and versions are not filtered. HTTPS-to-local-HTTP support varies, and a failed request shows connection or permission guidance after the browser attempts it. See [observed browser test results](../docs/browser-compatibility.md). Guest Wi-Fi/client isolation or blocked mDNS can prevent connection even on the same Wi-Fi name. This is a trusted-network prototype: HTTP does not encrypt the code, session token or audio. Vercel serves the website over HTTPS; song transfers go directly across the LAN. [Local Network Access](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Local_network_access)

Choose or drop a WAV, MP3, OGG, FLAC or M4A file comfortably below the 95 MiB request limit. Upload progress shows transfer to the device. Refresh is on demand; there is no background polling. Changing or disconnecting a session is blocked while a transfer is active.

## Local development

Node.js is needed to build/test; the website has no packages to install or server-side Vercel functions.

```sh
npm --prefix web run dev       # http://127.0.0.1:5173
npm --prefix web test
npm --prefix web run build     # web/dist
```

Add `http://127.0.0.1:5173` to the device's exact `CHORDSENSE_ALLOWED_ORIGINS` when testing that origin. The backend must already be running and the native screen must generate the code.

## Vercel deployment

From the repository root, run `npx vercel@latest login` and `npx vercel@latest link`; create/select the upload project and choose `./`. Leave dashboard **Root Directory blank** and use root [vercel.json](../vercel.json): Framework Other, build `node web/scripts/build.mjs`, output `web/dist` and the configured no-package install command.

Set the public Production variable `CHORDSENSE_API_URL=http://chordsense.local:5051` to record the device endpoint explicitly; it is also the website default. Keep the root [.vercelignore](../.vercelignore) allowlist, then run `npx vercel@latest deploy --prod`. Both `/` and `/upload` open the song manager. CLI deployment uses local working files without a Git commit. Codes, session tokens and worker secrets must never become frontend build variables.

For the current campus deployment, retain the private-IP build override instead:

```sh
npx vercel@latest deploy --prod --build-env CHORDSENSE_API_URL=http://10.136.137.225:5051
```

RFC1918 private IPv4 addresses on port 5051 are supported. The root `vercel.json` CSP must permit the exact configured address. If DHCP changes the Pi's IP, update both that CSP entry and the build override before redeploying. See the [verified deployment record](../docs/pi-pairing-deployment.md) for the public website, running Pi services and completed browser test.

Set the stable production origin as both `CHORDSENSE_ALLOWED_ORIGINS` and `CHORDSENSE_COMPANION_URL` on the Pi. Install the matching backend and rebuilt native screen app. See [the complete setup guide](../docs/web-device-architecture.md) and [Pi deployment steps](../backend/deploy/README.md).
