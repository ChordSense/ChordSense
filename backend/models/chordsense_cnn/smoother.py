import librosa
import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view
from typing import TypedDict

from .audio_processing import DEFAULT_PREPROCESSING_CONFIG, PreprocessingConfig
from .config import CHORD_CLASSES, POST_ONSET_LENGTH, POST_ONSET_OFFSET, VOTE_WINDOW


IntArray = npt.NDArray[np.int64]
FloatArray = npt.NDArray[np.float32]


class PredictionResult(TypedDict):
    frame_labels: IntArray
    segments: list[tuple[int, int, int]]
    onset_frames: IntArray


def _majority_label(labels: IntArray) -> int:
    """Return the most common non-negative label, preferring the lowest on ties."""
    return int(np.bincount(labels).argmax())


def smooth_predictions(predictions: IntArray, vote_window: int = VOTE_WINDOW) -> IntArray:
    half_window = vote_window // 2
    padded = np.asarray(np.pad(predictions, half_window, mode="edge"), dtype=np.int64)
    windows = np.asarray(sliding_window_view(padded, vote_window), dtype=np.int64)
    return np.asarray([_majority_label(window) for window in windows], dtype=np.int64)


def final_prediction(
    smoothed_predictions: IntArray,
    analysis_waveform: FloatArray,
    preprocessing: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> PredictionResult:
    noise_index = CHORD_CLASSES.index("Noise")
    onsets = np.asarray(
        librosa.onset.onset_detect(
            y=analysis_waveform,
            sr=preprocessing.sample_rate,
            hop_length=preprocessing.hop_length,
            backtrack=True,
        ),
        dtype=np.int64,
    )

    frame_labels = np.empty_like(smoothed_predictions)
    segments: list[tuple[int, int, int]] = []
    current_chord = noise_index
    current_start = 0

    for onset_frame in onsets.tolist():
        start = onset_frame + POST_ONSET_OFFSET
        end = min(start + POST_ONSET_LENGTH, len(smoothed_predictions))
        if start >= len(smoothed_predictions):
            break

        post_onset_chord = _majority_label(smoothed_predictions[start:end])
        if post_onset_chord != current_chord:
            segments.append((current_start, int(onset_frame), current_chord))
            current_start = int(onset_frame)
            current_chord = post_onset_chord

    segments.append((current_start, len(smoothed_predictions), current_chord))
    for start, end, label in segments:
        frame_labels[start:end] = label

    return {
        "frame_labels": frame_labels,
        "segments": segments,
        "onset_frames": onsets,
    }
