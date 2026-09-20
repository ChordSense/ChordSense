from dataclasses import dataclass, replace
from pathlib import Path
import sys
import time
from typing import Literal

import numpy as np
import numpy.typing as npt

from .audio_processing import (
    AudioBuffer,
    DEFAULT_PREPROCESSING_CONFIG,
    PreprocessedAudio,
    PreprocessingConfig,
    create_feature_windows,
    load_audio_file,
    preprocess_audio,
)
from .config import (
    CHORD_CLASSES,
    NUM_CLASSES,
    POST_ONSET_LENGTH,
    POST_ONSET_OFFSET,
    RECORDING_OUTPUT_FILE,
    VOTE_WINDOW,
)
from .inference_backends import HailoInferenceBackend
from .smoother import (
    PredictionResult,
    causal_smooth_predictions,
    final_prediction,
    smooth_predictions,
)

MODEL_DIR = Path(__file__).resolve().parent
DEFAULT_CHECKPOINT = MODEL_DIR / "checkpoints" / "latest_chord_cnn.pth"
FloatArray = npt.NDArray[np.float32]


@dataclass(frozen=True)
class PostprocessingConfig:
    """Select smoothing and segmentation independently of model inference."""

    smoothing_mode: Literal["centered", "causal"]
    segmentation_mode: Literal["onset", "framewise"]
    vote_window: int
    minimum_chord_duration: float
    fill_internal_noise: bool
    onset_decision_offset: int
    onset_decision_window: int

    def __post_init__(self) -> None:
        if self.smoothing_mode not in {"centered", "causal"}:
            raise ValueError(f"Unsupported smoothing mode: {self.smoothing_mode}")
        if self.segmentation_mode not in {"onset", "framewise"}:
            raise ValueError(f"Unsupported segmentation mode: {self.segmentation_mode}")
        if self.vote_window <= 0:
            raise ValueError("vote_window must be positive")
        if self.smoothing_mode == "centered" and self.vote_window % 2 == 0:
            raise ValueError("centered smoothing requires an odd vote_window")
        if self.minimum_chord_duration < 0:
            raise ValueError("minimum_chord_duration cannot be negative")
        if self.onset_decision_offset < 0:
            raise ValueError("onset_decision_offset cannot be negative")
        if self.onset_decision_window <= 0:
            raise ValueError("onset_decision_window must be positive")


OFFLINE_POSTPROCESSING = PostprocessingConfig(
    smoothing_mode="centered",
    segmentation_mode="onset",
    vote_window=VOTE_WINDOW,
    minimum_chord_duration=0.4,
    fill_internal_noise=True,
    # Main indexed each prediction by its window start. Predictions are now
    # correctly aligned to the window center, so retain main's effective
    # post-onset decision point by adding half the context window.
    onset_decision_offset=(
        POST_ONSET_OFFSET + DEFAULT_PREPROCESSING_CONFIG.context_frames // 2
    ),
    onset_decision_window=POST_ONSET_LENGTH,
)

LIVE_POSTPROCESSING = PostprocessingConfig(
    smoothing_mode="causal",
    segmentation_mode="framewise",
    vote_window=3,
    minimum_chord_duration=0.0,
    fill_internal_noise=False,
    onset_decision_offset=0,
    onset_decision_window=1,
)


@dataclass
class NormalizedSegment:
    start: float
    end: float
    label: int
    confidence: float


@dataclass(frozen=True)
class RecognitionSegment:
    start: float
    end: float
    chord: str
    confidence: float


@dataclass(frozen=True)
class RecognitionResult:
    segments: list[RecognitionSegment]
    duration_seconds: float
    processing_seconds: float


