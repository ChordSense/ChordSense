"""One background guitar stream and causal CNN session for playback feedback."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np

from iod_client import IodClient, IodError, IodSampleFrame, IodStream
from models.chordsense_cnn.audio_processing import PreprocessingConfig
from models.chordsense_cnn.config import CHORD_CLASSES
from models.chordsense_cnn.streaming import (
    DEFAULT_STREAMING_PREPROCESSING_CONFIG,
    StreamingChordRecognizer,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_LIVE_HEF = BASE_DIR / "models/chordsense_cnn/checkpoints/chordsense_live.hef"
DEFAULT_LIVE_MANIFEST = BASE_DIR / "models/chordsense_cnn/checkpoints/chordsense_live.json"
SAMPLE_RATE = 22_050


class FeedbackBusyError(RuntimeError):
    """A session or WAV capture already owns the required hardware."""


class FeedbackStateError(RuntimeError):
    """The requested lifecycle operation is invalid in the current state."""


def load_live_recognizer() -> StreamingChordRecognizer:
    """Load only an explicitly documented causal-STFT HEF, never the offline HEF."""
    hef_path = Path(os.environ.get("CHORDSENSE_LIVE_HEF", str(DEFAULT_LIVE_HEF)))
    manifest_path = Path(
        os.environ.get("CHORDSENSE_LIVE_MANIFEST", str(DEFAULT_LIVE_MANIFEST))
    )
    if not hef_path.is_file() or not manifest_path.is_file():
        raise RuntimeError(
            "Live feedback needs a validated causal-STFT HEF and manifest. "
            "Set CHORDSENSE_LIVE_HEF and CHORDSENSE_LIVE_MANIFEST."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("pi_verified") is not True:
        raise ValueError("Live HEF has not passed Pi Torch/Hailo verification")
    preprocessing = PreprocessingConfig(**manifest["preprocessing"])
    actual_config = asdict(preprocessing)
    expected_config = asdict(DEFAULT_STREAMING_PREPROCESSING_CONFIG)
    actual_config.pop("version")
    expected_config.pop("version")
    if actual_config != expected_config or manifest.get("class_names") != CHORD_CLASSES:
        raise ValueError("Live HEF manifest does not match the causal CNN contract")
    expected_hash = manifest.get("hef_sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ValueError("Live HEF manifest requires hef_sha256")
    digest = hashlib.sha256()
    with hef_path.open("rb") as hef_file:
        for chunk in iter(lambda: hef_file.read(1024 * 1024), b""):
            digest.update(chunk)
    actual_hash = digest.hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("Live HEF hash does not match its manifest")
    return StreamingChordRecognizer.from_hef(
        hef_path,
        preprocessing=preprocessing,
        temperature=float(manifest["temperature"]),
    )


class LiveFeedbackSession:
    """Own model and stream lifetimes; publish bounded prediction events."""

    def __init__(
        self,
        iod: IodClient | None = None,
        recognizer_factory: Callable[[], StreamingChordRecognizer] = load_live_recognizer,
        frame_samples: int = 441,
        silence_rms: float = 0.008,
        clipping_fraction: float = 0.05,
    ):
        self.iod = iod or IodClient()
        self.recognizer_factory = recognizer_factory
        self.frame_samples = frame_samples
        self.silence_rms = silence_rms
        self.clipping_fraction = clipping_fraction
        self._condition = threading.Condition()
        self._lifecycle_lock = threading.RLock()
        self._events: deque[dict] = deque(maxlen=128)
        self._state = "idle"
        self._session_id: str | None = None
        self._generation = 0
        self._sequence = 0
        self._recognizer: StreamingChordRecognizer | None = None
        self._stream: IodStream | None = None
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._error: str | None = None

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

    def start(self) -> dict:
        with self._lifecycle_lock:
            return self._start()

    def _start(self) -> dict:
        with self._condition:
            if self._state != "idle":
                raise FeedbackBusyError(f"feedback is already {self._state}")
            self._state = "starting"
            self._session_id = uuid.uuid4().hex
            self._generation = 0
            self._sequence = 0
            self._events.clear()
            self._error = None
        recognizer = None
        stream = None
        try:
            recognizer = self.recognizer_factory()
            stream = self.iod.open_stream(self.frame_samples)
        except Exception as exc:
            if stream is not None:
                stream.close()
            if recognizer is not None:
                recognizer.close()
            with self._condition:
                self._state = "idle"
                self._error = str(exc)
            if isinstance(exc, IodError) and "captur" in str(exc).lower():
                raise FeedbackBusyError(str(exc)) from exc
            raise
        self._recognizer = recognizer
        self._stream = stream
        self._stop_event = threading.Event()
        with self._condition:
            self._state = "running"
        self._publish({"type": "status", "status": "warming_up"})
        self._start_worker()
        return self.status()

    def _start_worker(self) -> None:
        assert self._stream is not None and self._recognizer is not None
        self._worker = threading.Thread(
            target=self._run,
            args=(self._stream, self._recognizer, self._stop_event, self._generation),
            name="chordsense-live-feedback",
            daemon=True,
        )
        self._worker.start()

    def _run(
        self,
        stream: IodStream,
        recognizer: StreamingChordRecognizer,
        stop_event: threading.Event,
        generation: int,
    ) -> None:
        next_sample: int | None = None
        base_sample: int | None = None
        silent_samples = 0
        silence_reset = False
        failed = False
        try:
            while not stop_event.is_set():
                try:
                    frame = stream.read_frame()
                except TimeoutError:
                    continue
                if next_sample is not None and frame.sample_index != next_sample:
                    recognizer.reset()
                    base_sample = None
                    silent_samples = 0
                    silence_reset = False
                    self._publish({"type": "status", "status": "stream_gap"})
                next_sample = frame.sample_index + frame.sample_count
                samples = np.frombuffer(frame.pcm16le, dtype="<i2")
                normalized = samples.astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(normalized * normalized)))
                clipped = float(np.mean(np.abs(normalized) >= 0.98))
                quality = (
                    "clipping" if clipped >= self.clipping_fraction
                    else "silence" if rms < self.silence_rms
                    else "ok"
                )
                if quality == "silence":
                    silent_samples += frame.sample_count
                    if silent_samples >= round(0.25 * SAMPLE_RATE):
                        if not silence_reset:
                            recognizer.reset()
                            base_sample = None
                            silence_reset = True
                            self._publish({"type": "status", "status": "silence"})
                        continue
                else:
                    if silence_reset:
                        recognizer.reset()
                        base_sample = None
                        silence_reset = False
                    silent_samples = 0
                if base_sample is None:
                    base_sample = frame.sample_index
                predictions = recognizer.push_samples(samples)
                for prediction in predictions:
                    sample_end = base_sample + prediction.sample_index
                    captured_at_ns = self._prediction_time_ns(frame, sample_end)
                    age_ms = (
                        max(0.0, (time.monotonic_ns() - captured_at_ns) / 1_000_000)
                        if captured_at_ns is not None else None
                    )
                    self._publish({
                        "type": "prediction",
                        "model": "chordsense-causal-stft-cnn",
                        "inference_backend": getattr(getattr(recognizer, "backend", None), "name", "unknown"),
                        "sample_index": sample_end,
                        "captured_at_ns": captured_at_ns,
                        "sample_age_ms": age_ms,
                        "chord": prediction.chord if quality == "ok" else None,
                        "confidence": prediction.confidence,
                        "changed": prediction.changed,
                        "input_quality": quality,
                        "rms": rms,
                    })
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
                    recognizer.close()
                finally:
                    with self._condition:
                        if self._recognizer is recognizer:
                            self._recognizer = None

    @staticmethod
    def _prediction_time_ns(frame: IodSampleFrame, sample_end: int) -> int | None:
        if frame.captured_at_ns is None:
            return None
        frame_end = frame.sample_index + frame.sample_count
        return frame.captured_at_ns - round(
            (frame_end - sample_end) * 1_000_000_000 / SAMPLE_RATE
        )

    def pause(self) -> dict:
        with self._lifecycle_lock:
            return self._pause()

    def _pause(self) -> dict:
        with self._condition:
            if self._state != "running":
                raise FeedbackStateError(f"cannot pause feedback while {self._state}")
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
                raise FeedbackStateError(f"cannot resume feedback while {self._state}")
        stream = self.iod.open_stream(self.frame_samples)
        assert self._recognizer is not None
        self._recognizer.reset()
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
                raise RuntimeError("live feedback worker did not stop")
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
        if self._recognizer is not None:
            self._recognizer.close()
            self._recognizer = None
        with self._condition:
            self._state = "idle"
            self._error = None
        self._publish({"type": "status", "status": "stopped"})
        return self.status()

    def wait_event(self, session_id: str, after_sequence: int, timeout: float = 10.0) -> dict | None:
        with self._condition:
            if session_id != self._session_id:
                raise FeedbackStateError("feedback session is no longer current")
            self._condition.wait_for(
                lambda: any(e["sequence"] > after_sequence for e in self._events)
                or self._state == "idle",
                timeout=timeout,
            )
            return next(
                (e for e in self._events if e["sequence"] > after_sequence),
                None,
            )
