import tempfile
import unittest
import wave
from dataclasses import replace

import librosa
import numpy as np

from models.chordsense_cnn.audio_processing import (
    AudioBuffer,
    DEFAULT_PREPROCESSING_CONFIG,
    create_feature_windows,
    load_audio_file,
    load_dataset_audio,
    preprocess_audio,
)


class DecodedAudio:
    def __init__(self, data: np.ndarray, sample_rate: int):
        self.data = data
        self.sample_rate = sample_rate


class DatasetAudio:
    def __init__(self, decoded: DecodedAudio):
        self.decoded = decoded

    def get_all_samples(self) -> DecodedAudio:
        return self.decoded


def synthesize_chord(sample_rate: int, duration_seconds: float = 1.0) -> np.ndarray:
    time = np.arange(round(sample_rate * duration_seconds)) / sample_rate
    frequencies = (110.0, 138.59, 164.81)
    samples = sum(np.sin(2 * np.pi * frequency * time) for frequency in frequencies)
    return (samples / len(frequencies)).astype(np.float32)


class AudioProcessingTests(unittest.TestCase):
    def test_default_pipeline_matches_training_formula(self):
        config = DEFAULT_PREPROCESSING_CONFIG
        samples = synthesize_chord(config.sample_rate)
        processed = preprocess_audio(AudioBuffer(samples, config.sample_rate), config)

        harmonic = librosa.effects.harmonic(samples, margin=1.0)
        chroma = librosa.feature.chroma_cqt(
            y=harmonic,
            sr=config.sample_rate,
            hop_length=config.hop_length,
            fmin=32.7,
            n_chroma=12,
            bins_per_octave=12,
        )

        np.testing.assert_allclose(
            processed.analysis_waveform,
            harmonic,
            rtol=1e-6,
            atol=1e-7,
        )
        np.testing.assert_allclose(processed.chroma, chroma, rtol=1e-6, atol=1e-7)

    def test_resamples_to_configured_rate(self):
        config = DEFAULT_PREPROCESSING_CONFIG
        source_rate = 44_100
        processed = preprocess_audio(
            AudioBuffer(synthesize_chord(source_rate), source_rate)
        )

        self.assertEqual(processed.waveform.sample_rate, config.sample_rate)
        self.assertEqual(processed.waveform.samples.size, config.sample_rate)
        self.assertEqual(processed.chroma.shape[0], config.n_chroma)
        self.assertTrue(np.isfinite(processed.chroma).all())

    def test_dataset_audio_is_converted_to_mono(self):
        left = synthesize_chord(8_000, 0.1)
        right = left * 0.5
        source = {"audio": DatasetAudio(DecodedAudio(np.stack([left, right]), 8_000))}

        audio = load_dataset_audio(source)

        np.testing.assert_allclose(audio.samples, (left + right) / 2)
        self.assertEqual(audio.sample_rate, 8_000)

    def test_wav_file_preserves_source_rate(self):
        sample_rate = 8_000
        samples = synthesize_chord(sample_rate, 0.1)
        pcm = np.round(samples * np.iinfo(np.int16).max).astype("<i2")

        with tempfile.NamedTemporaryFile(suffix=".wav") as temporary_file:
            with wave.open(temporary_file.name, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                output.writeframes(pcm.tobytes())
            audio = load_audio_file(temporary_file.name)

        self.assertEqual(audio.sample_rate, sample_rate)
        self.assertEqual(audio.samples.size, samples.size)
        np.testing.assert_allclose(audio.samples, samples, atol=5e-5)

    def test_windows_match_previous_sliding_behavior(self):
        config = replace(DEFAULT_PREPROCESSING_CONFIG, context_frames=3)
        chroma = np.arange(12 * 5, dtype=np.float32).reshape(12, 5)

        batch = create_feature_windows(chroma, config)
        expected = np.array([chroma[:, index:index + 3] for index in range(3)])

        np.testing.assert_array_equal(batch.values, expected)
        np.testing.assert_array_equal(batch.start_frames, [0, 1, 2])
        np.testing.assert_array_equal(batch.center_frames, [1, 2, 3])
        np.testing.assert_array_equal(batch.end_frames, [3, 4, 5])

    def test_short_chroma_is_padded_once(self):
        config = replace(DEFAULT_PREPROCESSING_CONFIG, context_frames=5)
        chroma = np.ones((12, 2), dtype=np.float32)

        batch = create_feature_windows(chroma, config)

        self.assertEqual(batch.values.shape, (1, 12, 5))
        np.testing.assert_array_equal(batch.values[0, :, :2], chroma)
        np.testing.assert_array_equal(batch.values[0, :, 2:], 0)
        np.testing.assert_array_equal(batch.end_frames, [2])

    def test_audio_buffer_rejects_invalid_audio(self):
        with self.assertRaises(ValueError):
            AudioBuffer(np.array([], dtype=np.float32), 22_050)
        with self.assertRaises(ValueError):
            AudioBuffer(np.zeros((2, 10), dtype=np.float32), 22_050)
        with self.assertRaises(ValueError):
            AudioBuffer(np.array([np.nan], dtype=np.float32), 22_050)


if __name__ == "__main__":
    unittest.main()