class ChordRecognizer:
    def __init__(
        self,
        checkpoint_path: str | Path,
        preprocessing: PreprocessingConfig | None = None,
        confidence_threshold: float = 0.0,
        postprocessing: PostprocessingConfig = OFFLINE_POSTPROCESSING,
    ):
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self.confidence_threshold = confidence_threshold
        self.postprocessing = postprocessing
        import torch

        from .model import build_model

        self._torch = torch
        self.device = self._select_device()
        checkpoint = torch.load(checkpoint_path, weights_only=True, map_location=self.device)
        checkpoint_preprocessing = checkpoint.get("preprocessing")
        checkpoint_config = PreprocessingConfig(**(checkpoint_preprocessing or {}))
        if preprocessing is None:
            preprocessing = checkpoint_config
        elif checkpoint_preprocessing is not None and checkpoint_config != preprocessing:
            raise ValueError("Checkpoint preprocessing configuration does not match inference")
        self.preprocessing = preprocessing
        self.temperature = float(checkpoint.get("calibration", {}).get("temperature", 1.0))
        if self.temperature <= 0:
            raise ValueError("Checkpoint calibration temperature must be positive")
        self.model = build_model(num_classes=NUM_CLASSES).to(self.device)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.eval()
        self.label_names = CHORD_CLASSES

    @staticmethod
    def _select_device() -> str:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def from_file(
        self,
        audio_path: str | Path,
        output_path: str | Path = RECORDING_OUTPUT_FILE,
    ) -> bool:
        audio_path = Path(audio_path)
        output_path = Path(output_path)
        if audio_path.suffix.lower() != ".wav" or output_path.suffix.lower() != ".lab":
            raise ValueError("Audio path must be a .wav file and output path must be a .lab file")
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        result = self.analyze_file(audio_path)
        return self.write_lab_file(result, output_path)

    def analyze_file(self, audio_path: str | Path) -> RecognitionResult:
        audio_path = Path(audio_path)
        if audio_path.suffix.lower() != ".wav":
            raise ValueError("Audio path must be a .wav file")
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        started = time.perf_counter()
        result = self.analyze_audio(load_audio_file(audio_path))
        return replace(result, processing_seconds=time.perf_counter() - started)

    def from_audio(
        self,
        audio: AudioBuffer,
        output_path: str | Path = RECORDING_OUTPUT_FILE,
    ) -> bool:
        result = self.analyze_audio(audio)
        return self.write_lab_file(result, output_path)

    def analyze_audio(self, audio: AudioBuffer) -> RecognitionResult:
        started = time.perf_counter()
        result = self.analyze_preprocessed(preprocess_audio(audio, self.preprocessing))
        return replace(result, processing_seconds=time.perf_counter() - started)

    def from_preprocessed(
        self,
        processed: PreprocessedAudio,
        output_path: str | Path = RECORDING_OUTPUT_FILE,
    ) -> bool:
        result = self.analyze_preprocessed(processed)
        return self.write_lab_file(result, output_path)

    def analyze_preprocessed(self, processed: PreprocessedAudio) -> RecognitionResult:
        return self.analyze_chroma(
            processed.analysis_waveform,
            processed.chroma,
            processed.duration_seconds,
        )

    def from_chroma(
        self,
        analysis_waveform: FloatArray,
        chroma: FloatArray,
        output_path: str | Path = RECORDING_OUTPUT_FILE,
    ) -> bool:
        duration_seconds = len(analysis_waveform) / self.preprocessing.sample_rate
        result = self.analyze_chroma(analysis_waveform, chroma, duration_seconds)
        return self.write_lab_file(result, output_path)

    def analyze_chroma(
        self,
        analysis_waveform: FloatArray,
        chroma: FloatArray,
        duration_seconds: float,
    ) -> RecognitionResult:
        window_batch = create_feature_windows(chroma, self.preprocessing)
        logits = self._infer_logits(window_batch.values)
        scaled_logits = logits / self.temperature
        scaled_logits -= scaled_logits.max(axis=1, keepdims=True)
        exponentials = np.exp(scaled_logits)
        probabilities = exponentials / exponentials.sum(axis=1, keepdims=True)

        frame_probabilities = self._align_window_probabilities(
            probabilities,
            window_batch.center_frames,
            window_batch.source_frame_count,
        )
        predictions = np.asarray(frame_probabilities.argmax(axis=1), dtype=np.int64)
        confidence = np.asarray(frame_probabilities.max(axis=1), dtype=np.float32)
        noise_index = CHORD_CLASSES.index("Noise")
        predictions[confidence < self.confidence_threshold] = noise_index
        if self.postprocessing.smoothing_mode == "causal":
            smoothed = causal_smooth_predictions(
                predictions,
                vote_window=self.postprocessing.vote_window,
            )
        else:
            smoothed = smooth_predictions(
                predictions,
                vote_window=self.postprocessing.vote_window,
            )
        selected_confidence = np.asarray(
            frame_probabilities[np.arange(len(smoothed)), smoothed],
            dtype=np.float32,
        )
        model_predictions = final_prediction(
            smoothed,
            analysis_waveform,
            self.preprocessing,
            selected_confidence,
            segmentation_mode=self.postprocessing.segmentation_mode,
            onset_decision_offset=self.postprocessing.onset_decision_offset,
            onset_decision_window=self.postprocessing.onset_decision_window,
        )
        segments = self.normalize_segments(
            model_predictions,
            duration_seconds,
            min_duration=self.postprocessing.minimum_chord_duration,
            fill_internal_noise=self.postprocessing.fill_internal_noise,
        )
        return RecognitionResult(
            segments=[
                RecognitionSegment(
                    start=segment.start,
                    end=segment.end,
                    chord=(
                        "N"
                        if segment.label == noise_index
                        else self.label_names[segment.label]
                    ),
                    confidence=segment.confidence,
                )
                for segment in segments
            ],
            duration_seconds=duration_seconds,
            processing_seconds=0.0,
        )

    def _infer_logits(self, windows: FloatArray) -> FloatArray:
        features = self._torch.from_numpy(windows).unsqueeze(1).to(self.device)
        with self._torch.inference_mode():
            logits = self.model(features).detach().cpu().numpy()
        return np.ascontiguousarray(logits, dtype=np.float32)

    @staticmethod
    def _align_window_probabilities(
        probabilities: FloatArray,
        center_frames: npt.NDArray[np.int64],
        source_frame_count: int,
    ) -> FloatArray:
        """Assign every source frame the nearest centered window output."""
        if probabilities.ndim != 2 or len(probabilities) != len(center_frames):
            raise ValueError("Window probabilities and center frames do not align")
        frames = np.arange(source_frame_count, dtype=np.int64)
        right = np.searchsorted(center_frames, frames, side="left")
        right = np.clip(right, 0, len(center_frames) - 1)
        left = np.clip(right - 1, 0, len(center_frames) - 1)
        choose_left = np.abs(frames - center_frames[left]) <= np.abs(
            center_frames[right] - frames
        )
        nearest = np.where(choose_left, left, right)
        return np.ascontiguousarray(probabilities[nearest], dtype=np.float32)

    def generate_lab_file(
        self,
        model_predictions: PredictionResult,
        output_path: str | Path = RECORDING_OUTPUT_FILE,
        duration_seconds: float | None = None,
        min_duration: float | None = None,
        fill_internal_noise: bool | None = None,
    ) -> bool:
        if duration_seconds is None:
            duration_seconds = (
                len(model_predictions["frame_labels"])
                * self.preprocessing.hop_length
                / self.preprocessing.sample_rate
            )
        if min_duration is None:
            min_duration = self.postprocessing.minimum_chord_duration
        if fill_internal_noise is None:
            fill_internal_noise = self.postprocessing.fill_internal_noise
        segments = self.normalize_segments(
            model_predictions,
            duration_seconds,
            min_duration,
            fill_internal_noise,
        )
        noise_index = CHORD_CLASSES.index("Noise")
        result = RecognitionResult(
            segments=[
                RecognitionSegment(
                    segment.start,
                    segment.end,
                    "N" if segment.label == noise_index else self.label_names[segment.label],
                    segment.confidence,
                )
                for segment in segments
            ],
            duration_seconds=duration_seconds,
            processing_seconds=0.0,
        )
        return self.write_lab_file(result, output_path)

    def normalize_segments(
        self,
        model_predictions: PredictionResult,
        duration_seconds: float,
        min_duration: float = 0.0,
        fill_internal_noise: bool = False,
    ) -> list[NormalizedSegment]:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if min_duration < 0:
            raise ValueError("min_duration cannot be negative")
        raw_segments = model_predictions["segments"]
        if not raw_segments:
            return []
        confidences = model_predictions["frame_confidences"]
        frame_seconds = self.preprocessing.hop_length / self.preprocessing.sample_rate
        normalized = []
        for index, (start, end, label) in enumerate(raw_segments):
            segment_start = min(duration_seconds, start * frame_seconds)
            segment_end = (
                duration_seconds
                if index == len(raw_segments) - 1
                else min(duration_seconds, end * frame_seconds)
            )
            if segment_end <= segment_start:
                continue
            normalized.append(
                NormalizedSegment(
                    start=segment_start,
                    end=segment_end,
                    label=label,
                    confidence=float(np.mean(confidences[start:end])),
                )
            )
        if not normalized:
            return []

        noise_index = CHORD_CLASSES.index("Noise")
        if fill_internal_noise:
            first_chord = next(
                (index for index, segment in enumerate(normalized) if segment.label != noise_index),
                None,
            )
            last_chord = next(
                (
                    index
                    for index in range(len(normalized) - 1, -1, -1)
                    if normalized[index].label != noise_index
                ),
                None,
            )
            if first_chord is None:
                normalized = [
                    NormalizedSegment(
                        normalized[0].start,
                        normalized[-1].end,
                        noise_index,
                        float(np.mean(confidences)),
                    )
                ]
            else:
                assert last_chord is not None
                for index in range(first_chord + 1, last_chord):
                    if normalized[index].label == noise_index:
                        normalized[index].label = normalized[index - 1].label

        normalized = self._merge_adjacent(normalized)
        changed = True
        while min_duration > 0 and changed and len(normalized) > 1:
            changed = False
            for index, segment in enumerate(normalized):
                edge_noise = segment.label == noise_index and (
                    index == 0 or index == len(normalized) - 1
                )
                if edge_noise or segment.end - segment.start >= min_duration:
                    continue
                if index > 0:
                    normalized[index - 1].end = segment.end
                else:
                    normalized[index + 1].start = segment.start
                normalized.pop(index)
                normalized = self._merge_adjacent(normalized)
                changed = True
                break
        return normalized

    @staticmethod
    def write_lab_file(
        result: RecognitionResult,
        output_path: str | Path = RECORDING_OUTPUT_FILE,
    ) -> bool:
        if not result.segments:
            return False
        try:
            with Path(output_path).open("w", encoding="utf-8") as output:
                for segment in result.segments:
                    output.write(f"{segment.start}\t{segment.end}\t{segment.chord}\n")
        except OSError:
            return False
        return True

    @staticmethod
    def _merge_adjacent(segments: list[NormalizedSegment]) -> list[NormalizedSegment]:
        merged = [segments[0]]
        for segment in segments[1:]:
            if segment.label == merged[-1].label:
                first_duration = merged[-1].end - merged[-1].start
                second_duration = segment.end - segment.start
                total_duration = first_duration + second_duration
                if total_duration > 0:
                    merged[-1].confidence = (
                        merged[-1].confidence * first_duration
                        + segment.confidence * second_duration
                    ) / total_duration
                merged[-1].end = segment.end
            else:
                merged.append(segment)
        return merged


