# Live chord feedback during playback

**Current recognizer selection:** The backend uses the DSP chord-template recognizer by default, with a 4,096-sample FFT, 512-sample hop and seven-frame context (about 325 ms); no HEF is needed for that route. Set `CHORDSENSE_LIVE_RECOGNIZER=cnn` to use the verified HEF/CNN route, which retains its 2,048 FFT and 15-frame contract. For a sample-aligned comparison on that original contract while the UI rates the CNN, set both `CHORDSENSE_LIVE_RECOGNIZER=cnn` and `CHORDSENSE_DSP_SHADOW=1`. See [the DSP experiment guide](dsp-template-experiment.md) for the current run commands and limitations, and [template signal processing](template-signal-processing.md) for stage equations. The implementation history below describes how the original CNN route was built and verified.

## Goal and scope

Show a stable green or red full-screen overlay while the user plays guitar during either song playback or playback of a completed recording. Keep the existing audio player and previous/current/next chord display running. Feedback is optional; ordinary playback continues to work without guitar input. Yellow remains an internal uncertain rating with no visible overlay.

"Record playback" here means the captured WAV loaded into Play Along after `end_recording`. Feedback does not run during the initial recording: `iod` deliberately makes WAV capture and live streaming mutually exclusive.

The rating compares live guitar input with the chord chart at the playback position. It does not grade rhythm, fingering, voicing, or the correctness of the generated chart.

## Existing pieces and gaps

| Piece | Current implementation | Work needed |
| --- | --- | --- |
| Playback and chord display | `play-along.js` uses the HTML audio element's `currentTime`, `getChordSet()`, and `updateChordDisplay()` for songs and recorded takes. | Reuse this timeline; add a feedback mode and rating overlay to the current card. |
| Guitar capture | `iod` streams 22,050 Hz mono PCM16 frames over `start_stream`; each frame has a sample index. | Add a persistent Python stream client and a per-session worker. Do not route the backing track into the CNN. |
| Live recognition | `StreamingTemplateRecognizer` is the current default; `StreamingChordRecognizer` remains available through `CHORDSENSE_LIVE_RECOGNIZER=cnn` and runs the CNN on Torch or Hailo. | Validate both routes on physical guitar input, including false green during wrong chords. |
| Backend and UI bridge | Flask exposes recording/analysis endpoints; Tauri commands call them. No live feedback transport exists. | Add session controls and a prediction event channel. |

**Model readiness gate:** the earlier *Audit ChordSense model* task documents the deployed `chordsense.hef` as the original baseline 25-class `ChordCNN`, exported from `latest_chord_cnn.pth` through the handed-off `chordsense.onnx` with a fixed `(1, 1, 12, 15)` input and 25 logits. It was compiled for Hailo-10H using **random calibration**. On the Pi, its top-1 predictions agreed with PyTorch on 98.3% of 2,943 windows from five real recordings using the **offline whole-recording CQT** path. This validates that offline path reasonably well, but does not validate live accuracy or confidence. The causal live front end uses STFT, a 2,048-point FFT, and 15 context frames. A separate causal-STFT live HEF has now been compiled and numerically verified as described below; the original offline HEF must not be used for live inference.

## Proposed data flow

```text
guitar jack -> SPI / iod start_stream -> Python live worker
    -> signal gate -> StreamingTemplateRecognizer (default) -> prediction events
                   or StreamingChordRecognizer (explicit cnn mode)
    -> Tauri event bridge -> feedback controller in play-along.js
HTML audio currentTime -> existing chord display -----------^
```

