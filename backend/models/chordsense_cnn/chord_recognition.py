import os
import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import librosa
import numpy as np
import torch

from models.chordsense_cnn.audio_processing import extract_chroma_cqt, slice_into_windows
from models.chordsense_cnn.config import *
from models.chordsense_cnn.model import build_model
from models.chordsense_cnn.smoother import final_prediction, smooth_predictions

class ChordRecognizer:
  def __init__(self, checkpoint_path: str):
    self.model = build_model(num_classes=NUM_CLASSES)
    device = "cpu" # Default device value
    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"
    checkpoint = torch.load(checkpoint_path, weights_only=True, map_location=device)
    self.model.load_state_dict(checkpoint['model_state_dict'])
    self.model.eval()
    self.label_names = CHORD_CLASSES

  def from_file(self, audio_path: str, output_path: str = RECORDING_OUTPUT_FILE):
    if not audio_path.endswith(".wav") or not output_path.endswith(".lab"):
      raise ValueError("Audio path must be a .wav file and output path must be a .lab file")
    if not os.path.exists(audio_path):
      raise FileNotFoundError(f"Audio file not found: {audio_path}")

    chroma_cqt, y_harmonics = extract_chroma_cqt(audio_path)

    if not self.from_chroma(y_harmonics, chroma_cqt, output_path):
      return False
    return True

  def from_chroma(self, y_harmonics: np.ndarray, chroma_cqt: np.ndarray, output_path: str = RECORDING_OUTPUT_FILE):
    windows = slice_into_windows(chroma_cqt)
    features = torch.tensor(np.array(windows), dtype=torch.float32).unsqueeze(1)
    with torch.no_grad():
      outputs = self.model(features)
      preds = torch.argmax(outputs, dim=1)
    smoothed = smooth_predictions(preds, vote_window=VOTE_WINDOW)
    model_predictions = final_prediction(smoothed, y_harmonics)
    if not self.generate_lab_file(model_predictions, output_path):
      return False
    return True

  def generate_lab_file(self, model_predictions: dict, output_path: str = RECORDING_OUTPUT_FILE, min_duration: float = 0.4):
    """
    Write segments to a .lab file in the format: start\tend\tchord_label (seconds).

    - Converts frame indices to seconds via HOP_LENGTH / SAMPLE_RATE.
    - Noise is only allowed at the very start or very end of the track;
      interior noise segments are absorbed into the previous chord.
    - Segments shorter than `min_duration` seconds are merged into the
      previous segment. Leading/trailing noise is exempt.
    """

    segments = model_predictions["segments"]
    if not segments:
      return False

    noise_idx = CHORD_CLASSES.index("Noise")
    frame_to_sec = HOP_LENGTH / SAMPLE_RATE

    segs = [[s * frame_to_sec, e * frame_to_sec, lbl] for s, e, lbl in segments]

    # Step 1: collapse interior noise — hold previous chord through it
    first_non_noise = next((i for i, s in enumerate(segs) if s[2] != noise_idx), None)
    last_non_noise = next((i for i in range(len(segs) - 1, -1, -1)
                           if segs[i][2] != noise_idx), None)

    if first_non_noise is None:
      segs = [[segs[0][0], segs[-1][1], noise_idx]]
    else:
      for i in range(first_non_noise + 1, last_non_noise):
        if segs[i][2] == noise_idx:
          segs[i][2] = segs[i - 1][2]

    def merge_adjacent(xs):
      out = [xs[0]]
      for seg in xs[1:]:
        if seg[2] == out[-1][2]:
          out[-1][1] = seg[1]
        else:
          out.append(seg)
      return out

    segs = merge_adjacent(segs)

    # Step 2: drop too-short segments, keeping edge noise
    def is_edge_noise(idx, xs):
      return xs[idx][2] == noise_idx and (idx == 0 or idx == len(xs) - 1)

    changed = True
    while changed and len(segs) > 1:
      changed = False
      for i in range(len(segs)):
        if is_edge_noise(i, segs):
          continue
        if (segs[i][1] - segs[i][0]) < min_duration:
          if i > 0:
            segs[i - 1][1] = segs[i][1]
            segs.pop(i)
          else:
            segs[i + 1][0] = segs[i][0]
            segs.pop(i)
          changed = True
          break
      segs = merge_adjacent(segs)

    try:
      with open(output_path, "w", encoding="utf-8") as f:
        for start, end, lbl in segs:
          name = "N" if lbl == noise_idx else self.label_names[lbl]
          f.write(f"{start}\t{end}\t{name}\n")
      return True
    except OSError:
      return False

def main():
  args = sys.argv[1:]
  if len(args) != 2:
    print("Usage: python chord_recognition.py <audio_path> <output_path>")
    return 1
  audio_path = args[0]
  output_path = args[1]
  chord_recognizer = ChordRecognizer(checkpoint_path="checkpoints/latest_chord_cnn.pth")
  chord_recognizer.from_file(audio_path, output_path)
  return 0

if __name__ == "__main__":
  main()