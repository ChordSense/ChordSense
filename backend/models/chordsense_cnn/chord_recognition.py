import sys
from dataclasses import asdict
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import numpy as np
import torch

from models.chordsense_cnn.audio_processing import (
    AudioBuffer,
    DEFAULT_PREPROCESSING_CONFIG,
    PreprocessedAudio,
    PreprocessingConfig,
    create_feature_windows,
    load_audio_file,
    preprocess_audio,
)
from models.chordsense_cnn.config import (
    CHORD_CLASSES,
    NUM_CLASSES,
    RECORDING_OUTPUT_FILE,
    VOTE_WINDOW,
)
from models.chordsense_cnn.model import build_model
from models.chordsense_cnn.smoother import final_prediction, smooth_predictions


class ChordRecognizer:
    def __init__(
        self,
        checkpoint_path: str | Path,
        preprocessing: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
    ):
        self.preprocessing = preprocessing
        self.device = self._select_device()
        self.model = build_model(num_classes=NUM_CLASSES).to(self.device)
        checkpoint = torch.load(checkpoint_path, weights_only=True, map_location=self.device)
        checkpoint_preprocessing = checkpoint.get("preprocessing")
        if (
            checkpoint_preprocessing is not None
            and checkpoint_preprocessing != asdict(preprocessing)
        ):
            raise ValueError("Checkpoint preprocessing configuration does not match inference")
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.eval()
        self.label_names = CHORD_CLASSES

    @staticmethod
    def _select_device() -> str:
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
        return self.from_audio(load_audio_file(audio_path), output_path)

    def from_audio(
        self,
        audio: AudioBuffer,
        output_path: str | Path = RECORDING_OUTPUT_FILE,
    ) -> bool:
        return self.from_preprocessed(preprocess_audio(audio, self.preprocessing), output_path)

    def from_preprocessed(
        self,
        processed: PreprocessedAudio,
        output_path: str | Path = RECORDING_OUTPUT_FILE,
    ) -> bool:
        return self.from_chroma(
            processed.analysis_waveform,
            processed.chroma,
            output_path,
        )

    def from_chroma(
        self,
        analysis_waveform: np.ndarray,
        chroma: np.ndarray,
        output_path: str | Path = RECORDING_OUTPUT_FILE,
    ) -> bool:
        windows = create_feature_windows(chroma, self.preprocessing).values
        features = torch.from_numpy(windows).unsqueeze(1).to(self.device)
        with torch.no_grad():
            predictions = self.model(features).argmax(dim=1).cpu().numpy()
        smoothed = smooth_predictions(predictions, vote_window=VOTE_WINDOW)
        model_predictions = final_prediction(
            smoothed,
            analysis_waveform,
            self.preprocessing,
        )
        return self.generate_lab_file(model_predictions, output_path)

    def generate_lab_file(
        self,
        model_predictions: dict,
        output_path: str | Path = RECORDING_OUTPUT_FILE,
        min_duration: float = 0.4,
    ) -> bool:
        segments = model_predictions["segments"]
        if not segments:
            return False

        noise_index = CHORD_CLASSES.index("Noise")
        frame_seconds = self.preprocessing.hop_length / self.preprocessing.sample_rate
        normalized = [
            [start * frame_seconds, end * frame_seconds, label]
            for start, end, label in segments
        ]

        first_chord = next(
            (i for i, segment in enumerate(normalized) if segment[2] != noise_index),
            None,
        )
        last_chord = next(
            (i for i in range(len(normalized) - 1, -1, -1) if normalized[i][2] != noise_index),
            None,
        )
        if first_chord is None:
            normalized = [[normalized[0][0], normalized[-1][1], noise_index]]
        else:
            for i in range(first_chord + 1, last_chord):
                if normalized[i][2] == noise_index:
                    normalized[i][2] = normalized[i - 1][2]

        normalized = self._merge_adjacent(normalized)
        changed = True
        while changed and len(normalized) > 1:
            changed = False
            for i, segment in enumerate(normalized):
                edge_noise = segment[2] == noise_index and (i == 0 or i == len(normalized) - 1)
                if edge_noise or segment[1] - segment[0] >= min_duration:
                    continue
                if i > 0:
                    normalized[i - 1][1] = segment[1]
                else:
                    normalized[i + 1][0] = segment[0]
                normalized.pop(i)
                normalized = self._merge_adjacent(normalized)
                changed = True
                break

        try:
            with Path(output_path).open("w", encoding="utf-8") as output:
                for start, end, label in normalized:
                    name = "N" if label == noise_index else self.label_names[label]
                    output.write(f"{start}\t{end}\t{name}\n")
        except OSError:
            return False
        return True

    @staticmethod
    def _merge_adjacent(segments: list[list]) -> list[list]:
        merged = [segments[0]]
        for segment in segments[1:]:
            if segment[2] == merged[-1][2]:
                merged[-1][1] = segment[1]
            else:
                merged.append(segment)
        return merged


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 2:
        print("Usage: python chord_recognition.py <audio_path> <output_path>")
        return 1
    recognizer = ChordRecognizer("checkpoints/latest_chord_cnn.pth")
    recognizer.from_file(args[0], args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