1. Add an `IodClient` streaming method that opens a dedicated Unix socket, sends `start_stream`, checks the acknowledgement, decodes base64 PCM16, and yields `{sample_index, samples}`. Validate frame size and sequence continuity. When indices jump because `iod` dropped frames, reset the extractor/decision filter instead of stitching unrelated samples together. Closing the socket unsubscribes the stream.
2. Add one backend `LiveFeedbackSession` that owns the stream, recognizer, worker thread, bounded latest-event queue, session ID, and start/pause/resume/stop lifecycle. Initialize Hailo once when the CNN route is selected and keep inference off the Flask request thread. Drop old predictions if the UI falls behind. Return a clear busy/error status when recording capture owns the ADC; release sockets and model resources on stop or failure.
3. Expose session control endpoints plus an event stream from Flask. Add Tauri commands to start/stop the session and a Rust task that forwards prediction events to the webview. Include `session_id`, sequence number, absolute ADC sample index, capture timestamp or measured sample age, predicted chord, confidence, input level/quality, and a model/status field. The existing `iod` `playback_position_secs` is **not** a valid clock while playback remains in the HTML audio element.
4. Add a Play Along choice for Standard Playback and Live Feedback. Both songs and recorded takes use the same `play-along.js` transport and feedback controller. Subscribe before playback starts; show no rating while the model warms up. Continue the existing `requestAnimationFrame` display loop independently of the audio worker. Stop the session when the track changes, mode changes, playback ends, or the user presses Stop.
5. Treat `audio.currentTime` as the expected-chord clock. Timestamp the PCM frame at capture or measure its age through the bridge, and align the prediction to the corresponding playback time. Calibrate the residual output/input latency on the Pi. Do not compare a delayed prediction blindly with whatever chord happens to be visible when the event arrives. Use a 200–300 ms chord-boundary grace interval as a starting setting, then tune with recorded traces. On pause, suspend evaluation and stream processing; on resume, warm up again. On seek, clear the rating and reject predictions captured before the seek. Tag all asynchronous results with the session ID so late events cannot color a new song.

## Rating contract

Normalize both sources to `{pitch_class, quality}` before comparison. Support `A`–`G`, sharps/flats as enharmonic equivalents, colon and compact labels (`D:maj`, `Dm`), and slash inversions (`G:maj/3`). Do not let the diagram-image parser determine correctness. `N`, gaps, unsupported chord types, silence, clipping, low confidence, stale predictions, and startup warmup have **no rating**; show a neutral card/status, not red.

| Rating | Proposed rule for a reliable, stable live prediction |
| --- | --- |
| Green | Same root and same major/minor quality as a supported expected triad. |
| Yellow | Same root with different major/minor quality, or the matching major/minor triad when the chart calls for `7`, `maj7`, or `min7`. The current CNN cannot verify the seventh, so it cannot award green for it. Brief transition uncertainty may also remain yellow/neutral instead of red. |
| Red | A different root persists beyond the chord-boundary grace period and passes a stricter confidence/dwell threshold. |

Yellow means "partially matching or still settling," not an exact chord. If this interpretation proves confusing in user trials, retain yellow for partial root/quality matches and use the neutral state for transitions.

## Stability rules and starting parameters

Use the recognizer's existing probability EMA, enter/exit hysteresis, and two-frame label confirmation as the **first** filter. Add an independent rating state machine keyed by the expected chord segment ID. These values are initial tuning parameters, not accuracy claims:

- Reject silence below a calibrated RMS floor, clipped input, and predictions below a calibrated confidence threshold. Noise/unknown never becomes red. Before green is confirmed, apply a short 150–250 ms dropout hold, then clear to neutral if unreliable input continues.
- Require about 200 ms of consistent evidence before entering green or yellow, and about 350 ms before entering red. Once green is confirmed, keep it through the end of that chart segment. Keep red for at least 300 ms unless the expected chord changes or input becomes unusable.
- At each expected-chord boundary, reset the rating accumulator, keep the new card neutral/yellow for the grace window, and prevent evidence for the previous chord from causing an immediate red flash. Reset similarly on seek, pause/resume, and new sessions.
- Rate short chart segments conservatively: if a segment ends before the live model can produce stable evidence, leave it unrated. Measure this explicitly rather than forcing a color.

At the current default live settings, the first model window needs `(2048 + 14 * 512) / 22050`, about **418 ms** of samples before any processing or confirmation time. This should be measured on hardware and may justify deploying the shorter-context live model. The UI must represent this warmup honestly.

## Implementation order

