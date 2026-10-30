# ChordSense native device interface

`device-ui` is a native Rust desktop application built with eframe/egui. It draws its interface directly and uses the existing chord diagrams, icons and Rakkas typeface. It does not load a web page, embed a webview, execute JavaScript, or require Node/npm. The separate `web/` companion website is unchanged.

The Python backend is still required at `http://127.0.0.1:5051`. Its API, model environments, recording library and upload pairing protocol remain the same. Windows/macOS playback uses the computer's audio output. On Linux/Pi, audio playback, recording and live input use the existing `iod` service. The **Tune** mode reads the same guitar input through the Python tuner API. Recording inference still needs the configured backend model/hardware; this migration does not turn a Windows microphone into the Pi capture device.

## Prerequisites

- A current Rust toolchain and Cargo.
- Windows: the MSVC Rust toolchain and Microsoft C++ Build Tools with **Desktop development with C++**. WebView2 is not needed by this native application.
- Linux: a working desktop session and the native window/audio build libraries for your distribution. On Debian/Ubuntu these commonly include `build-essential`, `pkg-config`, `libasound2-dev`, `libudev-dev`, `libx11-dev`, `libxi-dev`, `libxcursor-dev`, `libxrandr-dev`, `libxinerama-dev`, `libegl1-mesa-dev` and `libwayland-dev`.
- FFmpeg in PATH for desktop playback below normal speed while preserving pitch.
- The existing backend environment; the repository's installation helper currently requires Python 3.12.

## Initial setup

From the repository root:

```powershell
python setup.py
```

The helper initializes the model submodule, installs the backend/model Python dependencies, applies the existing model compatibility patch, checks backend imports, and builds the native interface. It starts `serve.py` with the backend virtual environment's interpreter, waits for a successful health response, then opens ChordSense. Closing the interface stops only the backend started by this helper. An already-running healthy ChordSense backend is reused and is left running.

Use `python setup.py --no-launch` to install/build only, or `python setup.py --windowed` for a window instead of fullscreen. Startup logs from a backend launched by the helper go to `runtime/startup.log`. Setup does not configure the Pi's `iod` service, Hailo runtime, companion discovery credentials, or the separate website.

## Subsequent launches

Open two terminals at the repository root. On Windows, start the backend with:

```powershell
.\backend\.venv\Scripts\python.exe backend\serve.py
```

On Linux use:

```sh
backend/.venv/bin/python backend/serve.py
```

In the other terminal:

```sh
cargo run --locked --manifest-path device-ui/Cargo.toml
```

For a windowed development preview:

```sh
cargo run --locked --manifest-path device-ui/Cargo.toml -- --windowed
```

The app is fullscreen by default. The backend health endpoint is `http://127.0.0.1:5051/health`.

Desktop playback follows the operating system's default audio output. While a song is loaded, the player checks for changes every 500 ms and transfers playback to the new output, preserving position, volume, speed, and paused state. If the device is temporarily unavailable, it retries automatically. Alternate audio formats are tried only on the selected default device.

The toolbar brightness slider controls the panel through Linux's `/sys/class/backlight` interface. It starts at the current panel setting and keeps adjustments above zero so the screen remains visible. The app user needs write access to the panel's `brightness` file (the Pi uses the `video` group). Displays without this interface show a disabled control. Audio uses full software volume for adjustment with the hardware pot; `--mute` still silences preview playback.

## Build and test

```sh
cargo test --locked --manifest-path device-ui/Cargo.toml
cargo build --release --locked --manifest-path device-ui/Cargo.toml
```

With Cargo's default target directory the executable is `device-ui/target/release/device-ui.exe` on Windows and `device-ui/target/release/device-ui` on Linux. Build the Linux/Pi artifact on an appropriate Linux toolchain before replacing a Pi screen launcher. Any service that previously referred to `src-tauri/target/...` must be updated to the native executable path.

The backend stays a separate service. Unit tests and a Windows preview do not establish that ADC capture, DAC audio, live recognition and touchscreen behavior work on the Pi; use the migration checklist for that device validation.

For a developer preview using existing analysis, append `-- --windowed --mute --audio /absolute/path/song.mp3 --lab /absolute/path/song.lab` to the Cargo run command. Omit `--mute` for normal listening.

## Analyze songs and tune your guitar

After loading audio without chords, the chord area says **Song not analyzed yet** and offers **Analyze song**. The toolbar highlights **Analyze** in green with an outline on both compact and large screens. Either action pauses an active preview and starts analysis; the prompt shows progress until chords are ready. Songs with saved analysis display their chords immediately. If analysis finishes without chords, the prompt says **No chords found**.

Select **Tune** beside Play Along and Record to start the guitar tuner. The native Rust screen shows the note/octave, frequency, cents meter, and flat/sharp guidance. Green indicates ±3 cents; “In tune” requires 500 ms in that band. Readings disappear after 600 ms without a clear note. Silence, clipping and connection failures have separate messages, with Retry for unavailable input.

The tuner uses standard guitar tuning (E A D G B E) with a fixed A4 reference of 440 Hz. Select a string to constrain pitch detection, or **Auto** for automatic note detection. There are no alternate tuning or reference-pitch controls. Leaving Tune or closing the app releases its input stream; recording and feedback retain their existing lifecycle. The tuner runs entirely in Rust/Python with no JavaScript, Tauri or webview.

Tuner backend files (`live_input_session.py`, `pitch_detection.py`, `tuner_session.py`, the updated `live_feedback_session.py` and `app.py`) must be deployed together and the API restarted alongside the rebuilt native UI. Synthetic pitch tests cover open strings, detuning, noise and weak fundamentals; physical accuracy still needs comparison with a reference tuner on the connected guitar.
