"""Replay a 22.05 kHz PCM16 guitar capture through the experimental DSP path.

Run from backend, for example:
  python -m models.chordsense_cnn.benchmark_template ../runtime/benchmarks/take.wav \
      --segment E:8:11 --segment A:12.5:15
"""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

from .audio_processing import PreprocessingConfig
from .template import DEFAULT_TEMPLATE_PREPROCESSING_CONFIG, StreamingTemplateRecognizer


SAMPLE_RATE = 22_050
CHUNK_SAMPLES = 441


def read_pcm16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as recording:
        if (
            recording.getnchannels() != 1
            or recording.getsampwidth() != 2
            or recording.getframerate() != SAMPLE_RATE
            or recording.getcomptype() != "NONE"
        ):
            raise ValueError("Expected mono 16-bit PCM WAV at 22,050 Hz")
        return np.frombuffer(recording.readframes(recording.getnframes()), dtype="<i2")


def parse_segment(value: str) -> tuple[str, float, float]:
    label, start, end = value.split(":", 2)
    start_time, end_time = float(start), float(end)
    if not label or start_time < 0 or end_time <= start_time:
        raise ValueError(f"Invalid segment: {value}")
    return label, start_time, end_time


def replay(
    samples: np.ndarray,
    *,
    preprocessing: PreprocessingConfig = DEFAULT_TEMPLATE_PREPROCESSING_CONFIG,
) -> list[dict]:
    """Mirror the selected template route's AC gate and long-silence reset."""
    recognizer = StreamingTemplateRecognizer(preprocessing=preprocessing)
    events: list[dict] = []
    base_sample: int | None = None
    silence_samples = 0
    silence_reset = False
    hold_samples = 0
    try:
        for offset in range(0, len(samples), CHUNK_SAMPLES):
            chunk = samples[offset : offset + CHUNK_SAMPLES]
            normalized = chunk.astype(np.float32) / 32768.0
            ac_rms = float(np.sqrt(np.mean((normalized - normalized.mean()) ** 2)))
            clipped = float(np.mean(np.abs(normalized) >= 0.98))
            if ac_rms >= 0.008:
                hold_samples = round(0.15 * SAMPLE_RATE)
            else:
                hold_samples = max(0, hold_samples - len(chunk))
            quality = (
                "clipping" if clipped >= 0.05 else
                "ok" if ac_rms >= 0.008 or hold_samples > 0 else "silence"
            )
            if quality == "silence":
                silence_samples += len(chunk)
                if silence_samples >= SAMPLE_RATE:
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
                silence_samples = 0
            if base_sample is None:
                base_sample = offset
            for prediction in recognizer.push_samples(chunk):
                label = prediction.chord if quality == "ok" else None
                events.append({
                    "seconds": (base_sample + prediction.sample_index) / SAMPLE_RATE,
                    "chord": label,
                    "raw_chord": prediction.raw_chord,
                    "input_quality": quality,
                    "quality_uncertain": prediction.quality_uncertain,
                    "root_margin": prediction.root_margin,
                    "quality_margin": prediction.quality_margin,
                    "concentration": prediction.concentration,
                })
    finally:
        recognizer.close()
    return events


def summarize(events: list[dict], segment: tuple[str, float, float]) -> dict:
    label, start, end = segment
    selected = [event for event in events if start <= event["seconds"] < end]
    named = [event for event in selected if event["chord"] not in (None, "N")]
    counts: dict[str, int] = {}
    for event in named:
        chord = event["chord"]
        counts[chord] = counts.get(chord, 0) + 1
    return {
        "label": label,
        "start": start,
        "end": end,
        "prediction_windows": len(selected),
        "named_windows": len(named),
        "exact_windows": sum(event["chord"] == label for event in named),
        "green_eligible_windows": sum(
            event["chord"] == label and not event["quality_uncertain"]
            for event in named
        ),
        "labels": counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--segment", action="append", default=[], type=parse_segment,
                        help="Actual played chord and stable interval as LABEL:START:END")
    args = parser.parse_args()
    events = replay(read_pcm16(args.wav))
    result = {
        "wav": str(args.wav),
        "method": "experimental-causal-chroma-template",
        "predictions": len(events),
        "segments": [summarize(events, item) for item in args.segment],
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
