# Live chord-template experiment

This is an opt-in, training-free comparison with the verified causal-STFT CNN. It uses the same 22,050 Hz PCM stream and 2,048-sample FFT / 512-sample hop / 15-frame chroma context. The best of 24 major/minor templates is the sum of chroma at the root, third, and fifth. It starts with no prediction for about 418 ms. No HEF compilation is needed.

## Run modes

Set one mode before starting the backend process:

```bash
# Existing HEF behavior (default)
python app.py

# Compare both recognizers on one IOD stream. UI still rates the CNN.
CHORDSENSE_DSP_SHADOW=1 python app.py

# Have the UI rate the experimental DSP output, with no HEF requirement.
CHORDSENSE_LIVE_RECOGNIZER=template python app.py
```

The shadow result is attached as `dsp_experiment` to each ordinary CNN prediction event with the same `sample_index`; it does not create a second event stream or a second hardware subscriber. `dsp_experiment` includes `chord`, `signal_ok`, `ac_rms`, `concentration`, `root_margin`, `quality_margin`, and the ungated `raw_chord`. A shadow error is reported as `dsp_shadow_error` and the CNN continues.

The selected template event identifies itself with `model: chordsense-chroma-template-experimental`. Its `confidence` field is only an adapter for the existing UI threshold logic, derived from root margin; `confidence_kind: template_root_margin` marks that it is **not a calibrated probability**. The original CNN path and its HEF manifest checks remain the default.

## Evidence and presentation

- DC-centered AC RMS below 0.008 is silence. A 150 ms signal hold avoids brief quiet gaps after a strum. Clipping is still vetoed.
- Template concentration must be at least 0.40 and the winning root must beat the next root by at least 0.02. Otherwise the label is `N` and the UI abstains.
- If the root is supported but the major/minor score difference is below 0.08, an otherwise matching chord is yellow. A matching seventh chord is also yellow because this scorer only resolves triads.
- The experimental UI requires a chord rating to persist for 500 ms before showing it. Once green is confirmed, the overlay stays green until the chart advances to the next chord, even if later inference is contradictory or the guitar falls silent. The normal CNN keeps its previous entry dwell and uses the same green latch.
- The template route waits a full second of silence before resetting its 15-frame context. Before green has been confirmed, sustained silence clears the rating; a new chord needs another causal context after reset. Only green and red have visible overlays; yellow remains an internal uncertain rating.

## Reproduce the saved guitar replay

Run from `backend` using a Python environment with NumPy, SciPy, and librosa (the model environment in this workspace has these packages):

```bash
models/chord-cnn-lstm-model/venv/bin/python -m models.chordsense_cnn.benchmark_template \
  ../runtime/benchmarks/guitar-gap-20260928-181631.wav \
  --segment N:2:5 --segment E:8:11 --segment A:12.5:15 \
  --segment D:17:19.5 --segment G:22:24.5 --segment N:29:34

models/chord-cnn-lstm-model/venv/bin/python -m models.chordsense_cnn.benchmark_template \
  ../runtime/benchmarks/guitar-gap-20260928-181712.wav \
  --segment N:2:8 --segment E:11:14 --segment A:15.5:18 \
  --segment D:20:23 --segment G:26:29 --segment N:33:34
```

The `--segment` labels refer to the *played* chord and deliberately avoid change boundaries; their timings are approximate. With the current code, the first take has E 129/129, A 108/108, D 108/108, and G 101/108 exact named windows; the seven remaining G windows say Gm. The second has E 129/129, A 107/107, D 123/123, and G 123/123 exact named windows. The G evidence is mostly quality-uncertain: zero first-take G windows and ten second-take G windows are green-eligible. The selected quiet intervals produce no named predictions.

On 147 held-out recordings, an exploratory feature replay found 25,913/30,474 audible chord windows passed the signal/root/concentration gate, with 94.1% exact labels among accepted windows. It also accepted 151/8,904 labeled noise windows. These windows are highly correlated and most chords came from one guitar; the recording, rather than the window, is the independent unit. A held-out F recording was called A#m in 181/235 audible windows, which a longer UI dwell cannot correct. Because confirmed green now persists to the end of a chart segment, a false green would persist too; deliberate wrong-chord trials are essential. The saved guitar takes had playback paused, so they do not test playback bleed or the complete UI loop.

## Live test before promoting it

On the Pi, first use shadow mode with playback-only/no guitar, then guitar with playback off and on. Record deliberate wrong chords as well as correct E–A–D–G and a seventh chord. Compare sample-aligned CNN and template labels, false green/red time, abstentions, rating switches, and time to first stable color. Try another guitar/player and input level. Promote this scorer only after wrong-chord false green is acceptably low in the whole UI loop; the saved replay does not establish that.