1. **Prove the live model path.** Use the recorded offline HEF provenance as a baseline. Select a causal-STFT-trained checkpoint, verify its preprocessing/class/calibration metadata, compile a live HEF with representative live-feature windows, compare Torch and Hailo predictions on identical windows, and benchmark end-to-end live accuracy and latency on the Pi with real jack audio.
2. **Build the stream service.** Implement the persistent `iod` client, session worker, quality gate, stale-frame handling, and session controls/events. Exercise capture/stream exclusion and cleanup.
3. **Build pure comparison logic.** Implement chord normalization, temporal alignment, three-color/neutral rules, and rating hysteresis as a testable module with timestamped inputs.
4. **Integrate the UI.** Add the Live Feedback choice, current-card badge/outline and text label, status for input loss/warmup, and lifecycle hooks for play, pause, seek, stop, ended, track replacement, and mode switch. Use the same path for songs and recorded takes.
5. **Tune on device.** Replay labeled chord-change traces and run live guitar sessions for both playback sources. Tune confidence, RMS, boundary grace, and dwell times from observed false reds, rating reversals, and time to stable green.

## Acceptance checks

- Standard playback and recording capture/analysis still work. Starting feedback while capture is active reports a usable error and leaves capture intact.
- Both a loaded song and a finished recorded take display synchronized chords and feedback while audio plays; pause, seek, stop, end, track change, and mode change leave no stale color or streaming subscriber.
- Green appears for a sustained matching triad and stays through the current chart segment; a sustained wrong root becomes red before green is confirmed. Same-root partial/unsupported-seventh matches remain internal yellow with no visible overlay. Silence, `N`, unsupported labels, low confidence, and dropped input produce no new rating.
- A single bad model frame or brief dropout does not flip the rating; repeated stable evidence does. Old events cannot affect a new session or a post-seek chord.
- Measure on the Pi: prediction/rating latency, false-red rate around boundaries, rating changes per held chord, dropped stream frames, worker CPU/Hailo use, and visual frame smoothness while audio and inference run together. Set final numeric targets only after the model/HEF and hardware path are verified.

The rating logic includes yellow for partial matches. The UI displays only green and red full-screen tints.

## Implementation checkpoint (items 1–4)

- The selected live candidate is `e1b-causal-stft-seed-42/model.pth`. Its 25 classes and all numerical preprocessing fields match `StreamingChordRecognizer`. This is a separate model from the offline CQT `chordsense.hef`. Its checkpoint has no probability-temperature calibration, so the current temperature is 1.0 and the rating confidence thresholds remain provisional.
- `prepare_live_hef.py` exports a static `(1,1,12,15)` ONNX model, 800 balanced calibration windows from 194 real training recordings (32 windows per class), and 200 evaluation windows from the held-out dataset test split. The Noise class has only two eligible training recordings, so its 32 windows are less varied than the chord classes. The script excludes the original validation recordings from quantization, checks Torch/ONNX logit parity, and writes file hashes and class/preprocessing provenance to `inputs.json`.
- The seed-42 Torch model gets **64.5% top-1** on the 200 balanced interior test windows, despite its 86.3% window accuracy on the original validation set. A full pass over all 147 dedicated test recordings gives 69.6% overall window accuracy, 61.5% chord-window accuracy, and 67.4% recording accuracy (97/144 chord recordings). The dedicated test split is mostly files named `_test_`; the training/validation recordings are mostly different takes. On the same two-interior-window evaluation method, sampled training/validation/test accuracy is 94.9%/90.2%/67.9%, with only two test windows below the training RMS threshold. The gap is therefore a generalization failure across recording takes, not a silence-label or export artifact. Test recall is almost zero for A, B, C#m, D, F#, and G#m, despite high validation recall for those classes. At a raw softmax threshold of 0.9, 17 of 106 accepted balanced test windows are still wrong. Seed 43/44 checkpoints score 62.5%/66.5% on the same small slice. Compilation proves deployment compatibility but cannot fix these errors. Do not present red as a trustworthy judgment until the model and device trials improve this.
- The IOD stream frames now include a monotonic capture timestamp. `IodClient` and `LiveFeedbackSession` handle stream ownership, frame gaps, signal quality, model inference, session controls, and bounded SSE events. A missing DAC no longer prevents the IOD sampler and stream from starting; playback commands report that audio output is unavailable.
- `feedback-rating.js` implements normalization, three internal ratings plus abstention, time alignment, chord-boundary grace, and confidence/dwell hysteresis. Play Along has a Live Feedback toggle for both loaded songs and recorded takes. The Tauri bridge forwards Flask SSE events to the playback controller. Green and red use transparent, pointer-free full-screen tints; yellow has no visible overlay, and feedback status remains available to screen readers. Confirmed green stays until the current chart segment ends, including through silence or later contradictory inference. Pause, seek, stop, and new sessions clear it. Playback timing follows the IOD audio player, and events from older sessions or seeks are rejected.
- A temporary Pi daemon and then the installed IOD service passed the real socket stream/capture exclusion and unsubscribe checks. The installed service binary was backed up as `iod/target/release/chordsense-iod.before-live-feedback-20260927` on the Pi. The service is active, and PCM frames have sub-millisecond transport age in the local Pi test. A Pi Torch causal-STFT benchmark produced its first possible prediction after 417.96 ms with 1.17 ms p95 processing per 20 ms audio chunk and a 0.0544 real-time factor. These are compute measurements, not evidence of rating accuracy.
- The current Pi ADC frames are all `-32768` (RMS 1.0, clipping fraction 1.0). The session correctly publishes `input_quality: clipping` and `chord: null`. Guitar-input wiring or gain must be fixed before a meaningful physical-input accuracy or latency trial; the earlier Torch benchmark used a saved capture rather than a valid current guitar signal.

