import librosa
import numpy as np
import torch
import torchcodec
from config import *

def extract_chroma_cqt(sample):
    """Extract chroma CQT from a HuggingFace dataset sample.
    
    Args:
        sample: HuggingFace dataset row with sample["audio"] 
                as a torchcodec AudioDecoder object
    
    Returns:
        chroma: np.ndarray of shape (12, T)
    """
    audio = sample["audio"].get_all_samples()
    waveform = audio.data.squeeze().cpu().numpy()
    sr = audio.sample_rate

    # Resample to our standard rate if needed
    if sr != SAMPLE_RATE:
        waveform = librosa.resample(waveform, orig_sr=sr, target_sr=SAMPLE_RATE)
        sr = SAMPLE_RATE

    # Harmonic separation — reduce pick/percussion noise
    y_harmonic = librosa.effects.harmonic(waveform, margin=1.0)

    # Chroma CQT
    chroma = librosa.feature.chroma_cqt(
        y=y_harmonic,
        sr=sr,
        hop_length=HOP_LENGTH,
        fmin=FMIN,
        n_chroma=N_CHROMA,
        bins_per_octave=BINS_PER_OCTAVE
    )

    return chroma  # shape: (12, T)

def slice_into_windows(chroma, context_frames=CONTEXT_FRAMES):
    """Slice a chroma matrix into overlapping fixed-size windows.
    
    Returns:
        windows: np.ndarray of shape (N, 12, context_frames)
    """
    n_frames = chroma.shape[1]
    if n_frames < context_frames:
        # Pad short clips with zeros on the right
        pad_width = context_frames - n_frames
        chroma = np.pad(chroma, ((0, 0), (0, pad_width)))
        return chroma[np.newaxis, :]  # single window

    windows = []
    # Stride of 1 frame for maximum training data from each clip
    for i in range(n_frames - context_frames + 1):
        window = chroma[:, i:i + context_frames]
        windows.append(window)

    return np.array(windows)

'''
def process_dataset(dataset):
   # Consider making this, if we need pre-processed for fast training     