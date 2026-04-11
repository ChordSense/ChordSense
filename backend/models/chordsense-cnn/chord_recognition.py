from model import build_model
from audio_processing import slice_into_windows, extract_chroma_cqt
from argparse import ArgumentParser
from config import *
import numpy as np
from smoother import smooth_predictions, final_prediction
import librosa
import torch

class ChordRecognizer:
  def __init__(self, checkpoint_path: str):
    self.model = build_model(num_classes=NUM_CLASSES)
    self.model.load_state_dict(torch.load(checkpoint_path))
    self.model.eval()
    self.label_names = CHORD_CLASSES

  def from_file(self, audio_path: str):
    waveform, sr = librosa.load(audio_path, sr=SAMPLE_RATE)
    y_harmonics = librosa.effects.harmonic(waveform, margin=1.0)
    chroma_cqt = extract_chroma_cqt(waveform)

    if not self.from_chroma(y_harmonics, chroma_cqt):
      return False
    return True

  def from_chroma(self, y_harmonics: np.ndarray, chroma_cqt: np.ndarray):
    windows = slice_into_windows(chroma_cqt)
    features = torch.tensor(np.array(windows), dtype=torch.float32).unsqueeze(1)
    with torch.no_grad():
      outputs = self.model(features)
      preds = torch.argmax(outputs, dim=1)
    smoothed = smooth_predictions(preds, vote_window=VOTE_WINDOW)
    model_predictions = final_prediction(smoothed, y_harmonics)
    if not self.generate_lab_file(model_predictions):
      return False
    return True

  def generate_lab_file(self, model_predictions: dict):
    pass