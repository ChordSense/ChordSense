# Native Rust migration verification

This change replaces the device interface's JavaScript/Tauri webview with native Rust. It retains the Python backend API and leaves the companion website in `web/` alone. Native font rendering and input behavior differ from browser rendering; exact visual and interaction equivalence must be established by comparing real screens and running the checks below. This document is a verification checklist, not a claim that every hardware check has passed.

## Appearance

Compare screenshots at the actual display resolutions used on Windows and the Pi. Check the cream background, Rakkas headings, black controls, header tabs, ChordSense badge, spacing, current-chord halo/timer, next/later diagrams, focus layout, library and dialogs. Exercise long song titles, unsupported chord labels, empty libraries, narrow windows, touch input, DPI scaling, errors, countdowns and end-of-song results.

## Behavior carried by the old interface

- Play Along and Record mode tabs; M shortcut; mode changes blocked during countdown, starting, recording and processing. Release playback/live input before Record claims capture.
- Browse uploaded songs and saved recordings; loading/empty/error states; native local-file picker; scrolling by wheel/touch and buttons; selection followed by Load/Rename; recording deletion with confirmation.
- Rename existing uploads/recordings with an 80-character title limit, trimmed nonempty validation, save errors and a touchscreen keyboard. Preserve Unicode and editing selection; protect in-flight saves.
- Load local audio or an existing upload; analyze with the submission chord dictionary; reuse saved recording analysis. Ignore stale responses after selecting a different track.
- Play/pause, stop/reset, seek, time display, volume, automatic end handling and speed from 0.50 to 1.00 in 0.05 increments. Slow playback preserves pitch and the original-song chord timeline.
- Current/next/later chord diagrams with unsupported-name fallback, distinct upcoming chord changes and progress halo. Capo frets 0–11, reset, and the existing recommendation heuristic (recommendations 0–7).
- Live feedback start/pause/resume/stop and event streaming, session validation, timestamp alignment, thresholds/hysteresis, rating overlay and accumulated score. End-of-song result supports replay and returning to the song.
- Four-second recording countdown, cancellation before capture, single start request, elapsed timer, processing lock, library-full/error states and loading the completed take back into Play Along.
- Pair uploads automatically generates a code. Show the website, six-digit code and expiry countdown. Poll pending pairing every two seconds, regenerate, cancel, and disconnect all. Closing must cancel an unused code, including when close happens before the start response arrives; it must not revoke already-paired browsers. Ignore superseded status responses.
- Closing the app releases any owned live-feedback session and playback resources. No duplicate backend process is created by the setup helper.

## Validation boundaries

Rust unit tests can check chord/capo rules, feedback decisions, response handling and timing transitions. A local mock API can check UI requests and error states without modifying the real song library. Windows runtime checks cover native drawing, dialogs and desktop playback. Actual Pi tests remain necessary for `iod`, ADC/DAC, capture/playback contention, live recognition, physical touchscreen interaction and deployment service paths.

For the JavaScript removal requirement, inspect the delivered `device-ui` source and build instructions: there should be no `.js`/`.mjs`/`.cjs` files, Node package manifests, Tauri runtime or embedded browser. Keep any migration backup outside `device-ui`. Target-specific JavaScript-related packages in a Cargo lockfile do not establish use in the native executable; inspect the active native dependency tree as well.

## Checks completed on Windows, 2026-10-28

- Cargo build and 31 Rust regression tests passed.
- Setup helper syntax and six mocked startup checks passed.
- Native app launched; sample MP3 and saved LAB loaded, diagrams rendered, muted playback, pause, seek to 60 seconds, focus view and 0.75x tempo verified.
- Active Windows Cargo dependency tree contains no Tauri, Wry, WebView, JS engine, js-sys or wasm-bindgen.
- Delivered source contains no JavaScript, HTML, CSS, npm manifest or Tauri project.
- Real Pi capture/live feedback, touchscreen and Linux deployment remain unverified. Native rendering is not established as pixel-identical to the former browser UI.
