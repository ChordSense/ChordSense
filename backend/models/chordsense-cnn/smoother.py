from scipy.stats import mode
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from config import *
import librosa

def smooth_predictions(predictions, vote_window=VOTE_WINDOW):
  half = vote_window // 2
  padded_predictions = np.pad(predictions, half, mode="edge")
  sliding_view = sliding_window_view(padded_predictions, vote_window)
  smoothed_preds = mode(sliding_view, axis=1, keepdims=False).mode
  return smoothed_preds

def final_prediction(smoothed_preds, y):
  noise_idx = CHORD_CLASSES.index("Noise")
    
  onsets = librosa.onset.onset_detect(y=y, sr=SAMPLE_RATE, hop_length=HOP_LENGTH, backtrack=True)
  
  final_pred = np.empty_like(smoothed_preds)
  segments = []  # list of (start_frame, end_frame, label)
  
  current_chord = noise_idx
  current_start = 0
  
  for onset_frame in onsets:
    # read the post-onset window to find out what chord starts here
    lo = onset_frame + POST_ONSESET_OFFSET
    hi = min(lo + POST_ONSESET_LENGTH, len(smoothed_preds))
    if lo >= len(smoothed_preds):
      break  # onset too close to end to read a post-window
    
    post_onset_chord = mode(smoothed_preds[lo:hi], keepdims=False).mode
    
    if post_onset_chord != current_chord:
      # commit a boundary at this onset
      segments.append((current_start, onset_frame, current_chord))
      current_start = onset_frame
      current_chord = post_onset_chord
    # else: same chord, this onset was just another strum — ignore
  
  # close out the final segment
  segments.append((current_start, len(smoothed_preds), current_chord))
  
  # paint frame-level output from segments
  for start, end, label in segments:
    final_pred[start:end] = label

  return {
    "frame_labels": final_pred,
    "segments": segments,
    "onset_frames": onsets,
  }