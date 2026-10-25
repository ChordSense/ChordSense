# Tuner

Tune is a third device mode next to Play Along and Record. It reads single notes from the guitar jack and shows a traditional needle tuner: note name, −50 to +50 cent meter, flat/sharp indicators, and frequency.

## What is reused and what is not

The live chord recognizer is **not** reused for pitch. Its chroma folds all octaves into 12 pitch classes, and its 2,048-point FFT at 22,050 Hz has ~10.8 Hz bins: about two semitones at low E (82.4 Hz), where one cent is 0.05 Hz. Its 15-frame context also delays the first output by ~418 ms.

Everything around the recognizer is reused:

| Piece | Where |
| --- | --- |
| `iod` `start_stream` PCM frames (22,050 Hz, sample index, capture time) | unchanged; `iod` allows several stream subscribers |
| Stream lifecycle, event queue, SSE, gap handling, silence/clipping gate | `backend/live_input_session.py`, now the base of both `LiveFeedbackSession` and `TunerSession` |
| SSE route | `_live_input_events()` in `backend/app.py` serves `/feedback/events` and `/tuner/events` |
| Tauri session bridge | `device-ui/src-tauri/src/live_bridge.rs`, used by `feedback_bridge.rs` and `tuner_bridge.rs` |
| Pitch estimator | `backend/pitch_detection.py`, a numpy port of `iod/examples/support/pitch.rs` |

## Pitch estimation

`estimate_f0` keeps `pitch.rs`'s normalized autocorrelation, 85%-of-peak lobe selection, and parabolic interpolation, computed through an FFT (~0.13 ms per 20 ms frame on a desktop CPU). Synthetic tests exposed two failures that the port fixes:

- With noise, the first local maximum above the threshold is often a ripple on the rising edge of the true peak. At 0.01 RMS noise this read low E up to ~70 cents off. The port takes the top of the whole lobe instead.
- After low-pass filtering, the lobe around lag zero can still exceed the threshold at `min_lag`, which read low E as ~1,200 Hz. The port only searches after the correlation first goes negative.

The window is low-passed at 2 kHz (or 1.5 × the search ceiling), which roughly halves the error under noise. `PitchTracker` adds a 2,048-sample sliding window updated every stream frame, a 40 ms mute after each attack, a 5-frame median, and an EMA on the log-frequency scale. With a string selected, the search narrows to ±6 semitones around it, which rules out octave errors.

On synthetic plucked strings (six open strings, ±1/±5/±20 cents, bright and weak-fundamental timbres, 0.002 RMS noise), every settled reading is within 2 cents with no wrong-note readings; see `backend/tests/test_tuner.py`. The first reading arrives within 150 ms of a pluck.

## Backend API

| Route | Body / query | Result |
| --- | --- | --- |
| `POST /tuner/start` | optional `{a4_hz, target_midi}` | 201 with session status; 409 while a recording capture owns the ADC |
| `POST /tuner/config` | `{a4_hz?, target_midi?}`; `target_midi: null` returns to auto | applied live between frames |
| `POST /tuner/stop` | | releases the `iod` stream |
| `GET /tuner/status` | | state, session, `a4_hz`, `target_midi` |
| `GET /tuner/events` | `session_id`, `after` | SSE |

Events are `status` transitions (`warming_up`, `listening`, `silence`, `clipping`, `stream_gap`, `error`, `stopped`) and, while the signal is usable, one `pitch` event per 20 ms frame:

```json
{"type":"pitch","note":"A","octave":2,"midi_note":45,"cents":-7.3,"f0_hz":109.54,
 "clarity":0.93,"ac_rms":0.12,"input_quality":"ok","a4_hz":440.0,"target_midi":null}
```

`note` is `null` for unclear frames; the UI keeps the last reading for 0.6 s rather than jumping to a wrong note.

## UI

`device-ui/src/tuner-model.js` holds the pure display rules (tested in `tuner-model.test.js`): green within ±3 cents, amber within ±10, red beyond; "in tune" is confirmed after 500 ms inside ±3 cents and holds until the note moves beyond ±5. With a string picked, cents are measured from that string even past the neighbouring note, and the needle pins at the end of the scale.

`tuner.js` wires the model to the DOM, eases the needle every animation frame, and stops the stream when leaving the mode. Entering Tune stops Play Along playback, the same way Record does.

## Before relying on it

These are synthetic results. On the Pi:

1. Confirm the ADC produces changing PCM (`cargo run --release --example spi_freq` with a reference tone). The live feedback notes record frames stuck at `-32768`, which shows as "Input too loud".
2. Compare against a trusted tuner on all six open strings and at 12th-fret harmonics: settled error, time to first reading, and needle jitter while a note sustains.
3. Check the low E as the string decays and with the guitar's tone control rolled off (weak fundamental), and the high e with a light pick attack.
4. Tune `min_clarity` (0.8), `silence_rms` (0.008), the median/EMA smoothing, and `LOCK_DELAY_MS` from those traces.
