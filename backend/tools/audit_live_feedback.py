"""Replay template timing experiments without changing live defaults.

Run from backend:
  python -m tools.audit_live_feedback --output ../artifacts/live-feedback-audit
  node ../frontend-tauri/frontend-tauri/src/feedback-audit.js \
    ../artifacts/live-feedback-audit/replay.json

The saved guitar labels describe stable interiors, not exact change times.
Synthetic transitions exercise timing; they do not establish guitar accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

from models.chordsense_cnn.benchmark_template import read_pcm16, summarize
from models.chordsense_cnn.streaming import DEFAULT_STREAMING_PREPROCESSING_CONFIG
from models.chordsense_cnn.template import (
    DEFAULT_TEMPLATE_PREPROCESSING_CONFIG,
    PITCH_CLASS,
    TRIAD_LABELS,
    StreamingTemplateRecognizer,
)


RATE = 22_050
ROOT = Path(__file__).resolve().parents[2]
TAKES = (
    ("guitar-gap-20260928-181631.wav", (
        ("N", 2, 5), ("E", 8, 11), ("A", 12.5, 15),
        ("D", 17, 19.5), ("G", 22, 24.5), ("N", 29, 34),
    )),
    ("guitar-gap-20260928-181712.wav", (
        ("N", 2, 8), ("E", 11, 14), ("A", 15.5, 18),
        ("D", 20, 23), ("G", 26, 29), ("N", 33, 34),
    )),
)
PROFILES = (
    ("baseline2048", 2048, 512, 15, 441, 0),
    ("context9", 2048, 512, 9, 441, 0),
    ("context7", 2048, 512, 7, 441, 0),
    ("hop256", 2048, 256, 15, 441, 0),
    ("packet221", 2048, 512, 15, 221, 0),
    ("fft1024", 1024, 512, 15, 441, 0),
    ("fft4096-context7", 4096, 512, 7, 441, 0),
    ("highpass20", 2048, 512, 15, 441, 20),
)


def synthetic_take() -> tuple[np.ndarray, list[tuple[str, float, float]]]:
    """Fixed-seed triads with harmonics, DC offset, and abrupt labeled changes."""
    rng = np.random.default_rng(42)
    labels = list(TRIAD_LABELS)
    rng.shuffle(labels)
    length = 2 * RATE
    t = np.arange(length, dtype=np.float64) / RATE
    attack = np.minimum(t / 0.015, 1)
    envelope = attack * (0.45 + 0.55 * np.exp(-t / 0.8))
    chunks, segments = [], []
    for index, label in enumerate(labels):
        minor = label.endswith("m")
        pitch = PITCH_CLASS[label[:-1] if minor else label]
        midi = (36 + pitch, 48 + pitch + (3 if minor else 4), 48 + pitch + 7)
        values = np.zeros(length, dtype=np.float64)
        for note in midi:
            fundamental = 440 * 2 ** ((note - 69) / 12)
            for harmonic in range(1, 7):
                values += np.sin(2 * np.pi * fundamental * harmonic * t) / harmonic ** 1.5
        values *= 0.45 / np.max(np.abs(values))
        values = values * envelope - 0.197 + rng.normal(0, 0.001, length)
        chunks.append(np.round(values * 32768).astype("<i2"))
        segments.append((label, index * 2.0, (index + 1) * 2.0))
    return np.concatenate(chunks), segments


def replay(samples, config, chunk_samples=441, highpass_hz=0):
    recognizer = StreamingTemplateRecognizer(preprocessing=config)
    events, processing_ms = [], []
    base_sample = None
    silent_samples = hold_samples = 0
    silence_reset = False
    sos = butter(2, highpass_hz, fs=RATE, btype="highpass", output="sos") if highpass_hz else None
    zi = np.zeros((len(sos), 2)) if sos is not None else None
    for offset in range(0, len(samples), chunk_samples):
        chunk = samples[offset:offset + chunk_samples]
        normalized = chunk.astype(np.float32) / 32768.0
        ac_rms = float(np.sqrt(np.mean((normalized - normalized.mean()) ** 2)))
        clipping = float(np.mean(np.abs(normalized) >= 0.98))
        if ac_rms >= 0.008:
            hold_samples = round(0.15 * RATE)
        else:
            hold_samples = max(0, hold_samples - len(chunk))
        quality = "clipping" if clipping >= 0.05 else "ok" if hold_samples > 0 else "silence"
        started = time.perf_counter()
        # Filtering runs continuously, including quiet chunks. The input gate
        # always measures the original PCM, so a filter transient cannot bypass it.
        features = chunk
        if sos is not None:
            features, zi = sosfilt(sos, normalized, zi=zi)
        if quality == "silence":
            silent_samples += len(chunk)
            if silent_samples >= RATE:
                if not silence_reset:
                    recognizer.reset()
                    base_sample = None
                    silence_reset = True
                continue
        else:
            if silence_reset:
                recognizer.reset()
                base_sample = None
                silence_reset = False
            silent_samples = 0
        if base_sample is None:
            base_sample = offset
        predictions = recognizer.push_samples(features)
        processing_ms.append((time.perf_counter() - started) * 1000)
        for item in predictions:
            sample_end = base_sample + item.sample_index
            delivery_end = offset + len(chunk)
            events.append({
                "seconds": sample_end / RATE,
                "delivery_seconds": delivery_end / RATE,
                "sample_age_ms": (delivery_end - sample_end) / RATE * 1000,
                "chord": item.chord if quality == "ok" else None,
                "confidence": item.confidence,
                "raw_chord": item.raw_chord,
                "quality_uncertain": item.quality_uncertain,
                "input_quality": quality,
                "root_margin": item.root_margin,
                "quality_margin": item.quality_margin,
                "concentration": item.concentration,
            })
    recognizer.close()
    return events, {
        "processing_p50_ms": float(np.percentile(processing_ms, 50)),
        "processing_p95_ms": float(np.percentile(processing_ms, 95)),
        "processing_p99_ms": float(np.percentile(processing_ms, 99)),
        "processing_measured_chunks": len(processing_ms),
        "processing_seconds": sum(processing_ms) / 1000,
        "audio_seconds": len(samples) / RATE,
        "first_prediction_delivered_ms": events[0]["delivery_seconds"] * 1000 if events else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--synthetic-only", action="store_true",
                        help="Run without the local, untracked guitar captures")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    recordings = []
    for filename, segments in (() if args.synthetic_only else TAKES):
        path = ROOT / "runtime/benchmarks" / filename
        if not path.is_file():
            parser.error(f"Missing saved capture: {path}")
        recordings.append((filename, read_pcm16(path), segments, "stable_interiors"))
    samples, segments = synthetic_take()
    recordings.append(("synthetic-24-triad-transitions", samples, segments, "synthetic_changes"))
    result = {
        "sample_rate": RATE,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "live_template_default": asdict(DEFAULT_TEMPLATE_PREPROCESSING_CONFIG),
        "timing_scope": "Local accelerated replay; packet ages simulated, device transport excluded",
        "profiles": [],
    }
    for name, fft, hop, context, packet, highpass in PROFILES:
        config = replace(DEFAULT_STREAMING_PREPROCESSING_CONFIG,
                         stft_n_fft=fft, hop_length=hop, context_frames=context)
        is_live_default = (
            fft == DEFAULT_TEMPLATE_PREPROCESSING_CONFIG.stft_n_fft
            and hop == DEFAULT_TEMPLATE_PREPROCESSING_CONFIG.hop_length
            and context == DEFAULT_TEMPLATE_PREPROCESSING_CONFIG.context_frames
            and packet == 441 and highpass == 0
        )
        if is_live_default:
            config = DEFAULT_TEMPLATE_PREPROCESSING_CONFIG
        span = fft + (context - 1) * hop
        profile = {
            "name": name, "preprocessing": asdict(config),
            "is_live_default": is_live_default,
            "packet_samples": packet, "highpass_hz": highpass,
            "warmup_ms": span / RATE * 1000,
            "feature_hop_ms": hop / RATE * 1000,
            "context_midpoint_age_ms": span / RATE * 500,
            "recordings": [],
        }
        for filename, samples, segments, kind in recordings:
            events, timing = replay(samples, config, packet, highpass)
            profile["recordings"].append({
                "name": filename, "label_kind": kind,
                "pcm_sha256": hashlib.sha256(samples.tobytes()).hexdigest(),
                "segments": [{"chord": label, "start": start, "end": end}
                             for label, start, end in segments],
                "window_summaries": [summarize(events, segment) for segment in segments],
                "timing": timing, "events": events,
            })
        result["profiles"].append(profile)
        print(f"{name}: warmup {profile['warmup_ms']:.1f} ms", flush=True)
    path = args.output / "replay.json"
    path.write_text(json.dumps(result, separators=(",", ":")) + "\n")
    summary = {**result, "profiles": [
        {**profile, "recordings": [{key: value for key, value in recording.items() if key != "events"}
                                  for recording in profile["recordings"]]}
        for profile in result["profiles"]
    ]}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(path)


if __name__ == "__main__":
    main()
