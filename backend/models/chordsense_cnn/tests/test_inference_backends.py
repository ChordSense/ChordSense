import unittest
from types import SimpleNamespace

import numpy as np

from models.chordsense_cnn.inference_backends import HailoInferenceBackend


class HailoOutputLifetimeTests(unittest.TestCase):
    def test_inference_results_remain_stable_after_next_hardware_run(self):
        # HailoRT writes each result into the same bound output buffer.
        backend = HailoInferenceBackend.__new__(HailoInferenceBackend)
        backend._closed = False
        backend._input_shape = (1, 1, 12, 15)
        backend._class_count = 25
        backend._hailo_input = np.zeros((12, 15, 1), dtype=np.float32)
        backend._hailo_output = np.zeros(25, dtype=np.float32)
        backend._bindings = object()
        runs = 0

        def run(_bindings, _timeout):
            nonlocal runs
            runs += 1
            backend._hailo_output.fill(runs)

        backend._configured_model = SimpleNamespace(run=run)
        features = np.zeros((1, 1, 12, 15), dtype=np.float32)
        first = backend.infer(features)
        second = backend.infer(features)

        self.assertEqual(first[0, 0], 1.0)
        self.assertEqual(second[0, 0], 2.0)
        self.assertFalse(np.shares_memory(first, second))


if __name__ == "__main__":
    unittest.main()
