import unittest

import numpy as np

from models.chordsense_cnn.smoother import (
    causal_smooth_predictions,
    final_prediction,
    smooth_predictions,
)


class SmootherTests(unittest.TestCase):
    def test_centered_smoother_requires_an_odd_positive_window(self):
        predictions = np.array([0, 1, 1], dtype=np.int64)
        for invalid in (0, 2, -1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                smooth_predictions(predictions, vote_window=invalid)

    def test_causal_smoothing_does_not_depend_on_future_predictions(self):
        prefix = np.array([1, 1, 2, 2], dtype=np.int64)
        first = causal_smooth_predictions(
            np.concatenate([prefix, [3, 3, 3]]),
            vote_window=3,
        )
        second = causal_smooth_predictions(
            np.concatenate([prefix, [9, 9, 9]]),
            vote_window=3,
        )
        np.testing.assert_array_equal(first[: len(prefix)], second[: len(prefix)])

    def test_final_prediction_uses_all_frames_and_preserves_rests(self):
        labels = np.array([24, 0, 0, 24, 24, 1], dtype=np.int64)
        confidence = np.linspace(0.6, 0.9, len(labels), dtype=np.float32)

        result = final_prediction(
            labels,
            np.zeros(100, dtype=np.float32),
            frame_confidences=confidence,
        )

        self.assertEqual(
            result["segments"],
            [(0, 1, 24), (1, 3, 0), (3, 5, 24), (5, 6, 1)],
        )
        np.testing.assert_array_equal(result["frame_labels"], labels)
        np.testing.assert_allclose(result["frame_confidences"], confidence)
        self.assertEqual(result["onset_frames"].size, 0)


if __name__ == "__main__":
    unittest.main()
