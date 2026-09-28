from __future__ import annotations

import unittest

import numpy as np

from backend.tools.lstm_baseline import (
    HEAD_SIZES,
    _array_sha256,
    _compare_archive_payloads,
    _compare_numeric,
)


class LstmBaselineTest(unittest.TestCase):
    def test_array_hash_includes_dtype_and_shape(self) -> None:
        values = np.arange(6, dtype=np.float32)
        self.assertNotEqual(_array_sha256(values), _array_sha256(values.reshape(2, 3)))
        self.assertNotEqual(
            _array_sha256(values), _array_sha256(values.astype(np.float64))
        )

    def test_numeric_comparison_reports_error(self) -> None:
        reference = np.asarray([0.0, 1.0, 2.0], dtype=np.float32)
        candidate = np.asarray([0.0, 1.25, 1.5], dtype=np.float32)
        result = _compare_numeric(reference, candidate)
        self.assertTrue(result["shape_match"])
        self.assertAlmostEqual(result["maximum_absolute_error"], 0.5)
        self.assertFalse(result["array_equal"])

    def test_final_output_gate_and_head_agreement(self) -> None:
        probabilities = np.zeros((2, sum(HEAD_SIZES)), dtype=np.float32)
        reference = {
            "ensemble_probabilities": probabilities,
            "decoded_tags": np.asarray(["C", "G"], dtype=np.str_),
            "lab_start_seconds": np.asarray([0.0, 1.0]),
            "lab_end_seconds": np.asarray([1.0, 2.0]),
            "lab_chords": np.asarray(["C", "G"], dtype=np.str_),
        }
        candidate = {key: value.copy() for key, value in reference.items()}
        equal = _compare_archive_payloads(reference, candidate)
        self.assertTrue(equal["final_output_equal"])
        self.assertEqual(
            equal["arrays"]["ensemble_probabilities"]["head_argmax_agreement"]["triad"],
            1.0,
        )

        candidate["decoded_tags"][1] = "F"
        changed = _compare_archive_payloads(reference, candidate)
        self.assertFalse(changed["final_output_equal"])
        self.assertEqual(changed["arrays"]["decoded_tags"]["frame_agreement"], 0.5)


if __name__ == "__main__":
    unittest.main()
