import unittest

import torch

from models.chordsense_cnn.config import NUM_CLASSES
from models.chordsense_cnn.model import MODEL_NAMES, build_model


class ModelTests(unittest.TestCase):
    def test_all_model_candidates_emit_the_existing_vocabulary(self):
        inputs = torch.zeros((2, 1, 12, 15))
        for model_name in MODEL_NAMES:
            with self.subTest(model_name=model_name):
                outputs = build_model(model_name=model_name)(inputs)
                self.assertEqual(tuple(outputs.shape), (2, NUM_CLASSES))

    def test_baseline_parameter_count_does_not_change(self):
        parameter_count = sum(
            parameter.numel() for parameter in build_model().parameters()
        )
        self.assertEqual(parameter_count, 21_913)

    def test_pitch_aware_candidates_remain_small(self):
        for model_name in ("pitch_aware", "depthwise_pitch"):
            with self.subTest(model_name=model_name):
                count = sum(
                    parameter.numel()
                    for parameter in build_model(model_name=model_name).parameters()
                )
                self.assertLess(count, 25_000)

    def test_candidates_support_shorter_streaming_context(self):
        inputs = torch.zeros((2, 1, 12, 4))
        for model_name in ("pitch_aware", "depthwise_pitch"):
            with self.subTest(model_name=model_name):
                outputs = build_model(model_name=model_name)(inputs)
                self.assertEqual(tuple(outputs.shape), (2, NUM_CLASSES))

    def test_unknown_model_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown model_name"):
            build_model(model_name="unknown")


if __name__ == "__main__":
    unittest.main()
