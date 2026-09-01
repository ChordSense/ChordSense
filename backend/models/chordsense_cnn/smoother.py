import librosa
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.stats import mode

from .audio_processing import DEFAULT_PREPROCESSING_CONFIG, PreprocessingConfig
from .config import CHORD_CLASSES, POST_ONSET_LENGTH, POST_ONSET_OFFSET, VOTE_WINDOW


def smooth_predictions(predictions: np.ndarray, vote_window: int = VOTE_WINDOW) -> np.ndarray:
    half_window = vote_window // 2
    padded = np.pad(predictions, half_window, mode="edge")
    windows = sliding_window_view(padded, vote_window)
    return mode(windows, axis=1, keepdims=False).mode


def final_prediction(
    smoothed_predictions: np.ndarray,
    analysis_waveform: np.ndarray,
    preprocessing: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> dict[str, np.ndarray | list[tuple[int, int, int]]]:
    noise_index = CHORD_CLASSES.index("Noise")
    onsets = librosa.onset.onset_detect(
        y=analysis_waveform,
        sr=preprocessing.sample_rate,
        hop_length=preprocessing.hop_length,
        backtrack=True,
    )

    frame_labels = np.empty_like(smoothed_predictions)
    segments = []
    current_chord = noise_index
    current_start = 0

    for onset_frame in onsets:
        start = onset_frame + POST_ONSET_OFFSET
        end = min(start + POST_ONSET_LENGTH, len(smoothed_predictions))
        if start >= len(smoothed_predictions):
            break

        post_onset_chord = int(mode(smoothed_predictions[start:end], keepdims=False).mode)
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
