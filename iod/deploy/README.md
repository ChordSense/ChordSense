# Deploying and using `chordsense-iod`

`chordsense-iod` is the Pi's hardware I/O daemon. It is the only process that
touches SPI0 (the MCP3201 ADC on the analog front-end) or I2S (the PCM5102A
DAC). `backend/` talks to it over a Unix socket; nothing else should open
`/dev/spidev0.0` or ALSA directly.

The ADC sampling thread runs **continuously from daemon start**, forever,
independent of whether anything is "recording". Starting a capture or a stream
just attaches a sink to that always-running feed.

- [Build](#build)
- [Run as a systemd service](#run-as-a-systemd-service) (recommended)
- [Run by hand for development](#run-by-hand-for-development)
- [Configuration](#configuration)
- [The socket API](#the-socket-api)
  - [Connection model](#connection-model)
  - [Commands](#commands)
  - [Streaming](#streaming)
  - [Sample format](#sample-format)
  - [Errors](#errors)
- [Talking to it from code](#talking-to-it-from-code)
- [Poking it by hand](#poking-it-by-hand)
- [Diagnostics](#diagnostics)
- [Troubleshooting](#troubleshooting)


## Build

```sh
cd iod
cargo build --release        # -> iod/target/release/chordsense-iod
```

Requires `dtparam=spi=on` in `/boot/firmware/config.txt` (already set on the dev
Pi) and a reboot; check with `ls -l /dev/spidev*`.


## Run as a systemd service

This is the right way to run it: it gets the sampling thread real `SCHED_FIFO`
priority (so the desktop can't preempt it), restarts on crash, and starts on
boot.

```sh
# from the repo root
sudo cp iod/deploy/chordsense-iod.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chordsense-iod

systemctl status chordsense-iod
journalctl -u chordsense-iod -f
```

The unit assumes the checkout is at `/home/chordsense/workspace/ChordSense`.
If it isn't, edit `WorkingDirectory=`, `EnvironmentFile=`, `ExecStart=` and
`ReadWritePaths=` in the `.service` file, plus the paths in
`chordsense-iod.env`.

After a `cargo build` the binary is replaced but the unit points at a fixed
path, so just `sudo systemctl restart chordsense-iod` to pick up a new build.

**Verify real-time priority actually took effect:**

```sh
chrt -p "$(pgrep -x chordsense-iod)"     # policy should be SCHED_FIFO
journalctl -u chordsense-iod | grep -i fifo   # should find NOTHING
```

If you see `could not raise sampling thread to SCHED_FIFO`, the scheduling
directives in the unit aren't being applied — check `RestrictRealtime=no` is
present and `LimitRTPRIO` is set.

### Socket path

The service puts the control socket at **`/run/chordsense/iod.sock`** (systemd's
`RuntimeDirectory=` owns that directory). When you run the backend against the
service, make it use the same path:

```sh
set -a; . iod/deploy/chordsense-iod.env; set +a
( cd backend && .venv/bin/python app.py )
```

`backend/iod_client.py` also auto-detects `/run/chordsense/iod.sock` if it
exists, so in practice sourcing the env file is optional — but it also sets the
captures dir, so do it anyway.


## Run by hand for development

`iod/run-dev.sh` runs the release binary as your logged-in user with a socket
under `$XDG_RUNTIME_DIR` (no root, no systemd):

```sh
./iod/run-dev.sh
```

It builds first if needed. This path does **not** get `SCHED_FIFO` (a normal
user can't request it) — capture quality will degrade under load. Fine for
wiring work, not for a demo. To get RT priority without the full service:

```sh
sudo setcap 'cap_sys_nice=ep' iod/target/release/chordsense-iod
# re-run after every `cargo build`, which replaces the binary
```


## Configuration

All via environment variables (see `iod/src/main.rs`). `chordsense-iod.env`
holds the deployment values.

| Variable | Default | Meaning |
|---|---|---|
| `CHORDSENSE_IOD_SOCKET` | `$XDG_RUNTIME_DIR/chordsense-iod.sock`, else `/run/chordsense/iod.sock` | Control socket path. |
| `CHORDSENSE_SPI_DEVICE` | `/dev/spidev0.0` | spidev node for the MCP3201. |
| `CHORDSENSE_CAPTURES_DIR` | `runtime/captures` under CWD (made absolute) | Where captured WAVs are written. Must be readable by the backend. |
| `CHORDSENSE_I2S_DEVICE_MATCH` | unset | Case-insensitive substring of the ALSA output device name for the DAC. Unset → system default output (HDMI on the dev Pi) with a warning. |

The SPI clock (`DEFAULT_SPI_MAX_SPEED_HZ`, 1 MHz) is a compile-time constant in
`main.rs` — confirm it against the MCP3201's actual VDD before trusting capture.


## The socket API

A Unix domain stream socket carrying **newline-delimited JSON**: one request
object per line in, one response object per line out.

### Connection model

- A fresh connection is a **control connection**: send a request line, read one
  response line, repeat as many times as you like. One thread per connection;
  open as many concurrent connections as you want.
- Sending `start_stream` **converts that connection to a stream connection for
  the rest of its life** — after the initial `{"ok":true}` the daemon only
  pushes frame lines and never reads another command on it. Close the
  connection to stop the stream.
- The daemon runs until killed; it removes and re-binds the socket file on
  startup.

Internal state is one of `idle` / `capturing` / `streaming` — **capture and
streaming are mutually exclusive**. Playback is independent and can run in any
of the three.

Every response has `"ok": true|false`. On `false` there is an `"error"` string.
Fields that don't apply to a given response are omitted.

### Commands

| `cmd` | Extra request fields | Success response | Notes |
|---|---|---|---|
| `start_capture` | — | `{"ok":true}` | Attaches a WAV sink to the live ADC feed. Errors if a capture or stream is already running. |
| `stop_capture` | — | `{"ok":true,"wav_path":"/abs/capture-<ms>.wav","duration_s":4.2}` | Finalizes the WAV. `duration_s` is `samples_written / 22050`. Errors if no capture is running or nothing was recorded. |
| `play` | `path` (abs path to an audio file) | `{"ok":true}` | Loads the file and starts playing on the DAC. Decodes via `rodio` (WAV/FLAC/MP3/OGG…). |
| `pause` | — | `{"ok":true}` | Always OK. |
| `resume` | — | `{"ok":true}` | Always OK. |
| `stop_playback` | — | `{"ok":true}` | Stops and reloads the current file, so position returns to 0. |
| `seek` | `position_secs` (number) | `{"ok":true}` | Errors if the decoder can't seek. |
| `set_volume` | `volume` (0.0–1.0) | `{"ok":true}` | Always OK. Default volume 0.8. |
| `status` | — | see below | Snapshot of all state. |
| `start_stream` | `frame_samples` (int, optional, default 441 ≈ 20 ms) | `{"ok":true}` then a push stream | See [Streaming](#streaming). Errors if a capture is running. |

`status` response:

```json
{
  "ok": true,
  "capturing": false,
  "streaming": false,
  "stream_subscribers": 0,
  "playing": true,
  "paused": false,
  "position_secs": 1.20,
  "duration_secs": 4.20
}
```

`duration_secs` is omitted if nothing is loaded for playback. `playing` is
`true` only when a track is loaded, not paused, and not finished.

Example control-connection traffic:

```text
-> {"cmd":"start_capture"}
<- {"ok":true}
-> {"cmd":"status"}
<- {"ok":true,"capturing":true,"streaming":false,"stream_subscribers":0,"playing":false,"paused":false,"position_secs":0.0}
-> {"cmd":"stop_capture"}
<- {"ok":true,"wav_path":"/home/chordsense/workspace/ChordSense/runtime/captures/capture-1788661773125.wav","duration_s":4.03}

-> {"cmd":"play","path":"/home/chordsense/workspace/ChordSense/runtime/outputs/song.wav"}
<- {"ok":true}
-> {"cmd":"set_volume","volume":0.6}
<- {"ok":true}
```

### Streaming

For live analysis (e.g. real-time chord detection). On a fresh connection:

```text
-> {"cmd":"start_stream"}                 (or {"cmd":"start_stream","frame_samples":1024})
<- {"ok":true}
<- {"sample_index":566546,"playback_position_secs":0.0,"samples":"<base64 pcm16 LE>"}
<- {"sample_index":566987,"playback_position_secs":0.0,"samples":"<base64 pcm16 LE>"}
...
```

- One frame line per `frame_samples` samples (441 ≈ 20 ms by default).
- `sample_index` is the running index of the first sample in the frame since
  daemon start — use it to detect gaps.
- `playback_position_secs` is the DAC's current position (0.0 when nothing is
  playing), for lining a stream up against a backing track.
- If a subscriber reads too slowly the daemon **drops the newest frame** rather
  than stall the sampler — watch `sample_index` for jumps.
- Multiple stream connections are allowed; each gets its own framing.

### Sample format

Both captured WAVs and stream frames: **mono, 22050 Hz, signed 16-bit PCM**,
little-endian.

The MCP3201 gives a 12-bit code `0..4095` centred on `2048`. The daemon maps it
to `i16` as `(code - 2048) << 4` — centred on 0, scaled ×16 (so silence ≈ 0,
full scale ≈ ±32760). To get a float in roughly `[-1, 1)` divide by 32768.

The native SPI rate is irregular and usually well above 22050 Hz; the daemon
resamples onto an even 22050 Hz grid locked to wall-clock time, so
`samples / 22050` is an accurate duration even if the native rate wobbled.

### Errors

`{"ok":false,"error":"..."}` with one of:

| `error` | When |
|---|---|
| `capture already in progress` | `start_capture` while capturing |
| `cannot start capture while streaming` | `start_capture` while a stream is open |
| `no capture in progress` | `stop_capture` with nothing running |
| `no frames captured` | `stop_capture` after a capture that wrote 0 samples |
| `cannot start stream while capturing` | `start_stream` while capturing |
| `start_stream must be the only command sent on a connection` | any command after `start_stream` on the same connection |
| `bad request: <detail>` | malformed JSON / unknown `cmd` |
| `<rodio error>` | `play` / `seek` / `stop_playback` decoder failures |


## Talking to it from code

The backend wraps all of this in `backend/iod_client.py`:

```python
from iod_client import IodClient, IodError

iod = IodClient()                 # resolves the socket path automatically
iod.start_capture()
# ... play guitar ...
wav_path, duration_s = iod.stop_capture()

iod.status()                      # -> dict
iod.play("/abs/backing.wav"); iod.pause(); iod.resume()
iod.seek(12.5); iod.set_volume(0.7); iod.stop_playback()
```

`IodError` is raised if the daemon is unreachable, sends malformed JSON, or
returns `{"ok": false}`. The client opens a fresh connection per call.
Streaming is not wrapped yet — open the socket directly and send
`{"cmd":"start_stream"}`.


## Poking it by hand

```sh
SOCK=/run/chordsense/iod.sock        # or "$XDG_RUNTIME_DIR/chordsense-iod.sock" for run-dev.sh

# one-shot
printf '{"cmd":"status"}\n' | nc -U -q1 "$SOCK"

# interactive
socat - UNIX-CONNECT:"$SOCK"
{"cmd":"start_capture"}
{"cmd":"stop_capture"}

# record 4 s and analyse via the backend instead
curl -s -XPOST localhost:5051/begin_recording
sleep 4
curl -s -XPOST localhost:5051/end_recording | python3 -m json.tool
```


## Diagnostics

Bench tools for the ADC path live in `iod/examples/` — see
`iod/examples/README.md`. Run them with the daemon **stopped** (they contend
for the SPI bus):

```sh
sudo systemctl stop chordsense-iod
cargo run --release --example spi_probe          # is a signal reaching the ADC?
cargo run --release --example spi_freq           # what pitch is on the front-end?
cargo run --release --example spi_capture_test -- 440 6   # does the real capture path keep time?
```


## Troubleshooting

**Backend says `cannot reach iod ... Connection refused` / `No such file`.**
The daemon isn't running, or backend and daemon disagree on the socket path.
Check `systemctl status chordsense-iod`, then confirm the path: the service uses
`/run/chordsense/iod.sock`, `run-dev.sh` uses `$XDG_RUNTIME_DIR/chordsense-iod.sock`.
Source `iod/deploy/chordsense-iod.env` before starting the backend.

**`could not raise sampling thread to SCHED_FIFO` in the journal.**
Running without privilege — you're on `run-dev.sh` (expected) or the unit's
scheduling directives aren't applying. For the unit, ensure `RestrictRealtime=no`
and `LimitRTPRIO=21` are present and `systemctl daemon-reload` was run.

**`ADC native rate ~NNNN Hz is near the 22050 Hz target`.**
The userspace SPI reader is being starved (heavy CPU/bus load). RT priority via
the service helps a lot; if it persists, the durable fix is the kernel
`mcp320x` IIO driver with an hrtimer trigger, not more userspace tuning.

**Playback warns `no output device matching '...'`.**
`CHORDSENSE_I2S_DEVICE_MATCH` doesn't match any `aplay -l` device — it falls
back to the default output. Set the match string once the PCM5102A's I2S
`dtoverlay` is enabled.

**`Permission denied` opening `/dev/spidev0.0`.**
The service user must be in the `spi` group (`chordsense` already is). Check
`ls -l /dev/spidev0.0` and `groups chordsense`.