### User-run Hailo compiler handoff and Pi verification

The user ran compilation and returned `chordsense_live.hef` and `chordsense_live.candidate.json` through Taildrop. **Codex did not run any compiler command.** The input archive is `artifacts/live-hef-e1b-seed42-inputs.tgz` (SHA-256 `eb64ddb117ac50abcc5a2ff405bcd0d02c67acb0428971608c6451abc9016b82`). It contains `chordsense_live.onnx`, `calibration_nhwc.npy`, held-out evaluation arrays, `inputs.json`, and `compile_live_hef.py`. The compiler commands for reproducibility are:

```bash
tar -xzf live-hef-e1b-seed42-inputs.tgz
python3 live-hef-e1b-seed42/compile_live_hef.py --bundle live-hef-e1b-seed42
```

The script checks input hashes, parses ONNX for `hailo10h`, quantizes with the real NHWC windows, and compiles `chordsense_live.hef` (SHA-256 `1ff578061b583cfedc94d35a278da0b5c69ffac8034299aca07a7fd9c2e7a19b`). On the Pi, `verify_live_hef.py` compared 200 held-out windows against saved Torch logits: **96% top-1 agreement**, Torch accuracy 64.5%, Hailo accuracy 65.5%, mean absolute logit difference 0.323, and p95 Hailo inference 0.876 ms. It wrote `chordsense_live.json` with `pi_verified: true`; the feedback service refuses a candidate HEF without this verified manifest. A 20.68-second saved-capture streaming benchmark had 0.976 ms p95 processing per 20 ms audio chunk, 0.041 real-time factor, and first possible prediction after 417.96 ms. This proves numerical and compute compatibility, not user-feedback accuracy.

An end-to-end Pi session loaded the HEF through the verified manifest, received an IOD stream, emitted a Hailo prediction event, and released its subscriber on stop. The event correctly withheld its chord because the ADC signal was clipped. The HEF and verified manifest are saved under `artifacts/live-hef-e1b-seed42/` locally. The UI integration is implemented locally; the Pi application build and physical guitar validation are still outstanding.

The desktop UI currently calls the backend on `127.0.0.1:5051`. For a guitar trial, deploy the UI and matching backend on the Pi, then load or record a track, turn on Live Feedback, and press Play. The default DSP route does not need HEF paths. To use the CNN instead, set `CHORDSENSE_LIVE_RECOGNIZER=cnn` and configure `CHORDSENSE_LIVE_HEF` and `CHORDSENSE_LIVE_MANIFEST` to the verified files. Feedback status for warmup, clipping, or disconnects is available to screen readers. A settled matching major/minor chord shows green until the chart segment ends; a sustained different root shows red before green is confirmed. Yellow means matching root with different or unverified quality and shows no overlay. Verify the ADC produces changing PCM before interpreting any rating.

After numerical verification, record live guitar through the physical input while both playback sources run. Measure false reds, rating switches and input/output alignment before treating the displayed ratings as trustworthy. The model candidate's recorded validation score alone is insufficient for that decision.
