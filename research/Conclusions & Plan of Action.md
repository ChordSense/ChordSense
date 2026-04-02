**Use of ML techniques vs DSP Techniques**

Chord classification based on a CNN or RNN can work in conjunction with traditional DSP techniques, to better extract features of the chord. The hybrid approach uses DSP as the front-end (computing a CQT or chromagram from raw audio) and feeds that musically structured representation into a CNN classifier. This gives us the interpretability and dimensionality reduction of DSP with the pattern-matching power of a learned model

If high accuracy cannot be achieved with our own model, we can use a lightweight vision pre-trained model (such as MobileNetV2) and fine-tune it, or fall back to a pure DSP mode using chromagram template matching. PANNs CNN14 was ruled out for on-device use due to its size (~80M params), but MobileNetV2 (~3.4M params) remains a viable benchmark against our custom CNN

**Feature Representation**

CQT (Constant-Q Transform) is the primary feature representation for chord recognition. Its logarithmic frequency axis aligns bins with fixed musical intervals (one semitone), making chord shapes geometrically consistent regardless of root note. Chroma CQT (12 bins, one per pitch class) is the compact variant we use for Phase 1, as it is voicing-invariant and has a lighter compute footprint on the Nano. Full 84-bin CQT preserves more harmonic detail and can be explored if chroma plateaus. MFCCs capture timbral information rather than pitch, so they are less suited for chord recognition but could be stacked with CQT later if accuracy needs a boost

**Operating Mode**

The on-device pipeline operates in record-then-transcribe mode, not real-time frame-by-frame display. The user presses record, plays guitar, presses stop, and the system transcribes the full recording into a chord chart. This removes latency constraints and allows more expensive post-processing (full CQT over the whole buffer, onset detection, smoothing) after recording completes

---

## Plan of Action

### Phase 1: On-device record and transcribe

Pipeline: WAV buffer → Chroma CQT (12 bins) → CNN per-frame classification → majority vote smoother (N=5) → onset detection + change confirmation → chord chart output

- CQT feature extraction using librosa.cqt (84 bins, 7 octaves), folded into chroma CQT (12 bins)
- Train custom ChordCNN on chroma input. Input shape: (1, 12, 15), 8 chord classes, ~21K parameters
- Evaluate CQT vs chromagram accuracy. Chromagram is the fallback if CQT does not clearly win
- Implement majority vote smoother using a sliding window of N=5 predictions 
- Add onset detection for strum boundaries using librosa.onset.onset_detect 
- Combine onset triggers with vote agreement for chord change confirmation
- Assemble the full pipeline: record WAV buffer → CQT → CNN → smooth → output chord sequence
- Export model to our dedicated hardware, benchmark inference time (target: <10ms per frame)

### Phase 2: Full song transcription 

- Run Chord-CNN-LSTM inference via ChordMiniApp for full song chord recognition
- Build chord diagram renderer mapping chord labels to guitar fingering positions
- Implement practice mode with section looping and tempo control via librosa.effects.time_stretch

### Phase 3: Enhancements

- Expand chord vocabulary to include 7ths, sus, slash chords. 
- The chord structure decomposition approach from the Large-Vocabulary Chord Transcription paper could scale our classifier without requiring massive data per chord type
- Stack MFCCs with CQT if accuracy plateaus, giving the model both pitch and timbral information
- Compare live recording against song transcription by merging both pipelines, enabling a "play along and compare" feature