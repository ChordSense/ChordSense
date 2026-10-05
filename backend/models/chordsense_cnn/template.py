"""Experimental, training-free major/minor chord scores from causal chroma.

The default uses a 4096-sample FFT and seven causal chroma frames. Explicit
preprocessing can retain the CNN's feature contract for paired comparisons.
Its score margin is relative evidence, not a calibrated probability.
Signal/noise rejection belongs to the caller because per-frame chroma is peak
normalized even when the input waveform is nearly silent.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from types import SimpleNamespace

import numpy as np
import numpy.typing as npt

from .audio_processing import PreprocessingConfig
from .config import CHORD_CLASSES
from .streaming import CausalChromaExtractor, DEFAULT_STREAMING_PREPROCESSING_CONFIG


DEFAULT_TEMPLATE_PREPROCESSING_CONFIG = replace(
    DEFAULT_STREAMING_PREPROCESSING_CONFIG,
    version="chroma-stft-template-v1",
    stft_n_fft=4096,
    hop_length=512,
    context_frames=7,
)


PITCH_CLASS = {
    "C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
    "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11,
}
TRIAD_LABELS = tuple(label for label in CHORD_CLASSES if label != "Noise")


def _template_matrix() -> npt.NDArray[np.float32]:
    if len(TRIAD_LABELS) != 24:
        raise ValueError("Expected exactly 24 major/minor chord classes")
    values = np.zeros((len(TRIAD_LABELS), 12), dtype=np.float32)
    for index, label in enumerate(TRIAD_LABELS):
        is_minor = label.endswith("m")
        root = label[:-1] if is_minor else label
        pitch = PITCH_CLASS[root]
        values[index, [pitch, (pitch + (3 if is_minor else 4)) % 12, (pitch + 7) % 12]] = 1.0
    return values


TRIAD_TEMPLATES = _template_matrix()


@dataclass(frozen=True)
class TemplatePrediction:
    sample_index: int
    timestamp_seconds: float
    chord: str
    confidence: float
    changed: bool
    raw_chord: str
    accepted: bool
    quality_uncertain: bool
    score: float
    concentration: float
    margin: float
    root_margin: float
    quality_margin: float


def score_chroma(chroma: npt.ArrayLike) -> tuple[str, float, float, float, float]:
    """Return best triad, its score, top-two, root, and quality margins."""
    values = np.asarray(chroma, dtype=np.float32)
    if values.shape != (12,) or not np.isfinite(values).all():
        raise ValueError("Expected one finite value for each pitch class")
    scores = TRIAD_TEMPLATES @ values
    best = int(np.argmax(scores))
    best_score = float(scores[best])
    next_score = float(np.partition(scores, -2)[-2])
    margin = max(0.0, best_score - next_score)

    # Keep root and major/minor evidence separate. A weak third should not be
    # mistaken for strong evidence that the whole chord quality is correct.
    root_scores = np.full(12, -np.inf, dtype=np.float32)
    for index, label in enumerate(TRIAD_LABELS):
        root = label[:-1] if label.endswith("m") else label
        root_scores[PITCH_CLASS[root]] = max(root_scores[PITCH_CLASS[root]], scores[index])
    winning_label = TRIAD_LABELS[best]
    winning_root = winning_label[:-1] if winning_label.endswith("m") else winning_label
    root_index = PITCH_CLASS[winning_root]
    other_root = float(np.max(np.delete(root_scores, root_index)))
    root_margin = max(0.0, best_score - other_root)
    opposite = winning_root if winning_label.endswith("m") else winning_root + "m"
    quality_margin = max(0.0, best_score - float(scores[TRIAD_LABELS.index(opposite)]))
    return winning_label, best_score, margin, root_margin, quality_margin


class StreamingTemplateRecognizer:
    """Score causal chroma windows; alternate settings support replay experiments."""

    model_name = "chordsense-chroma-template-experimental"

    def __init__(
        self,
        min_concentration: float = 0.40,
        min_root_margin: float = 0.02,
        min_quality_margin: float = 0.08,
        *,
        preprocessing: PreprocessingConfig = DEFAULT_TEMPLATE_PREPROCESSING_CONFIG,
    ):
        if not 0 <= min_concentration <= 1 or min_root_margin < 0 or min_quality_margin < 0:
            raise ValueError("Invalid template evidence thresholds")
        self.min_concentration = min_concentration
        self.min_root_margin = min_root_margin
        self.min_quality_margin = min_quality_margin
        if (preprocessing.feature_type != "chroma_stft" or preprocessing.use_harmonic
                or preprocessing.n_chroma != 12 or preprocessing.tuning is None):
            raise ValueError("Template recognizer requires causal 12-bin STFT with fixed tuning")
        self.preprocessing = preprocessing
        self.extractor = CausalChromaExtractor(
            sample_rate=self.preprocessing.sample_rate,
            n_fft=self.preprocessing.stft_n_fft,
            hop_length=self.preprocessing.hop_length,
            n_chroma=self.preprocessing.n_chroma,
            tuning=self.preprocessing.tuning or 0.0,
        )
        self.frames: deque[npt.NDArray[np.float32]] = deque(
            maxlen=self.preprocessing.context_frames
        )
        self.backend = SimpleNamespace(name="dsp-template")
        self._last_chord = "N"

    def push_samples(self, samples: npt.ArrayLike) -> list[TemplatePrediction]:
        predictions: list[TemplatePrediction] = []
        for frame in self.extractor.push(samples):
            self.frames.append(frame.chroma)
            if len(self.frames) < self.preprocessing.context_frames:
                continue
            chroma = np.mean(np.stack(self.frames), axis=0)
            raw_chord, score, margin, root_margin, quality_margin = score_chroma(chroma)
            concentration = score / max(float(chroma.sum()), 1e-8)
            accepted = (
                concentration >= self.min_concentration
                and root_margin >= self.min_root_margin
            )
            chord = raw_chord if accepted else "N"
            quality_uncertain = quality_margin < self.min_quality_margin
            # Threshold adapter for the existing UI's 0.60/0.70 gates:
            # those correspond to root margins of 0.02/0.04. This number is
            # not a posterior probability and must never be presented as one.
            evidence = min(0.99, 0.5 + 5.0 * root_margin) if accepted else 0.0
            predictions.append(TemplatePrediction(
                sample_index=frame.sample_end,
                timestamp_seconds=frame.sample_end / self.preprocessing.sample_rate,
                chord=chord,
                confidence=evidence,
                changed=chord != self._last_chord,
                raw_chord=raw_chord,
                accepted=accepted,
                quality_uncertain=quality_uncertain,
                score=score,
                concentration=concentration,
                margin=margin,
                root_margin=root_margin,
                quality_margin=quality_margin,
            ))
            self._last_chord = chord
        return predictions

    def reset(self) -> None:
        self.extractor.reset()
        self.frames.clear()
        self._last_chord = "N"

    def close(self) -> None:
        pass

    def __enter__(self) -> "StreamingTemplateRecognizer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
