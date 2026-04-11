import librosa
import numpy as np
from config import *

def extract_chroma_cqt(source):
    if isinstance(source, str):
        # File path
        waveform, sr = librosa.load(source, sr=SAMPLE_RATE)
    else:
        audio = source["audio"].get_all_samples()
        waveform = audio.data.cpu().numpy()
        if waveform.ndim == 2:
            waveform = waveform.mean(axis=0)
        else:
            waveform = waveform.squeeze()
        sr = audio.sample_rate

        if sr != SAMPLE_RATE:
            waveform = librosa.resample(waveform, orig_sr=sr, target_sr=SAMPLE_RATE)
            sr = SAMPLE_RATE

    y_harmonic = librosa.effects.harmonic(waveform, margin=1.0)

    # Chroma CQT, 12 bins per octave. Possibly change to higher resolution in the future.
    chroma = librosa.feature.chroma_cqt(
        y=y_harmonic, sr=sr, hop_length=HOP_LENGTH,
        fmin=32.7, n_chroma=12, bins_per_octave=12
    )

    return chroma, y_harmonic

def slice_into_windows(chroma, context_frames=CONTEXT_FRAMES):
    if chroma.ndim != 2:
        raise ValueError(f"Expected chroma shape (12, T), got {chroma.shape}")

    n_frames = chroma.shape[1]

    if n_frames < context_frames:
        necessary_padding = context_frames - n_frames
        padded = np.pad(chroma, ((0, 0), (0, necessary_padding)))
        return padded[np.newaxis, :]

    windows = []
    for i in range(n_frames - context_frames + 1):
        window = chroma[:, i:i + context_frames]
        windows.append(window)

    return np.array(windows)