class HailoChordRecognizer(ChordRecognizer):
    """Run the non-causal whole-recording pipeline with HEF inference."""

    def __init__(
        self,
        hef_path: str | Path,
        preprocessing: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
        confidence_threshold: float = 0.0,
        temperature: float = 1.0,
        postprocessing: PostprocessingConfig = OFFLINE_POSTPROCESSING,
    ):
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if temperature <= 0:
            raise ValueError("temperature must be positive")
        self.confidence_threshold = confidence_threshold
        self.postprocessing = postprocessing
        self.preprocessing = preprocessing
        self.temperature = temperature
        self.label_names = CHORD_CLASSES
        self.backend = HailoInferenceBackend(
            hef_path,
            input_shape=(
                1,
                1,
                preprocessing.n_chroma,
                preprocessing.context_frames,
            ),
            class_count=NUM_CLASSES,
        )

    def _infer_logits(self, windows: FloatArray) -> FloatArray:
        features = np.ascontiguousarray(windows[:, None, :, :], dtype=np.float32)
        logits = np.empty((len(features), NUM_CLASSES), dtype=np.float32)
        for index, feature_window in enumerate(features):
            logits[index] = self.backend.infer(feature_window[None, ...])[0]
        return logits

    def close(self) -> None:
        self.backend.close()


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 2:
        print("Usage: python chord_recognition.py <audio_path> <output_path>")
        return 1
    recognizer = ChordRecognizer(DEFAULT_CHECKPOINT)
    recognizer.from_file(args[0], args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
