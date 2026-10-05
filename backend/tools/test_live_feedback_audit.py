import unittest

import numpy as np

from models.chordsense_cnn.benchmark_template import replay as reference_replay
from models.chordsense_cnn.streaming import DEFAULT_STREAMING_PREPROCESSING_CONFIG
from models.chordsense_cnn.template import DEFAULT_TEMPLATE_PREPROCESSING_CONFIG
from tools.audit_live_feedback import replay
from tools.probe_iod_stream import summarize_frames


class LiveFeedbackAuditTests(unittest.TestCase):
    def test_replay_matches_reference_gate_for_both_feature_contracts(self):
        rate = 22_050
        times = np.arange(rate * 2) / rate
        tones = sum(np.sin(2 * np.pi * hz * times) for hz in (110, 138.59, 164.81)) * 0.04
        samples = np.round(np.concatenate((np.full(rate * 2, -0.197), tones - 0.197,
                                          np.full(rate * 2, -0.197))) * 32768).astype("<i2")
        for config in (DEFAULT_STREAMING_PREPROCESSING_CONFIG, DEFAULT_TEMPLATE_PREPROCESSING_CONFIG):
            with self.subTest(config=config):
                expected = reference_replay(samples, preprocessing=config)
                observed, _ = replay(samples, config)
                self.assertEqual([(x["seconds"], x["chord"], x["input_quality"]) for x in observed],
                                 [(x["seconds"], x["chord"], x["input_quality"]) for x in expected])
                self.assertTrue(all(x["delivery_seconds"] >= x["seconds"] for x in observed))

    def test_probe_counts_loss_without_confusing_it_with_clock_drift(self):
        frames = [{"sample_index": i, "sample_end": i + 441, "sample_count": 441,
                   "captured_at_ns": t, "received_at_ns": t + 200_000,
                   "ac_rms": 0.01, "dc_offset": -0.2, "clipping_fraction": 0}
                  for i, t in ((0, 1_000_000_000), (441, 1_020_000_000),
                               (1323, 1_060_000_000))]
        result = summarize_frames(frames)
        self.assertEqual(result["missing_output_samples"], 441)
        self.assertEqual(result["output_grid_rate_hz"], 22_050)
        self.assertEqual(result["receive_age_ms"]["p95"], 0.2)
        self.assertIsNone(result["native_adc_rate_hz"])

    def test_probe_without_timestamps_does_not_invent_a_rate(self):
        self.assertIsNone(summarize_frames([])["output_grid_rate_hz"])


if __name__ == "__main__":
    unittest.main()
