"""Benchmark causal Record-mode inference on synthetic audio or a WAV file."""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path
from typing import Iterable

import librosa
import numpy as np
import torch

from .audio_processing import feature_window_sample_span, load_audio_file
from .config import CHORD_CLASSES
from .streaming import StreamingChordRecognizer


def _synthetic_audio(sample_rate: int, duration_seconds: float) -> np.ndarray:
    times = np.arange(round(sample_rate * duration_seconds)) / sample_rate
    frequencies = (220.0, 277.18, 329.63)
    audio = sum(np.sin(2 * np.pi * frequency * times) for frequency in frequencies)
    return np.asarray(audio / len(frequencies) * 0.5, dtype=np.float32)


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--frame-samples", type=int, default=441)
    parser.add_argument("--audible-rms-threshold", type=float, default=0.01)
    parser.add_argument("--expected-chord", choices=(*CHORD_CLASSES[:-1], "N"))
    parser.add_argument("--device", choices=("cpu", "mps", "cuda"), default="cpu")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    if (
        args.duration <= 0
        or args.frame_samples <= 0
        or args.audible_rms_threshold < 0
    ):
        raise ValueError(
            "--duration and --frame-samples must be positive; "
            "--audible-rms-threshold cannot be negative"
        )
    recognizer = StreamingChordRecognizer.from_checkpoint(args.checkpoint, args.device)
    sample_rate = recognizer.preprocessing.sample_rate
    if args.wav is None:
        samples = _synthetic_audio(sample_rate, args.duration)
        source = "synthetic-a-major"
        expected_chord = args.expected_chord or "A"
    else:
        audio = load_audio_file(args.wav)
        samples = audio.samples
        if audio.sample_rate != sample_rate:
            samples = librosa.resample(
                samples,
                orig_sr=audio.sample_rate,
                target_sr=sample_rate,
            )
        source = args.wav.name
        expected_chord = args.expected_chord

    elapsed_per_chunk = []
    predictions = []
    started = time.perf_counter()
    for offset in range(0, len(samples), args.frame_samples):
        chunk_started = time.perf_counter()
        predictions.extend(
            recognizer.push_samples(samples[offset : offset + args.frame_samples])
        )
        elapsed_per_chunk.append(time.perf_counter() - chunk_started)
    total_elapsed = time.perf_counter() - started
    audio_seconds = len(samples) / sample_rate
    milliseconds = np.asarray(elapsed_per_chunk) * 1000.0
    first_prediction_samples = feature_window_sample_span(recognizer.preprocessing)
    confirmed_change_samples = first_prediction_samples + (
        recognizer.decision.confirmation_frames - 1
    ) * recognizer.preprocessing.hop_length
    first_chord = next(
        (prediction for prediction in predictions if prediction.chord != "N"),
        None,
    )
    first_change = next(
        (prediction for prediction in predictions if prediction.changed),
        None,
    )
    first_correct = next(
        (
            prediction
            for prediction in predictions
            if expected_chord is not None and prediction.chord == expected_chord
        ),
        None,
    )
    first_audible_sample = next(
        (
            offset
            for offset in range(0, len(samples), args.frame_samples)
            if np.sqrt(
                np.mean(samples[offset : offset + args.frame_samples] ** 2)
            )
            >= args.audible_rms_threshold
        ),
        None,
    )
    first_audible_seconds = (
        first_audible_sample / sample_rate
        if first_audible_sample is not None
        else None
    )
    result = {
        "source": source,
        "audio_seconds": audio_seconds,
        "frame_samples": args.frame_samples,
        "input_frame_milliseconds": args.frame_samples / sample_rate * 1000.0,
        "algorithmic_first_prediction_milliseconds": (
            first_prediction_samples / sample_rate * 1000.0
        ),
        "algorithmic_confirmed_change_milliseconds": (
            confirmed_change_samples / sample_rate * 1000.0
        ),
        "preprocessing": {
            "feature_type": recognizer.preprocessing.feature_type,
            "sample_rate": sample_rate,
            "n_fft": recognizer.preprocessing.stft_n_fft,
            "hop_length": recognizer.preprocessing.hop_length,
            "context_frames": recognizer.preprocessing.context_frames,
        },
        "chunks": len(elapsed_per_chunk),
        "predictions": len(predictions),
        "first_prediction_audio_seconds": (
            predictions[0].timestamp_seconds if predictions else None
        ),
        "first_chord_audio_seconds": (
            first_chord.timestamp_seconds if first_chord else None
        ),
        "first_chord_label": first_chord.chord if first_chord else None,
        "expected_chord": expected_chord,
        "first_audible_audio_seconds": first_audible_seconds,
        "first_chord_after_audible_milliseconds": (
            (first_chord.timestamp_seconds - first_audible_seconds) * 1000.0
            if first_chord is not None and first_audible_seconds is not None
            else None
        ),
        "first_correct_audio_seconds": (
            first_correct.timestamp_seconds if first_correct else None
        ),
        "first_correct_after_audible_milliseconds": (
            (first_correct.timestamp_seconds - first_audible_seconds) * 1000.0
            if first_correct is not None and first_audible_seconds is not None
            else None
        ),
        "first_state_change_audio_seconds": (
            first_change.timestamp_seconds if first_change else None
        ),
        "state_changes": sum(prediction.changed for prediction in predictions),
        "final_chord_label": predictions[-1].chord if predictions else None,
        "compute_seconds": total_elapsed,
        "real_time_factor": total_elapsed / audio_seconds,
        "chunk_compute_ms_p50": float(np.percentile(milliseconds, 50)),
        "chunk_compute_ms_p95": float(np.percentile(milliseconds, 95)),
        "chunk_compute_ms_max": float(milliseconds.max()),
        "p95_within_input_frame_deadline": bool(
            np.percentile(milliseconds, 95)
            <= args.frame_samples / sample_rate * 1000.0
        ),
        "runtime": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "torch": torch.__version__,
            "device": args.device,
        },
    }
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
