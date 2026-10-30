"""Live guitar tuner on the shared ``iod`` stream.

Reuses the Live Feedback stream lifecycle, event queue, and input gating, but
not its chroma features, which cannot resolve cents (see ``pitch_detection``).
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from iod_client import IodClient, IodStream
from live_input_session import SAMPLE_RATE, LiveInputSession, measure_frame
from pitch_detection import DEFAULT_A4_HZ, MAX_A4_HZ, MIN_A4_HZ, PitchTracker


_KEEP = "keep"


def validate_settings(a4_hz=None, target_midi=_KEEP) -> dict:
    """Check request values before they reach the worker thread."""
    settings = {}
    if a4_hz is not None:
        if isinstance(a4_hz, bool) or not isinstance(a4_hz, (int, float)):
            raise ValueError("a4_hz must be a number")
        if not MIN_A4_HZ <= a4_hz <= MAX_A4_HZ:
            raise ValueError(f"a4_hz must be between {MIN_A4_HZ:g} and {MAX_A4_HZ:g}")
        settings["a4_hz"] = float(a4_hz)
    if target_midi != _KEEP:
        if target_midi is not None and (
            isinstance(target_midi, bool) or not isinstance(target_midi, int)
            or not 28 <= target_midi <= 96
        ):
            raise ValueError("target_midi must be null or an integer between 28 and 96")
        settings["target_midi"] = target_midi
    return settings


class TunerSession(LiveInputSession):
    """Publish smoothed pitch readings plus signal-quality transitions."""

    label = "tuner"
    thread_name = "chordsense-tuner"

    def __init__(
        self,
        iod: IodClient | None = None,
        tracker_factory: Callable[[], PitchTracker] = PitchTracker,
        frame_samples: int = 441,
        silence_rms: float = 0.008,
        clipping_fraction: float = 0.05,
    ):
        super().__init__(iod=iod, frame_samples=frame_samples)
        self.tracker_factory = tracker_factory
        self.silence_rms = silence_rms
        self.clipping_fraction = clipping_fraction
        self._settings_lock = threading.Lock()
        self._settings = {"a4_hz": DEFAULT_A4_HZ, "target_midi": None}
        self._settings_version = 0

    def settings(self) -> dict:
        with self._settings_lock:
            return dict(self._settings)

    def configure(self, a4_hz=None, target_midi=_KEEP) -> dict:
        """Update the reference pitch or string lock, live or before start.

        The worker applies changes between frames so the tracker is only ever
        touched from one thread.
        """
        changes = validate_settings(a4_hz, target_midi)
        with self._settings_lock:
            self._settings.update(changes)
            self._settings_version += 1
            return dict(self._settings)

    def status(self) -> dict:
        return {**super().status(), **self.settings()}

    def _open_processor(self) -> PitchTracker:
        tracker = self.tracker_factory()
        tracker.configure(**self.settings())
        return tracker

    def _close_processor(self, tracker: PitchTracker) -> None:
        pass

    def _consume(self, stream: IodStream, tracker: PitchTracker, stop_event: threading.Event) -> None:
        next_sample: int | None = None
        signal_hold_samples = 0
        quality: str | None = None
        with self._settings_lock:
            applied_version = self._settings_version
            # Configuration may change between opening the tracker and starting
            # this worker; establish both from the same settings snapshot.
            tracker.configure(**self._settings)

        while not stop_event.is_set():
            try:
                frame = stream.read_frame()
            except TimeoutError:
                continue
            with self._settings_lock:
                if self._settings_version != applied_version:
                    tracker.configure(**self._settings)
                    applied_version = self._settings_version
                settings = dict(self._settings)
            if next_sample is not None and frame.sample_index != next_sample:
                tracker.reset()
                signal_hold_samples = 0
                self._publish({"type": "status", "status": "stream_gap"})
            next_sample = frame.sample_index + frame.sample_count

            levels = measure_frame(frame.pcm16le)
            # Same gate as the template feedback route: a 150 ms hold keeps a
            # decaying string from flickering in and out of "silence".
            if levels.ac_rms >= self.silence_rms:
                signal_hold_samples = round(0.15 * SAMPLE_RATE)
            else:
                signal_hold_samples = max(0, signal_hold_samples - frame.sample_count)
            frame_quality = (
                "clipping" if levels.clipped_fraction >= self.clipping_fraction
                else "silence" if levels.ac_rms < self.silence_rms and signal_hold_samples == 0
                else "ok"
            )
            if frame_quality != quality:
                quality = frame_quality
                if quality != "ok":
                    tracker.reset()
                self._publish({
                    "type": "status",
                    "status": "listening" if quality == "ok" else quality,
                    "ac_rms": levels.ac_rms,
                })
            if quality != "ok":
                continue

            reading = tracker.push(levels.normalized)
            if reading is None:
                continue
            frame_end = frame.sample_index + frame.sample_count
            self._publish({
                "type": "pitch",
                "sample_index": frame_end,
                "captured_at_ns": frame.captured_at_ns,
                "sample_age_ms": (
                    max(0.0, (time.monotonic_ns() - frame.captured_at_ns) / 1_000_000)
                    if frame.captured_at_ns is not None else None
                ),
                "f0_hz": reading.f0_hz,
                "note": reading.note,
                "octave": reading.octave,
                "midi_note": reading.midi_note,
                "cents": reading.cents,
                "clarity": reading.clarity,
                "ac_rms": levels.ac_rms,
                "input_quality": quality,
                **settings,
            })
