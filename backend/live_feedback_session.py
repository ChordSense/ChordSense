"""One background guitar stream and live recognizer for playback feedback."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from iod_client import IodClient, IodSampleFrame, IodStream
from live_input_session import (
    SAMPLE_RATE,
    LiveInputBusyError,
    LiveInputSession,
    LiveInputStateError,
    measure_frame,
)
from models.chordsense_cnn.audio_processing import PreprocessingConfig
from models.chordsense_cnn.config import CHORD_CLASSES
from models.chordsense_cnn.streaming import (
    DEFAULT_STREAMING_PREPROCESSING_CONFIG,
    StreamingChordRecognizer,
)
from models.chordsense_cnn.template import StreamingTemplateRecognizer, TemplatePrediction


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_LIVE_HEF = BASE_DIR / "models/chordsense_cnn/checkpoints/chordsense_live.hef"
DEFAULT_LIVE_MANIFEST = BASE_DIR / "models/chordsense_cnn/checkpoints/chordsense_live.json"
DEFAULT_LIVE_RECOGNIZER = "template"


# Kept as aliases so existing imports and the native client's "feedback is
# already ..." recovery check keep working.
FeedbackBusyError = LiveInputBusyError
FeedbackStateError = LiveInputStateError


LiveRecognizer = StreamingChordRecognizer | StreamingTemplateRecognizer


def load_live_recognizer() -> LiveRecognizer:
    """Load the DSP template by default, or an explicitly selected verified HEF."""
    mode = os.environ.get("CHORDSENSE_LIVE_RECOGNIZER", DEFAULT_LIVE_RECOGNIZER).lower()
    if mode == "template":
        return StreamingTemplateRecognizer()
    if mode != "cnn":
        raise ValueError("CHORDSENSE_LIVE_RECOGNIZER must be cnn or template")
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


class LiveFeedbackSession(LiveInputSession):
    """Own model and stream lifetimes; publish bounded prediction events."""

    label = "feedback"
    thread_name = "chordsense-live-feedback"

    def __init__(
        self,
        iod: IodClient | None = None,
        recognizer_factory: Callable[[], LiveRecognizer] = load_live_recognizer,
        frame_samples: int = 441,
        silence_rms: float = 0.008,
        clipping_fraction: float = 0.05,
        shadow_factory: Callable[[], StreamingTemplateRecognizer] | None = None,
    ):
        super().__init__(iod=iod, frame_samples=frame_samples)
        self.recognizer_factory = recognizer_factory
        self.silence_rms = silence_rms
        self.clipping_fraction = clipping_fraction
        self.shadow_factory = shadow_factory
        if (
            self.shadow_factory is None
            and os.environ.get("CHORDSENSE_DSP_SHADOW") == "1"
            and os.environ.get(
                "CHORDSENSE_LIVE_RECOGNIZER", DEFAULT_LIVE_RECOGNIZER
            ).lower() == "cnn"
        ):
            self.shadow_factory = StreamingTemplateRecognizer
        self._shadow_recognizer: StreamingTemplateRecognizer | None = None

    def _open_processor(self) -> LiveRecognizer:
        recognizer = self.recognizer_factory()
        self._shadow_recognizer = None
        if self.shadow_factory is not None:
            try:
                self._shadow_recognizer = self.shadow_factory()
            except Exception as exc:
                # A comparison experiment must not prevent the verified
                # primary recognizer from starting.
                self._publish({"type": "status", "status": "dsp_shadow_error", "error": str(exc)})
        return recognizer

    def _reset_processor(self, recognizer: LiveRecognizer) -> None:
        recognizer.reset()
        if self._shadow_recognizer is not None:
            try:
                self._shadow_recognizer.reset()
            except Exception as exc:
                try:
                    self._shadow_recognizer.close()
                except Exception:
                    pass
                self._shadow_recognizer = None
                self._publish({"type": "status", "status": "dsp_shadow_error", "error": str(exc)})

    def _close_processor(self, recognizer: LiveRecognizer) -> None:
        try:
            recognizer.close()
        finally:
            if self._shadow_recognizer is not None:
                try:
                    self._shadow_recognizer.close()
                except Exception:
                    pass
                self._shadow_recognizer = None

    def _consume(
        self,
        stream: IodStream,
        recognizer: LiveRecognizer,
        stop_event: threading.Event,
    ) -> None:
        next_sample: int | None = None
        base_sample: int | None = None
        silent_samples = 0
        silence_reset = False
        signal_hold_samples = 0
        shadow = self._shadow_recognizer
        experimental = isinstance(recognizer, StreamingTemplateRecognizer)

        def disable_shadow(exc: Exception) -> None:
            nonlocal shadow
            if shadow is not None:
                try:
                    shadow.close()
                except Exception:
                    pass
            shadow = None
            self._shadow_recognizer = None
            self._publish({"type": "status", "status": "dsp_shadow_error", "error": str(exc)})

        def reset_shadow() -> None:
            if shadow is not None:
                try:
                    shadow.reset()
                except Exception as exc:
                    disable_shadow(exc)

        while not stop_event.is_set():
            try:
                frame = stream.read_frame()
            except TimeoutError:
                continue
            if next_sample is not None and frame.sample_index != next_sample:
                recognizer.reset()
                reset_shadow()
                base_sample = None
                silent_samples = 0
                silence_reset = False
                signal_hold_samples = 0
                self._publish({"type": "status", "status": "stream_gap"})
            next_sample = frame.sample_index + frame.sample_count
            levels = measure_frame(frame.pcm16le)
            samples = levels.samples
            rms = levels.rms
            ac_rms = levels.ac_rms
            if ac_rms >= self.silence_rms:
                signal_hold_samples = round(0.15 * SAMPLE_RATE)
            else:
                signal_hold_samples = max(0, signal_hold_samples - frame.sample_count)
            signal_ok = ac_rms >= self.silence_rms or signal_hold_samples > 0
            clipped = levels.clipped_fraction
            has_signal = signal_ok if experimental else rms >= self.silence_rms
            quality = (
                "clipping" if clipped >= self.clipping_fraction
                else "silence" if not has_signal
                else "ok"
            )
            if quality == "silence":
                silent_samples += frame.sample_count
                reset_after = 1.0 if experimental else 0.25
                if silent_samples >= round(reset_after * SAMPLE_RATE):
                    if not silence_reset:
                        recognizer.reset()
                        reset_shadow()
                        base_sample = None
                        silence_reset = True
                        self._publish({"type": "status", "status": "silence"})
                    continue
            else:
                if silence_reset:
                    recognizer.reset()
                    reset_shadow()
                    base_sample = None
                    silence_reset = False
                silent_samples = 0
            if base_sample is None:
                base_sample = frame.sample_index
            predictions = recognizer.push_samples(samples)
            shadow_predictions = {}
            if shadow is not None:
                try:
                    shadow_predictions = {
                        item.sample_index: item for item in shadow.push_samples(samples)
                    }
                except Exception as exc:
                    disable_shadow(exc)
            for prediction in predictions:
                sample_end = base_sample + prediction.sample_index
                captured_at_ns = self._prediction_time_ns(frame, sample_end)
                age_ms = (
                    max(0.0, (time.monotonic_ns() - captured_at_ns) / 1_000_000)
                    if captured_at_ns is not None else None
                )
                event = {
                    "type": "prediction",
                    "model": getattr(recognizer, "model_name", "chordsense-causal-stft-cnn"),
                    "inference_backend": getattr(getattr(recognizer, "backend", None), "name", "unknown"),
                    "sample_index": sample_end,
                    "captured_at_ns": captured_at_ns,
                    "sample_age_ms": age_ms,
                    "chord": prediction.chord if quality == "ok" else None,
                    "confidence": prediction.confidence,
                    "changed": prediction.changed,
                    "input_quality": quality,
                    "rms": rms,
                }
                if experimental:
                    event.update({
                        "ac_rms": ac_rms,
                        "confidence_kind": "template_root_margin",
                        "quality_uncertain": prediction.quality_uncertain,
                        "template_evidence": self._template_evidence(prediction),
                    })
                if shadow is not None:
                    other = shadow_predictions.get(prediction.sample_index)
                    if other is not None:
                        event["dsp_experiment"] = {
                            "sample_index": sample_end,
                            "chord": other.chord if signal_ok else None,
                            "signal_ok": signal_ok,
                            "ac_rms": ac_rms,
                            "quality_uncertain": other.quality_uncertain,
                            **self._template_evidence(other),
                        }
                self._publish(event)

    @staticmethod
    def _template_evidence(prediction: TemplatePrediction) -> dict:
        return {
            "raw_chord": prediction.raw_chord,
            "accepted": prediction.accepted,
            "score": prediction.score,
            "concentration": prediction.concentration,
            "margin": prediction.margin,
            "root_margin": prediction.root_margin,
            "quality_margin": prediction.quality_margin,
        }

    @staticmethod
    def _prediction_time_ns(frame: IodSampleFrame, sample_end: int) -> int | None:
        if frame.captured_at_ns is None:
            return None
        frame_end = frame.sample_index + frame.sample_count
        return frame.captured_at_ns - round(
            (frame_end - sample_end) * 1_000_000_000 / SAMPLE_RATE
        )
