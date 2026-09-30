#!/usr/bin/env bash
# DAC / headphone-jack test helper. Plays through the running chordsense-iod service
# (so the DAC stays owned by iod; nothing else can open it while iod runs).
#
#   iod/deploy/dac_test.sh [song [file]]  8 s of a song (default: first file in runtime/uploads) at 30%,
#                                         prints position each second
#   iod/deploy/dac_test.sh tone       steady 1 kHz full-scale sine, loops ~5 min (for scope/meter)
#   iod/deploy/dac_test.sh silence    digital silence, loops ~5 min (comparison readings)
#   iod/deploy/dac_test.sh stop       stop playback
#   iod/deploy/dac_test.sh restart    restart the iod service (sudo), e.g. after rewiring the DAC
#
# The tone is full scale -- unplug headphones before running it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:-song}"

if [[ "$MODE" == "restart" ]]; then
    sudo systemctl restart chordsense-iod
    sleep 1
    systemctl is-active chordsense-iod
    exit
fi

cd "$REPO/backend"
.venv/bin/python - "$MODE" "$REPO" "${2:-}" <<'EOF'
import math, struct, sys, time, wave
from pathlib import Path
from iod_client import IodClient, IodError

mode, repo = sys.argv[1], Path(sys.argv[2])
iod = IodClient()

try:
    status = iod.status()
except IodError as e:
    sys.exit(f"FAIL: can't reach iod: {e}")

device = status.get("output_device")
if not device or "hifiberry" not in device:
    sys.exit(f"FAIL: iod is not using the DAC (output_device={device!r}); "
             "check `journalctl -u chordsense-iod -b | grep -v ADC`")

if mode == "stop":
    iod.stop_playback()
    print("stopped")
    sys.exit()

if mode == "song":
    uploads = sorted((repo / "runtime/uploads").glob("*.*"))
    song = Path(sys.argv[3]).resolve() if sys.argv[3] else (uploads[0] if uploads else None)
    if song is None:
        sys.exit("no song given and runtime/uploads is empty; pass a file: dac_test.sh song <file>")
    print(f"output: {device}\nplaying 8 s of {song.name} at 30% volume")
    iod.set_volume(0.3)
    iod.play(song)
    try:
        for _ in range(8):
            time.sleep(1)
            s = iod.status()
            print(f"  position {s['position_secs']:5.1f} s   playing={s['playing']}")
    finally:
        iod.stop_playback()
    print("position should count ~1..8 in real time")
    sys.exit()

if mode not in ("tone", "silence"):
    sys.exit(f"unknown mode {mode!r} (song | tone | silence | stop | restart)")

# iod runs with PrivateTmp=yes, so the WAV must live where the service can see it
path = repo / f"runtime/outputs/dac_{mode}.wav"
if not path.exists():
    amp = 0 if mode == "silence" else 32000
    cycles = [int(amp * math.sin(2 * math.pi * i / 44.1)) for i in range(441)]  # 10 x 1 kHz @ 44.1 kHz
    chunk = b"".join(struct.pack("<hh", v, v) for v in cycles)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(chunk * (44100 * 300 // 441))

iod.set_volume(1.0)
iod.play(path)
print(f"{mode}: looping on {device} for ~5 min. Stop with: iod/deploy/dac_test.sh stop")
EOF
