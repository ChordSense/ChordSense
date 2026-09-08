import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view
from typing import TypedDict

from .audio_processing import DEFAULT_PREPROCESSING_CONFIG, PreprocessingConfig
from .config import VOTE_WINDOW


IntArray = npt.NDArray[np.int64]
FloatArray = npt.NDArray[np.float32]


class PredictionResult(TypedDict):
    frame_labels: IntArray
    frame_confidences: FloatArray
    segments: list[tuple[int, int, int]]
    onset_frames: IntArray


def _majority_label(labels: IntArray) -> int:
    """Return the most common non-negative label, preferring the lowest on ties."""
    return int(np.bincount(labels).argmax())


def smooth_predictions(predictions: IntArray, vote_window: int = VOTE_WINDOW) -> IntArray:
    predictions = np.asarray(predictions, dtype=np.int64)
    if predictions.ndim != 1 or predictions.size == 0:
        raise ValueError("predictions must be a non-empty one-dimensional array")
    if vote_window <= 0 or vote_window % 2 == 0:
        raise ValueError("vote_window must be a positive odd integer")
    half_window = vote_window // 2
    padded = np.asarray(np.pad(predictions, half_window, mode="edge"), dtype=np.int64)
    windows = np.asarray(sliding_window_view(padded, vote_window), dtype=np.int64)
    return np.asarray([_majority_label(window) for window in windows], dtype=np.int64)


def causal_smooth_predictions(
    predictions: IntArray,
    vote_window: int = 3,
) -> IntArray:
    """Majority-vote using only the current and preceding predictions."""
    predictions = np.asarray(predictions, dtype=np.int64)
    if predictions.ndim != 1 or predictions.size == 0:
        raise ValueError("predictions must be a non-empty one-dimensional array")
    if vote_window <= 0:
        raise ValueError("vote_window must be positive")
    smoothed = np.empty_like(predictions)
    for index in range(len(predictions)):
        start = max(0, index - vote_window + 1)
        smoothed[index] = _majority_label(predictions[start : index + 1])
    return smoothed


def final_prediction(
    smoothed_predictions: IntArray,
    analysis_waveform: FloatArray,
    preprocessing: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
    frame_confidences: FloatArray | None = None,
) -> PredictionResult:
    """Convert every model frame into contiguous segments.

    The previous implementation changed labels only at detected onsets. A
    missed onset therefore turned an otherwise confident chord prediction into
    an all-Noise transcript. Direct run-length encoding is deterministic,
    retains rests, and is compatible with both offline and causal front ends.
    ``analysis_waveform`` and ``preprocessing`` remain in the signature for API
    compatibility with older callers.
    """
    del analysis_waveform, preprocessing
    frame_labels = np.asarray(smoothed_predictions, dtype=np.int64)
    if frame_labels.ndim != 1 or frame_labels.size == 0:
        raise ValueError("smoothed_predictions must be a non-empty one-dimensional array")
    if frame_confidences is None:
        confidences = np.ones(len(frame_labels), dtype=np.float32)
    else:
        confidences = np.asarray(frame_confidences, dtype=np.float32)
        if confidences.shape != frame_labels.shape:
            raise ValueError("frame_confidences must match smoothed_predictions")
        if not np.isfinite(confidences).all():
            raise ValueError("frame_confidences contains non-finite values")

    segments: list[tuple[int, int, int]] = []
    current_chord = int(frame_labels[0])
    current_start = 0
    for frame_index in range(1, len(frame_labels)):
        label = int(frame_labels[frame_index])
        if label != current_chord:
            segments.append((current_start, frame_index, current_chord))
            current_start = frame_index
            current_chord = label
    segments.append((current_start, len(frame_labels), current_chord))

    return {
        "frame_labels": frame_labels.copy(),
        "frame_confidences": confidences,
        "segments": segments,
        "onset_frames": np.empty(0, dtype=np.int64),
    }
