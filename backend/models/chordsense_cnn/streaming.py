"""Causal feature extraction and stateful live inference for Record mode."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

import numpy as np
import numpy.typing as npt
import torch
from librosa.filters import chroma as chroma_filter
from scipy.signal import get_window

from .audio_processing import DEFAULT_PREPROCESSING_CONFIG, PreprocessingConfig
from .config import CHORD_CLASSES, NUM_CLASSES
from .model import build_model


FloatArray = npt.NDArray[np.float32]


DEFAULT_STREAMING_PREPROCESSING_CONFIG = replace(
    DEFAULT_PREPROCESSING_CONFIG,
    version="chroma-stft-causal-v1",
    feature_type="chroma_stft",
    use_harmonic=False,
    tuning=0.0,
)


@dataclass(frozen=True)
class StreamingFeatureFrame:
    sample_start: int
    sample_end: int
    chroma: FloatArray


@dataclass(frozen=True)
class LivePrediction:
    sample_index: int
    timestamp_seconds: float
    chord: str
    confidence: float
    changed: bool


@dataclass(frozen=True)
class DecisionState:
    label: int
    confidence: float
    changed: bool


class CausalChromaExtractor:
    """Incremental STFT-to-chroma front end with no future-frame access."""

    def __init__(
        self,
        sample_rate: int = 22_050,
        n_fft: int = 2048,
        hop_length: int = 512,
        n_chroma: int = 12,
        tuning: float = 0.0,
    ):
        if sample_rate <= 0 or n_fft <= 0 or hop_length <= 0 or n_chroma <= 0:
            raise ValueError("Streaming feature dimensions must be positive")
        if hop_length > n_fft:
            raise ValueError("hop_length cannot exceed n_fft")
        if not np.isfinite(tuning):
            raise ValueError("tuning must be finite")
        self.sample_rate = sample_rate
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.n_chroma = n_chroma
        # Match librosa.feature.chroma_stft(center=False, tuning=<fixed>): a
        # periodic Hann window, a power spectrogram, and librosa's normalized
        # FFT-bin-to-pitch-class filter. Fixed tuning avoids non-causal
        # whole-buffer tuning estimation.
        self.window = np.asarray(
            get_window("hann", n_fft, fftbins=True),
            dtype=np.float32,
        )
        self.projection = np.asarray(
            chroma_filter(
                sr=sample_rate,
                n_fft=n_fft,
                n_chroma=n_chroma,
                tuning=tuning,
                dtype=np.float32,
            ),
            dtype=np.float32,
        )
        self.buffer = np.empty(0, dtype=np.float32)
        self.buffer_start_sample = 0

    @staticmethod
    def _normalize_samples(samples: npt.ArrayLike) -> FloatArray:
        values = np.asarray(samples)
        if values.ndim != 1:
            raise ValueError("Streaming PCM must be one-dimensional")
        if np.issubdtype(values.dtype, np.integer):
            limits = np.iinfo(values.dtype)
            scale = float(max(abs(limits.min), limits.max))
            values = values.astype(np.float32) / scale
        else:
            values = values.astype(np.float32)
        if not np.isfinite(values).all():
            raise ValueError("Streaming PCM contains non-finite samples")
        return values

    def push(self, samples: npt.ArrayLike) -> list[StreamingFeatureFrame]:
        values = self._normalize_samples(samples)
        if values.size == 0:
            return []
        self.buffer = np.concatenate((self.buffer, values))
        frames: list[StreamingFeatureFrame] = []
        while len(self.buffer) >= self.n_fft:
            spectrum = np.abs(
                np.fft.rfft(self.buffer[: self.n_fft] * self.window)
            ) ** 2
            chroma = np.asarray(self.projection @ spectrum, dtype=np.float32)
            peak = float(chroma.max(initial=0.0))
            if peak > 0:
                chroma /= peak
            frames.append(
                StreamingFeatureFrame(
                    sample_start=self.buffer_start_sample,
                    sample_end=self.buffer_start_sample + self.n_fft,
                    chroma=chroma,
                )
            )
            self.buffer = self.buffer[self.hop_length :]
            self.buffer_start_sample += self.hop_length
        return frames

    def reset(self) -> None:
        self.buffer = np.empty(0, dtype=np.float32)
        self.buffer_start_sample = 0


class CausalDecisionFilter:
    """EMA, hysteresis, and confirmation without looking into future frames."""

    def __init__(
        self,
        class_count: int,
        noise_index: int,
        ema_alpha: float = 0.45,
        enter_threshold: float = 0.60,
        exit_threshold: float = 0.45,
        confirmation_frames: int = 2,
    ):
        if class_count <= 1 or not 0 <= noise_index < class_count:
            raise ValueError("Invalid class_count or noise_index")
        if not 0 < ema_alpha <= 1:
            raise ValueError("ema_alpha must be in (0, 1]")
        if not 0 <= exit_threshold <= enter_threshold <= 1:
            raise ValueError("Expected 0 <= exit_threshold <= enter_threshold <= 1")
        if confirmation_frames <= 0:
            raise ValueError("confirmation_frames must be positive")
        self.class_count = class_count
        self.noise_index = noise_index
        self.ema_alpha = ema_alpha
        self.enter_threshold = enter_threshold
        self.exit_threshold = exit_threshold
        self.confirmation_frames = confirmation_frames
        self.ema: FloatArray | None = None
        self.active_label = noise_index
        self.candidate_label = noise_index
        self.candidate_count = 0

    def update(self, probabilities: npt.ArrayLike) -> DecisionState:
        values = np.asarray(probabilities, dtype=np.float32)
        if values.shape != (self.class_count,) or not np.isfinite(values).all():
            raise ValueError("probabilities must be one finite value per class")
        total = float(values.sum())
        if total <= 0:
            raise ValueError("probabilities must have positive mass")
        values = values / total
        self.ema = (
            values.copy()
            if self.ema is None
            else self.ema_alpha * values + (1.0 - self.ema_alpha) * self.ema
        )

        proposed = int(self.ema.argmax())
        proposed_confidence = float(self.ema[proposed])
        if proposed != self.active_label:
            threshold = (
                self.enter_threshold
                if self.active_label == self.noise_index
                else self.exit_threshold
            )
            if proposed_confidence < threshold:
                proposed = self.active_label

        changed = False
        if proposed == self.active_label:
            self.candidate_label = self.active_label
            self.candidate_count = 0
        else:
            if proposed == self.candidate_label:
                self.candidate_count += 1
            else:
                self.candidate_label = proposed
                self.candidate_count = 1
            if self.candidate_count >= self.confirmation_frames:
                self.active_label = proposed
                self.candidate_count = 0
                changed = True

        return DecisionState(
            label=self.active_label,
            confidence=float(self.ema[self.active_label]),
            changed=changed,
        )

    def reset(self) -> None:
        self.ema = None
        self.active_label = self.noise_index
        self.candidate_label = self.noise_index
        self.candidate_count = 0


class StreamingChordRecognizer:
    """Run the existing CNN incrementally over causal chroma frames."""

    def __init__(
        self,
        model: torch.nn.Module,
        preprocessing: PreprocessingConfig = (
            DEFAULT_STREAMING_PREPROCESSING_CONFIG
        ),
        device: str = "cpu",
        extractor: CausalChromaExtractor | None = None,
        decision_filter: CausalDecisionFilter | None = None,
        temperature: float = 1.0,
    ):
        if temperature <= 0:
            raise ValueError("temperature must be positive")
        self.model = model.to(device).eval()
        self.preprocessing = preprocessing
        self.device = device
        self.temperature = temperature
        self.extractor = extractor or CausalChromaExtractor(
            sample_rate=preprocessing.sample_rate,
            n_fft=preprocessing.stft_n_fft,
            hop_length=preprocessing.hop_length,
            n_chroma=preprocessing.n_chroma,
            tuning=0.0 if preprocessing.tuning is None else preprocessing.tuning,
        )
        self.frames: deque[FloatArray] = deque(maxlen=preprocessing.context_frames)
        self.decision = decision_filter or CausalDecisionFilter(
            NUM_CLASSES,
            CHORD_CLASSES.index("Noise"),
        )

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str | Path,
        device: str = "cpu",
    ) -> "StreamingChordRecognizer":
        checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=True)
        preprocessing_metadata = checkpoint.get("preprocessing")
        preprocessing = (
            PreprocessingConfig(**preprocessing_metadata)
            if preprocessing_metadata
            else DEFAULT_STREAMING_PREPROCESSING_CONFIG
        )
        if checkpoint.get("class_names") not in (None, CHORD_CLASSES):
            raise ValueError("Checkpoint vocabulary does not match CHORD_CLASSES")
        model = build_model(
            num_classes=NUM_CLASSES,
            model_name=checkpoint.get("model_name", "baseline"),
        )
        model.load_state_dict(checkpoint["model_state_dict"])
        temperature = float(checkpoint.get("calibration", {}).get("temperature", 1.0))
        return cls(model, preprocessing, device, temperature=temperature)

    def push_samples(self, samples: npt.ArrayLike) -> list[LivePrediction]:
        predictions: list[LivePrediction] = []
        for feature_frame in self.extractor.push(samples):
            self.frames.append(feature_frame.chroma)
            if len(self.frames) < self.preprocessing.context_frames:
                continue
            features = np.stack(tuple(self.frames), axis=1)[None, None, :, :]
            tensor = torch.from_numpy(np.asarray(features, dtype=np.float32)).to(self.device)
            with torch.inference_mode():
                probabilities = torch.softmax(
                    self.model(tensor) / self.temperature,
                    dim=1,
                )[0].cpu().numpy()
            state = self.decision.update(probabilities)
            predictions.append(
                LivePrediction(
                    sample_index=feature_frame.sample_end,
                    timestamp_seconds=feature_frame.sample_end / self.preprocessing.sample_rate,
                    chord=(
                        "N"
                        if state.label == CHORD_CLASSES.index("Noise")
                        else CHORD_CLASSES[state.label]
                    ),
                    confidence=state.confidence,
                    changed=state.changed,
                )
            )
        return predictions

    def reset(self) -> None:
        self.extractor.reset()
        self.frames.clear()
        self.decision.reset()


def stream_chunks(
    recognizer: StreamingChordRecognizer,
    chunks: Iterable[npt.ArrayLike],
) -> Iterable[LivePrediction]:
    for chunk in chunks:
        yield from recognizer.push_samples(chunk)
