import tempfile
import unittest
from pathlib import Path

import numpy as np

from models.chordsense_cnn.audio_processing import DEFAULT_PREPROCESSING_CONFIG
from models.chordsense_cnn.chord_recognition import (
    ChordRecognizer,
    LIVE_POSTPROCESSING,
    RecognitionResult,
    RecognitionSegment,
)
from models.chordsense_cnn.config import CHORD_CLASSES
from models.chordsense_cnn.smoother import final_prediction


def recognizer_without_model() -> ChordRecognizer:
    recognizer = ChordRecognizer.__new__(ChordRecognizer)
    recognizer.preprocessing = DEFAULT_PREPROCESSING_CONFIG
    recognizer.label_names = CHORD_CLASSES
    recognizer.postprocessing = LIVE_POSTPROCESSING
    return recognizer


class ChordRecognitionTests(unittest.TestCase):
    def test_nearest_window_alignment_covers_leading_and_trailing_frames(self):
        probabilities = np.array([[0.8, 0.2], [0.1, 0.9]], dtype=np.float32)
        aligned = ChordRecognizer._align_window_probabilities(
            probabilities,
            np.array([2, 3], dtype=np.int64),
            source_frame_count=5,
        )

        np.testing.assert_array_equal(aligned, probabilities[[0, 0, 0, 1, 1]])

    def test_normalized_segments_cover_exact_audio_duration_and_keep_noise(self):
        recognizer = recognizer_without_model()
        noise = CHORD_CLASSES.index("Noise")
        labels = np.array([0, 0, noise, noise, 1, 1], dtype=np.int64)
        confidence = np.array([0.9, 0.8, 0.7, 0.6, 0.75, 0.85], dtype=np.float32)
        predictions = final_prediction(
            labels,
            np.zeros(10, dtype=np.float32),
            frame_confidences=confidence,
        )
        duration = 0.731

        segments = recognizer.normalize_segments(predictions, duration)

        self.assertEqual([segment.label for segment in segments], [0, noise, 1])
        self.assertEqual(segments[0].start, 0.0)
        self.assertEqual(segments[-1].end, duration)
        self.assertAlmostEqual(segments[0].confidence, 0.85, places=6)
        self.assertAlmostEqual(segments[1].confidence, 0.65, places=6)

    def test_lab_writer_uses_exact_result_boundaries(self):
        recognizer = recognizer_without_model()
        noise = CHORD_CLASSES.index("Noise")
        labels = np.array([0, noise], dtype=np.int64)
        predictions = final_prediction(
            labels,
            np.zeros(10, dtype=np.float32),
            frame_confidences=np.array([0.9, 0.8], dtype=np.float32),
        )
        segments = recognizer.normalize_segments(predictions, duration_seconds=0.5)
        result = RecognitionResult(
            segments=[
                RecognitionSegment(
                    segment.start,
                    segment.end,
                    "N" if segment.label == noise else CHORD_CLASSES[segment.label],
                    segment.confidence,
                )
                for segment in segments
            ],
            duration_seconds=0.5,
            processing_seconds=0.1,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.lab"
            self.assertTrue(recognizer.write_lab_file(result, path))
            lines = path.read_text().splitlines()

        self.assertEqual(lines[-1].split()[1], "0.5")
        self.assertEqual(lines[-1].split()[2], "N")


if __name__ == "__main__":
    unittest.main()
