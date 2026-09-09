from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import librosa
import numpy as np
import numpy.typing as npt


FloatArray = npt.NDArray[np.float32]
IntArray = npt.NDArray[np.int64]


@dataclass(frozen=True)
class AudioBuffer:
    samples: FloatArray
    sample_rate: int

    def __post_init__(self) -> None:
        samples = np.ascontiguousarray(self.samples, dtype=np.float32)
        if samples.ndim != 1:
            raise ValueError(f"Expected mono audio, got shape {samples.shape}")
        if samples.size == 0:
            raise ValueError("Audio buffer is empty")
        if not np.isfinite(samples).all():
            raise ValueError("Audio buffer contains non-finite samples")
        if self.sample_rate <= 0:
            raise ValueError("Sample rate must be positive")
        object.__setattr__(self, "samples", samples)

    @property
    def duration_seconds(self) -> float:
        return self.samples.size / self.sample_rate


@dataclass(frozen=True)
class PreprocessingConfig:
    version: str = "chroma-cqt-v1"
    feature_type: Literal["chroma_cqt", "chroma_stft"] = "chroma_cqt"
    sample_rate: int = 22_050
    hop_length: int = 512
    context_frames: int = 15
    use_harmonic: bool = True
    harmonic_margin: float = 1.0
    fmin_hz: float = 32.7
    n_chroma: int = 12
    bins_per_octave: int = 12
    n_octaves: int = 7
    stft_n_fft: int = 2048
    # ``None`` preserves the shipped CQT pipeline's global tuning estimate.
    # Causal STFT experiments set this to 0.0 because a whole-buffer estimate
    # is unavailable online.
    tuning: float | None = None

    def __post_init__(self) -> None:
        positive_values = {
            "sample_rate": self.sample_rate,
            "hop_length": self.hop_length,
            "context_frames": self.context_frames,
            "harmonic_margin": self.harmonic_margin,
            "fmin_hz": self.fmin_hz,
            "n_chroma": self.n_chroma,
            "bins_per_octave": self.bins_per_octave,
            "n_octaves": self.n_octaves,
            "stft_n_fft": self.stft_n_fft,
        }
        invalid = [name for name, value in positive_values.items() if value <= 0]
        if invalid:
            names = ", ".join(invalid)
            raise ValueError(f"Preprocessing values must be positive: {names}")
        if self.feature_type not in {"chroma_cqt", "chroma_stft"}:
            raise ValueError(f"Unsupported feature_type: {self.feature_type}")
        if self.tuning is not None and not np.isfinite(self.tuning):
            raise ValueError("tuning must be finite")


DEFAULT_PREPROCESSING_CONFIG = PreprocessingConfig()


def feature_window_sample_span(config: PreprocessingConfig) -> int:
    """Return the source-sample span represented by one feature window."""
    if config.feature_type == "chroma_stft":
        return config.stft_n_fft + (config.context_frames - 1) * config.hop_length
    # Preserve the shipped CQT labeler's historical window convention.
    return config.context_frames * config.hop_length


@dataclass(frozen=True)
class PreprocessedAudio:
    waveform: AudioBuffer
    analysis_waveform: FloatArray
    chroma: FloatArray

    @property
    def duration_seconds(self) -> float:
        return self.waveform.duration_seconds


@dataclass(frozen=True)
class FeatureWindowBatch:
    values: FloatArray
    start_frames: IntArray
    center_frames: IntArray
    end_frames: IntArray
    source_frame_count: int


def load_audio_file(path: str | Path) -> AudioBuffer:
    samples, sample_rate = librosa.load(path, sr=None, mono=True)
    return AudioBuffer(np.asarray(samples, dtype=np.float32), int(sample_rate))


def load_dataset_audio(source: dict[str, Any]) -> AudioBuffer:
    decoded = source["audio"].get_all_samples()
    samples = decoded.data
    if hasattr(samples, "cpu"):
        samples = samples.cpu().numpy()
    samples = np.asarray(samples)
    if samples.ndim == 2:
        samples = samples.mean(axis=0)
    else:
        samples = samples.squeeze()
    return AudioBuffer(samples, decoded.sample_rate)


