import unittest
from dataclasses import replace

import numpy as np

from models.chordsense_cnn.template import (
    PITCH_CLASS,
    TRIAD_LABELS,
    StreamingTemplateRecognizer,
    score_chroma,
)
from models.chordsense_cnn.streaming import DEFAULT_STREAMING_PREPROCESSING_CONFIG


class TemplateScoreTests(unittest.TestCase):
    def test_each_major_minor_triad_has_its_own_best_template(self):
        for label in TRIAD_LABELS:
            with self.subTest(label=label):
                minor = label.endswith("m")
                root = label[:-1] if minor else label
                pitch = PITCH_CLASS[root]
                chroma = np.zeros(12, dtype=np.float32)
                chroma[[pitch, (pitch + (3 if minor else 4)) % 12, (pitch + 7) % 12]] = 1
                predicted, score, margin, root_margin, quality_margin = score_chroma(chroma)
                self.assertEqual(predicted, label)
                self.assertEqual(score, 3.0)
                self.assertGreater(margin, 0)
                self.assertGreater(root_margin, 0)
                self.assertGreater(quality_margin, 0)

    def test_root_can_be_clear_while_quality_is_ambiguous(self):
        # A G chord with a weak third should carry root evidence, but not
        # strong evidence that its quality is major rather than minor.
        chroma = np.zeros(12, dtype=np.float32)
        chroma[PITCH_CLASS["G"]] = 1.0
        chroma[PITCH_CLASS["D"]] = 0.95
        chroma[PITCH_CLASS["B"]] = 0.10
        chroma[PITCH_CLASS["A#"]] = 0.09
        label, _, _, root_margin, quality_margin = score_chroma(chroma)
        self.assertEqual(label, "G")
        self.assertGreater(root_margin, 0.05)
        self.assertLess(quality_margin, 0.02)

    def test_chunk_sizes_do_not_change_prediction_sequence(self):
        rate = 22_050
        times = np.arange(rate * 2, dtype=np.float32) / rate
        frequencies = (110.0, 138.59, 164.81)
        waveform = sum(np.sin(2 * np.pi * freq * times) for freq in frequencies)
        pcm = np.asarray(waveform / 3 * 12_000, dtype="<i2")

        def predict(chunk_size):
            with StreamingTemplateRecognizer() as recognizer:
                return [
                    prediction
                    for offset in range(0, len(pcm), chunk_size)
                    for prediction in recognizer.push_samples(pcm[offset : offset + chunk_size])
                ]

        small = predict(441)
        large = predict(3_000)
        self.assertGreater(len(small), 0)
        self.assertEqual(small[0].sample_index, 7_168)
        self.assertEqual(
            [(row.sample_index, row.chord) for row in small],
            [(row.sample_index, row.chord) for row in large],
        )
        self.assertTrue(np.allclose(
            [row.margin for row in small],
            [row.margin for row in large],
        ))

    def test_reset_restarts_causal_window(self):
        samples = np.zeros(7_168, dtype="<i2")
        recognizer = StreamingTemplateRecognizer()
        self.assertEqual(len(recognizer.push_samples(samples)), 1)
        recognizer.reset()
        self.assertEqual(recognizer.push_samples(samples[:4_096]), [])
        self.assertEqual(len(recognizer.push_samples(samples[4_096:])), 1)

    def test_default_warms_up_at_7168_samples_without_changing_cnn_contract(self):
        recognizer = StreamingTemplateRecognizer()
        self.assertEqual(recognizer.push_samples(np.zeros(7167, dtype="<i2")), [])
        first = recognizer.push_samples(np.zeros(1, dtype="<i2"))
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].sample_index, 7168)
        self.assertAlmostEqual(first[0].timestamp_seconds, 7168 / 22_050)
        self.assertEqual(DEFAULT_STREAMING_PREPROCESSING_CONFIG.stft_n_fft, 2048)
        self.assertEqual(DEFAULT_STREAMING_PREPROCESSING_CONFIG.context_frames, 15)

    def test_short_context_warms_up_without_future_samples(self):
        config = replace(DEFAULT_STREAMING_PREPROCESSING_CONFIG, context_frames=7)
        recognizer = StreamingTemplateRecognizer(preprocessing=config)
        span = 2048 + 6 * 512
        self.assertEqual(recognizer.push_samples(np.zeros(span - 1, dtype="<i2")), [])
        predictions = recognizer.push_samples(np.zeros(1, dtype="<i2"))
        self.assertEqual(len(predictions), 1)
        self.assertEqual(predictions[0].sample_index, span)
        self.assertAlmostEqual(predictions[0].timestamp_seconds, span / 22_050)

    def test_template_rejects_offline_feature_contract(self):
        config = replace(DEFAULT_STREAMING_PREPROCESSING_CONFIG, feature_type="chroma_cqt")
        with self.assertRaisesRegex(ValueError, "causal 12-bin STFT"):
            StreamingTemplateRecognizer(preprocessing=config)


if __name__ == "__main__":
    unittest.main()
