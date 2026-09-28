"""Client for the Pi hardware I/O daemon (``iod``).

``iod`` (``iod/`` in this repo, package ``chordsense-iod``) is the only process
on the Pi that touches SPI0 or I2S directly. It exposes a Unix domain socket
carrying newline-delimited JSON: one request object per line in, one response
object per line out. See ``iod/src/protocol.rs`` for the command list.

The backend uses this for Record mode: ``begin_recording`` attaches a capture
sink (``start_capture``) and ``end_recording`` detaches it and gets back a WAV
path (``stop_capture``). The sampler itself runs continuously inside ``iod``
regardless of recording state.

The playback commands are wrapped here too but unused for now — frontend audio
still plays locally. They become relevant when playback moves onto ``iod``'s
I2S output.
"""

from __future__ import annotations

import json
import os
import socket
import base64
import binascii
from dataclasses import dataclass
from pathlib import Path


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
class IodSampleFrame:
    sample_index: int
    pcm16le: bytes
    captured_at_ns: int | None

    @property
    def sample_count(self) -> int:
        return len(self.pcm16le) // 2


class IodStream:
    """One dedicated stream connection; closing it removes the iod subscriber."""

    MAX_LINE_BYTES = 1_048_576

    def __init__(self, socket_path: str, frame_samples: int, timeout: float):
        if not 1 <= frame_samples <= 4_410:
            raise ValueError("frame_samples must be between 1 and 4410")
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._buffer = bytearray()
        self._closed = False
        try:
            self._socket.settimeout(timeout)
            self._socket.connect(socket_path)
            self._socket.sendall(
                (json.dumps({"cmd": "start_stream", "frame_samples": frame_samples}) + "\n")
                .encode("utf-8")
            )
            response = self._read_json()
            if not response.get("ok", False):
                raise IodError(response.get("error", "iod rejected start_stream"))
        except (OSError, ValueError, IodError) as exc:
            self.close()
            if isinstance(exc, OSError):
                raise IodError(f"cannot start iod stream at {socket_path}: {exc}") from exc
            raise

    def _read_json(self) -> dict:
        while b"\n" not in self._buffer:
            try:
                chunk = self._socket.recv(4096)
            except socket.timeout as exc:
                raise TimeoutError("waiting for iod stream frame") from exc
            except OSError as exc:
                raise IodError(f"iod stream read failed: {exc}") from exc
            if not chunk:
                raise IodError("iod stream closed")
            self._buffer.extend(chunk)
            if len(self._buffer) > self.MAX_LINE_BYTES:
                raise IodError("iod stream frame exceeds maximum line size")
        line, _, rest = self._buffer.partition(b"\n")
        self._buffer = bytearray(rest)
        try:
            value = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise IodError("iod sent malformed stream JSON") from exc
        if not isinstance(value, dict):
            raise IodError("iod stream message must be an object")
        return value

    def read_frame(self) -> IodSampleFrame:
        if self._closed:
            raise IodError("iod stream is closed")
        value = self._read_json()
        try:
            sample_index = value["sample_index"]
            if type(sample_index) is not int or sample_index < 0:
                raise ValueError("invalid sample_index")
            pcm = base64.b64decode(value["samples"], validate=True)
            if not pcm or len(pcm) % 2:
                raise ValueError("invalid PCM16 frame length")
            captured_at_ns = value.get("captured_at_ns")
            if captured_at_ns is not None and (
                type(captured_at_ns) is not int or captured_at_ns <= 0
            ):
                raise ValueError("invalid captured_at_ns")
        except (KeyError, TypeError, ValueError, binascii.Error) as exc:
            raise IodError(f"iod sent malformed PCM frame: {exc}") from exc
        return IodSampleFrame(sample_index, pcm, captured_at_ns)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()

    def __enter__(self) -> "IodStream":
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

    def status(self) -> dict:
        return self._request({"cmd": "status"})

    def open_stream(self, frame_samples: int = 441, timeout: float = 1.0) -> IodStream:
        """Subscribe to live guitar PCM; separate from the short control requests."""
        return IodStream(self.socket_path, frame_samples, timeout)

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
