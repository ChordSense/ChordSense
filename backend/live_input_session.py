"""Shared lifecycle for features that consume the live guitar stream.

Live Feedback and the tuner both subscribe to ``iod``'s PCM stream on a worker
thread, publish a bounded queue of sequenced events for SSE, and support the
same start/pause/resume/stop lifecycle. Subclasses own their analysis object
(the "processor") and the per-frame loop in ``_consume``.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass

import numpy as np

from iod_client import IodClient, IodError, IodStream


SAMPLE_RATE = 22_050


class LiveInputBusyError(RuntimeError):
    """A session or WAV capture already owns the required hardware."""


class LiveInputStateError(RuntimeError):
    """The requested lifecycle operation is invalid in the current state."""


@dataclass(frozen=True)
class InputLevels:
    samples: np.ndarray
    normalized: np.ndarray
    rms: float
    ac_rms: float
    clipped_fraction: float


def measure_frame(pcm16le: bytes) -> InputLevels:
    """Decode one PCM16 frame and measure the levels used for input gating.

    The Pi ADC has a substantial DC offset, so ``ac_rms`` (DC-centered) is the
    honest signal level; ``rms`` is kept for the original CNN route.
    """
    samples = np.frombuffer(pcm16le, dtype="<i2")
    normalized = samples.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(normalized * normalized)))
    ac_rms = float(np.sqrt(np.mean((normalized - np.mean(normalized)) ** 2)))
    clipped = float(np.mean(np.abs(normalized) >= 0.98))
    return InputLevels(samples, normalized, rms, ac_rms, clipped)


class LiveInputSession:
    """Own one processor and stream lifetime; publish bounded events."""

    # Used in user-facing lifecycle errors; the Tauri bridge matches
    # "<label> is already " to recover sessions orphaned by a UI restart.
    label = "live input"
    thread_name = "chordsense-live-input"

    def __init__(self, iod: IodClient | None = None, frame_samples: int = 441):
        self.iod = iod or IodClient()
        self.frame_samples = frame_samples
        self._condition = threading.Condition()
        self._lifecycle_lock = threading.RLock()
        self._events: deque[dict] = deque(maxlen=128)
        self._state = "idle"
        self._session_id: str | None = None
        self._generation = 0
        self._sequence = 0
        self._processor = None
        self._stream: IodStream | None = None
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._error: str | None = None

    # -- subclass hooks --

    def _open_processor(self):
        raise NotImplementedError

    def _reset_processor(self, processor) -> None:
        processor.reset()

    def _close_processor(self, processor) -> None:
        processor.close()

    def _consume(self, stream: IodStream, processor, stop_event: threading.Event) -> None:
        """Read frames until ``stop_event`` is set; raise to fail the session."""
        raise NotImplementedError

    # -- events --

    def _publish(self, event: dict) -> None:
        with self._condition:
            self._sequence += 1
            self._events.append({
                "session_id": self._session_id,
                "generation": self._generation,
                "sequence": self._sequence,
                "emitted_at_unix_ms": round(time.time() * 1000),
                **event,
            })
            self._condition.notify_all()

    def status(self) -> dict:
        with self._condition:
            return {
                "state": self._state,
                "session_id": self._session_id,
                "generation": self._generation,
                "sequence": self._sequence,
                "error": self._error,
            }

    def wait_event(self, session_id: str, after_sequence: int, timeout: float = 10.0) -> dict | None:
        with self._condition:
            if session_id != self._session_id:
                raise LiveInputStateError(f"{self.label} session is no longer current")
            self._condition.wait_for(
                lambda: any(e["sequence"] > after_sequence for e in self._events)
                or self._state == "idle",
                timeout=timeout,
            )
            return next(
                (e for e in self._events if e["sequence"] > after_sequence),
                None,
            )

    # -- lifecycle --

    def start(self) -> dict:
        with self._lifecycle_lock:
            return self._start()

    def _start(self) -> dict:
        with self._condition:
            if self._state != "idle":
                raise LiveInputBusyError(f"{self.label} is already {self._state}")
            self._state = "starting"
            self._session_id = uuid.uuid4().hex
            self._generation = 0
            self._sequence = 0
            self._events.clear()
            self._error = None
        processor = None
        stream = None
        try:
            processor = self._open_processor()
            stream = self.iod.open_stream(self.frame_samples)
        except Exception as exc:
            if stream is not None:
                stream.close()
            if processor is not None:
                self._close_processor(processor)
            with self._condition:
                self._state = "idle"
                self._error = str(exc)
            if isinstance(exc, IodError) and "captur" in str(exc).lower():
                raise LiveInputBusyError(str(exc)) from exc
            raise
        self._processor = processor
        self._stream = stream
        self._stop_event = threading.Event()
        with self._condition:
            self._state = "running"
        self._publish({"type": "status", "status": "warming_up"})
        self._start_worker()
        return self.status()

    def _start_worker(self) -> None:
        assert self._stream is not None and self._processor is not None
        self._worker = threading.Thread(
            target=self._run,
            args=(self._stream, self._processor, self._stop_event, self._generation),
            name=self.thread_name,
            daemon=True,
        )
        self._worker.start()

    def _run(self, stream: IodStream, processor, stop_event: threading.Event, generation: int) -> None:
        failed = False
        try:
            self._consume(stream, processor, stop_event)
        except Exception as exc:
            if not stop_event.is_set():
                failed = True
                with self._condition:
                    if self._generation == generation:
                        self._state = "error"
                        self._error = str(exc)
                self._publish({"type": "status", "status": "error", "error": str(exc)})
        finally:
            stream.close()
            if failed:
                try:
                    self._close_processor(processor)
                finally:
                    with self._condition:
                        if self._processor is processor:
                            self._processor = None

    def pause(self) -> dict:
        with self._lifecycle_lock:
            return self._pause()

    def _pause(self) -> dict:
        with self._condition:
            if self._state != "running":
                raise LiveInputStateError(f"cannot pause {self.label} while {self._state}")
            self._state = "paused"
        self._stop_worker()
        self._publish({"type": "status", "status": "paused"})
        return self.status()

    def resume(self) -> dict:
        with self._lifecycle_lock:
            return self._resume()

    def _resume(self) -> dict:
        with self._condition:
            if self._state != "paused":
                raise LiveInputStateError(f"cannot resume {self.label} while {self._state}")
        stream = self.iod.open_stream(self.frame_samples)
        assert self._processor is not None
        self._reset_processor(self._processor)
        self._stream = stream
        self._stop_event = threading.Event()
        with self._condition:
            self._generation += 1
            self._events.clear()
            self._state = "running"
        self._publish({"type": "status", "status": "warming_up"})
        self._start_worker()
        return self.status()

    def _stop_worker(self) -> None:
        self._stop_event.set()
        if self._stream is not None:
            self._stream.close()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            if self._worker.is_alive():
                raise RuntimeError(f"{self.label} worker did not stop")
        self._worker = None
        self._stream = None

    def stop(self) -> dict:
        with self._lifecycle_lock:
            return self._stop()

    def _stop(self) -> dict:
        with self._condition:
            if self._state == "idle":
                return self.status()
            self._state = "stopping"
        self._stop_worker()
        if self._processor is not None:
            self._close_processor(self._processor)
            self._processor = None
        with self._condition:
            self._state = "idle"
            self._error = None
        self._publish({"type": "status", "status": "stopped"})
        return self.status()
