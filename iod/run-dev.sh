#!/usr/bin/env bash
#
# Dev launcher for chordsense-iod on the Pi.
#
# Runs the release binary with a control socket and captures directory that
# don't need root. The backend's iod_client resolves the same default socket
# path, so when both run as the same user (with XDG_RUNTIME_DIR set) nothing
# extra is needed. A hardened systemd unit is a later task; for now run this in
# tmux alongside the backend.
#
# Env overrides (all optional):
#   CHORDSENSE_IOD_SOCKET        control socket path
#   CHORDSENSE_CAPTURES_DIR      where captured WAVs are written
#   CHORDSENSE_SPI_DEVICE        spidev node (default /dev/spidev0.0)
#   CHORDSENSE_I2S_DEVICE_MATCH  ALSA output-name substring for the PCM5102A

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/.." && pwd)"

export CHORDSENSE_IOD_SOCKET="${CHORDSENSE_IOD_SOCKET:-${XDG_RUNTIME_DIR:-/tmp}/chordsense-iod.sock}"
export CHORDSENSE_CAPTURES_DIR="${CHORDSENSE_CAPTURES_DIR:-$repo_root/runtime/captures}"
export CHORDSENSE_SPI_DEVICE="${CHORDSENSE_SPI_DEVICE:-/dev/spidev0.0}"

bin="$here/target/release/chordsense-iod"
if [[ ! -x "$bin" ]]; then
    echo "run-dev.sh: building release binary..."
    (cd "$here" && cargo build --release)
fi

mkdir -p "$CHORDSENSE_CAPTURES_DIR"

echo "chordsense-iod"
echo "  socket:   $CHORDSENSE_IOD_SOCKET"
echo "  captures: $CHORDSENSE_CAPTURES_DIR"
echo "  spi:      $CHORDSENSE_SPI_DEVICE"
echo

exec "$bin"