def preprocess_audio(
    audio: AudioBuffer,
    config: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> PreprocessedAudio:
    samples = audio.samples
    if audio.sample_rate != config.sample_rate:
        samples = librosa.resample(
            samples,
            orig_sr=audio.sample_rate,
            target_sr=config.sample_rate,
        )
    waveform = AudioBuffer(samples, config.sample_rate)
    analysis_waveform = (
        librosa.effects.harmonic(waveform.samples, margin=config.harmonic_margin)
        if config.use_harmonic
        else waveform.samples.copy()
    )
    if config.feature_type == "chroma_cqt":
        chroma = librosa.feature.chroma_cqt(
            y=analysis_waveform,
            sr=config.sample_rate,
            hop_length=config.hop_length,
            fmin=config.fmin_hz,
            n_chroma=config.n_chroma,
            bins_per_octave=config.bins_per_octave,
            n_octaves=config.n_octaves,
            tuning=config.tuning,
            norm=np.inf,
            threshold=0.0,
        )
    else:
        minimum_samples = config.stft_n_fft
        if len(analysis_waveform) < minimum_samples:
            analysis_waveform = np.pad(
                analysis_waveform,
                (0, minimum_samples - len(analysis_waveform)),
            )
        chroma = librosa.feature.chroma_stft(
            y=analysis_waveform,
            sr=config.sample_rate,
            n_fft=config.stft_n_fft,
            hop_length=config.hop_length,
            n_chroma=config.n_chroma,
            center=False,
            tuning=config.tuning,
            norm=np.inf,
        )
    return PreprocessedAudio(
        waveform=waveform,
        analysis_waveform=np.ascontiguousarray(analysis_waveform, dtype=np.float32),
        chroma=np.ascontiguousarray(chroma, dtype=np.float32),
    )


def create_feature_windows(
    chroma: FloatArray,
    config: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> FeatureWindowBatch:
    chroma = np.asarray(chroma, dtype=np.float32)
    if chroma.ndim != 2 or chroma.shape[0] != config.n_chroma:
        raise ValueError(
            f"Expected chroma shape ({config.n_chroma}, T), got {chroma.shape}"
        )
    frame_count = chroma.shape[1]
    if frame_count == 0:
        raise ValueError("Chroma contains no frames")

    if frame_count < config.context_frames:
        padding = config.context_frames - frame_count
        values = np.pad(chroma, ((0, 0), (0, padding)))[np.newaxis, :]
        starts = np.array([0], dtype=np.int64)
        ends = np.array([frame_count], dtype=np.int64)
    else:
        view = np.lib.stride_tricks.sliding_window_view(
            chroma,
            window_shape=config.context_frames,
            axis=1,
        )
        values = np.transpose(view, (1, 0, 2)).copy()
        starts = np.arange(values.shape[0], dtype=np.int64)
        ends = starts + config.context_frames

    centers = np.minimum(
        starts + config.context_frames // 2,
        frame_count - 1,
    )
    return FeatureWindowBatch(
        values=np.ascontiguousarray(values, dtype=np.float32),
        start_frames=starts,
        center_frames=centers,
        end_frames=ends,
        source_frame_count=frame_count,
    )


def extract_chroma_cqt(
    source: str | Path | dict[str, Any],
    config: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> tuple[FloatArray, FloatArray]:
    audio = (
        load_audio_file(source)
        if isinstance(source, (str, Path))
        else load_dataset_audio(source)
    )
    processed = preprocess_audio(audio, config)
    return processed.chroma, processed.analysis_waveform


def slice_into_windows(
    chroma: FloatArray,
    context_frames: int | None = None,
) -> FloatArray:
    config = DEFAULT_PREPROCESSING_CONFIG
    if context_frames is not None:
        config = replace(config, context_frames=context_frames)
    return create_feature_windows(chroma, config).values
