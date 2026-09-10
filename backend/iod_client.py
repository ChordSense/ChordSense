"""Client for the Pi hardware I/O daemon (``iod``).

``iod`` (``iod/`` in this repo, package ``chordsense-iod``) is the only process
on the Pi that touches SPI0 or I2S directly. It exposes a Unix domain socket
carrying newline-delimited JSON: one request object per line in, one response
object per line out. See ``iod/src/protocol.rs`` for the command list.

Record mode opens a live stream so the backend can run Hailo inference while it
also writes the PCM frames to a WAV. The capture commands remain wrapped for
diagnostics and other callers. The sampler itself runs continuously inside
``iod`` regardless of recording state.

The playback commands are wrapped here too but unused for now — frontend audio
still plays locally. They become relevant when playback moves onto ``iod``'s
I2S output.
"""

from __future__ import annotations

import base64
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


SYSTEM_SOCKET_PATH = "/run/chordsense/iod.sock"


def default_socket_path() -> str:
    """Resolve the control socket path.

    Resolution order:

    1. ``CHORDSENSE_IOD_SOCKET`` if set (the systemd unit sets this explicitly).
    2. ``/run/chordsense/iod.sock`` if it exists — i.e. the daemon is running as
       the system service (`iod/deploy/chordsense-iod.service`), whose
       ``RuntimeDirectory=`` owns that path.
    3. ``$XDG_RUNTIME_DIR/chordsense-iod.sock`` — the default `iod/run-dev.sh`
       uses when run by hand as the logged-in user.
    4. ``/run/chordsense/iod.sock`` as a last resort.

    Steps 2-4 mirror ``iod``'s own ``default_socket_path()`` (``iod/src/main.rs``)
    plus the "prefer the service socket if present" shortcut, so backend and
    daemon agree with no configuration in both the service and dev-script cases.
    """
    override = os.environ.get("CHORDSENSE_IOD_SOCKET")
    if override:
        return override
    if Path(SYSTEM_SOCKET_PATH).exists():
        return SYSTEM_SOCKET_PATH
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / "chordsense-iod.sock")
    return SYSTEM_SOCKET_PATH


class IodError(RuntimeError):
    """``iod`` is unreachable, spoke malformed JSON, or returned ``{"ok": false}``."""


@dataclass(frozen=True)
class IodStreamFrame:
    """One live mono PCM16 frame received from ``iod``."""

    sample_index: int
    playback_position_seconds: float
    pcm16: bytes


class IodAudioStream:
    """Owned ``iod`` stream connection that yields live PCM frames."""

    def __init__(self, sock: socket.socket, reader: BinaryIO | None = None):
        self._socket = sock
        self._reader = reader or sock.makefile("rb")
        self._closed = False

    def __iter__(self) -> Iterator[IodStreamFrame]:
        while not self._closed:
            try:
                line = self._reader.readline()
            except OSError:
                if self._closed:
                    return
                raise
            if not line:
                return
            try:
                payload = json.loads(line)
                pcm16 = base64.b64decode(payload["samples"], validate=True)
                if len(pcm16) % 2:
                    raise ValueError("PCM16 payload has an odd byte count")
                yield IodStreamFrame(
                    sample_index=int(payload["sample_index"]),
                    playback_position_seconds=float(
                        payload.get("playback_position_secs", 0.0)
                    ),
                    pcm16=pcm16,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise IodError(f"iod sent a malformed stream frame: {line!r}") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._reader.close()
        finally:
            self._socket.close()

    def __enter__(self) -> "IodAudioStream":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class IodClient:
    def __init__(self, socket_path: str | None = None, timeout: float = 5.0):
        self.socket_path = socket_path or default_socket_path()
        self.timeout = timeout

    def _request(self, payload: dict) -> dict:
        request_line = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                sock.sendall(request_line)
                buffer = b""
                while b"\n" not in buffer:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
        except OSError as exc:
            raise IodError(
                f"cannot reach iod at {self.socket_path} ({exc}). "
                "Is chordsense-iod running? (iod/run-dev.sh)"
            ) from exc

        if not buffer:
            raise IodError("iod closed the connection without responding")

        first_line = buffer.split(b"\n", 1)[0]
        try:
            response = json.loads(first_line)
        except json.JSONDecodeError as exc:
            raise IodError(f"iod sent malformed JSON: {first_line!r}") from exc

        if not response.get("ok", False):
            raise IodError(response.get("error", "iod reported an unspecified failure"))
        return response

    # -- capture (Record mode) --

    def start_capture(self) -> None:
        """Attach a capture sink. Raises if a capture or stream is already active."""
        self._request({"cmd": "start_capture"})

    def stop_capture(self) -> tuple[Path, float]:
        """Detach the sink and finalize the WAV. Returns ``(wav_path, duration_s)``.

        Raises ``IodError`` if no capture was running or nothing was recorded.
        """
        response = self._request({"cmd": "stop_capture"})
        wav_path = response.get("wav_path")
        if not wav_path:
            raise IodError("iod stop_capture returned no wav_path")
        return Path(wav_path), float(response.get("duration_s", 0.0))

    def start_stream(self, frame_samples: int = 441) -> IodAudioStream:
        """Open a live mono PCM16 stream. Closing it unsubscribes from ``iod``."""

        if frame_samples <= 0:
            raise ValueError("frame_samples must be positive")
        request_line = (
            json.dumps({"cmd": "start_stream", "frame_samples": frame_samples})
            + "\n"
        ).encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
            sock.sendall(request_line)
            reader = sock.makefile("rb")
            response_line = reader.readline()
            if not response_line:
                reader.close()
                raise IodError("iod closed the stream connection without responding")
            response = json.loads(response_line)
            if not response.get("ok", False):
                reader.close()
                raise IodError(
                    response.get("error", "iod rejected the stream request")
                )
            sock.settimeout(None)
            return IodAudioStream(sock, reader)
        except (OSError, json.JSONDecodeError) as exc:
            sock.close()
            raise IodError(
                f"cannot start iod stream at {self.socket_path} ({exc})"
            ) from exc
        except Exception:
            sock.close()
            raise

    def status(self) -> dict:
        return self._request({"cmd": "status"})

    # -- playback (wrapped for later; frontend audio is still local) --

    def play(self, path: str | Path) -> None:
        self._request({"cmd": "play", "path": str(path)})

    def pause(self) -> None:
        self._request({"cmd": "pause"})

    def resume(self) -> None:
        self._request({"cmd": "resume"})

    def stop_playback(self) -> None:
        self._request({"cmd": "stop_playback"})

    def seek(self, position_secs: float) -> None:
        self._request({"cmd": "seek", "position_secs": float(position_secs)})

    def set_volume(self, volume: float) -> None:
        self._request({"cmd": "set_volume", "volume": float(volume)})
