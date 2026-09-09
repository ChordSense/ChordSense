import unittest
from dataclasses import replace

import numpy as np
import torch

from models.chordsense_cnn.audio_processing import (
    AudioBuffer,
    DEFAULT_PREPROCESSING_CONFIG,
    preprocess_audio,
)
from models.chordsense_cnn.config import NUM_CLASSES
from models.chordsense_cnn.streaming import (
    CausalChromaExtractor,
    CausalDecisionFilter,
    DEFAULT_STREAMING_PREPROCESSING_CONFIG,
    StreamingChordRecognizer,
)


class ConstantModel(torch.nn.Module):
    def __init__(self, winning_class: int):
        super().__init__()
        self.winning_class = winning_class

    def forward(self, values):
        logits = torch.zeros((len(values), NUM_CLASSES), device=values.device)
        logits[:, self.winning_class] = 8.0
        return logits


class StreamingTests(unittest.TestCase):
    def test_streaming_default_identifies_the_causal_front_end(self):
        config = DEFAULT_STREAMING_PREPROCESSING_CONFIG

        self.assertEqual(config.feature_type, "chroma_stft")
        self.assertFalse(config.use_harmonic)
        self.assertEqual(config.tuning, 0.0)

    def test_extractor_is_chunk_boundary_invariant(self):
        sample_rate = 8_000
        time = np.arange(4096) / sample_rate
        samples = np.sin(2 * np.pi * 220 * time).astype(np.float32)
        whole = CausalChromaExtractor(
            sample_rate,
            n_fft=512,
            hop_length=128,
        )
        chunked = CausalChromaExtractor(
            sample_rate,
            n_fft=512,
            hop_length=128,
        )

        whole_frames = whole.push(samples)
        chunked_frames = []
        for start in range(0, len(samples), 137):
            chunked_frames.extend(chunked.push(samples[start : start + 137]))

        self.assertEqual(len(whole_frames), len(chunked_frames))
        self.assertEqual(
            [frame.sample_end for frame in whole_frames],
            [frame.sample_end for frame in chunked_frames],
        )
        for expected, actual in zip(whole_frames, chunked_frames):
            np.testing.assert_allclose(expected.chroma, actual.chroma, atol=1e-6)

    def test_extractor_matches_offline_causal_stft_feature(self):
        sample_rate = 8_000
        times = np.arange(4096) / sample_rate
        samples = (
            0.5 * np.sin(2 * np.pi * 220 * times)
            + 0.3 * np.sin(2 * np.pi * 277.18 * times)
            + 0.2 * np.sin(2 * np.pi * 329.63 * times)
        ).astype(np.float32)
        config = replace(
            DEFAULT_PREPROCESSING_CONFIG,
            feature_type="chroma_stft",
            sample_rate=sample_rate,
            n_chroma=12,
            stft_n_fft=512,
            hop_length=128,
            use_harmonic=False,
            tuning=0.0,
        )
        expected = preprocess_audio(AudioBuffer(samples, sample_rate), config).chroma
        extractor = CausalChromaExtractor(
            sample_rate=sample_rate,
            n_fft=config.stft_n_fft,
            hop_length=config.hop_length,
            n_chroma=config.n_chroma,
            tuning=config.tuning,
        )

        frames = []
        for start in range(0, len(samples), 137):
            frames.extend(extractor.push(samples[start : start + 137]))
        actual = np.stack([frame.chroma for frame in frames], axis=1)

        self.assertEqual(actual.shape, expected.shape)
        np.testing.assert_allclose(actual, expected, rtol=2e-5, atol=2e-6)

    def test_decision_filter_requires_confirmation(self):
        decision = CausalDecisionFilter(
            class_count=3,
            noise_index=2,
            ema_alpha=1.0,
            enter_threshold=0.6,
            exit_threshold=0.4,
            confirmation_frames=2,
        )

        first = decision.update([0.9, 0.05, 0.05])
        second = decision.update([0.9, 0.05, 0.05])

        self.assertEqual(first.label, 2)
        self.assertFalse(first.changed)
        self.assertEqual(second.label, 0)
        self.assertTrue(second.changed)

    def test_streaming_recognizer_emits_monotonic_timestamps(self):
        config = replace(
            DEFAULT_STREAMING_PREPROCESSING_CONFIG,
            context_frames=3,
            stft_n_fft=256,
            hop_length=64,
        )
        extractor = CausalChromaExtractor(
            sample_rate=config.sample_rate,
            n_fft=config.stft_n_fft,
            hop_length=config.hop_length,
        )
        recognizer = StreamingChordRecognizer(
            ConstantModel(0),
            config,
            extractor=extractor,
            decision_filter=CausalDecisionFilter(
                NUM_CLASSES,
                NUM_CLASSES - 1,
                ema_alpha=1.0,
                confirmation_frames=1,
            ),
        )
        samples = np.zeros(2048, dtype=np.float32)

        predictions = recognizer.push_samples(samples)

        self.assertGreater(len(predictions), 1)
        self.assertTrue(
            all(
                left.timestamp_seconds < right.timestamp_seconds
                for left, right in zip(predictions, predictions[1:])
            )
        )
        complete_hops = (
            len(samples) - config.stft_n_fft
        ) // config.hop_length
        expected_last_end = (
            complete_hops * config.hop_length + config.stft_n_fft
        )
        self.assertEqual(predictions[-1].sample_index, expected_last_end)


if __name__ == "__main__":
    unittest.main()
