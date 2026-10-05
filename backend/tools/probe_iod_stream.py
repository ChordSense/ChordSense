"""Measure IOD output cadence, transport age, continuity, and signal quality.

Run on the Pi from backend while recording capture is inactive:
  python -m tools.probe_iod_stream --seconds 15 --output /tmp/iod-probe.json

This subscribes to the existing daemon and closes its subscriber afterward.
It cannot measure native ADC conversion rate: IOD exports resampled output.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from iod_client import IodClient


RATE = 22_050


def percentiles(values):
    if not values:
        return None
    return {name: float(np.percentile(values, percentile))
            for name, percentile in (("p50", 50), ("p95", 95), ("p99", 99), ("max", 100))}


def summarize_frames(frames):
    gaps = reversed_frames = 0
    capture_intervals, arrival_intervals, ages = [], [], []
    for previous, current in zip(frames, frames[1:]):
        delta = current["sample_index"] - previous["sample_end"]
        gaps += max(0, delta)
        reversed_frames += delta < 0
        arrival_intervals.append((current["received_at_ns"] - previous["received_at_ns"]) / 1e6)
        if previous["captured_at_ns"] is not None and current["captured_at_ns"] is not None:
            capture_intervals.append((current["captured_at_ns"] - previous["captured_at_ns"]) / 1e6)
    for item in frames:
        if item["captured_at_ns"] is not None:
            ages.append((item["received_at_ns"] - item["captured_at_ns"]) / 1e6)
    timed = [item for item in frames if item["captured_at_ns"] is not None]
    output_rate = None
    if len(timed) > 1:
        elapsed = (timed[-1]["captured_at_ns"] - timed[0]["captured_at_ns"]) / 1e9
        if elapsed > 0:
            output_rate = (timed[-1]["sample_end"] - timed[0]["sample_end"]) / elapsed
    return {
        "frames_received": len(frames), "samples_received": sum(item["sample_count"] for item in frames),
        "missing_output_samples": gaps, "out_of_order_frames": reversed_frames,
        "output_grid_rate_hz": output_rate,
        "output_grid_rate_error_ppm": (output_rate / RATE - 1) * 1e6 if output_rate else None,
        "native_adc_rate_hz": None,
        "native_adc_rate_note": "Not observable from resampled stream; instrument native reads separately",
        "capture_interval_ms": percentiles(capture_intervals),
        "arrival_interval_ms": percentiles(arrival_intervals),
        "receive_age_ms": percentiles(ages),
        "negative_age_frames": sum(age < 0 for age in ages),
        "ac_rms": percentiles([item["ac_rms"] for item in frames]),
        "dc_offset": percentiles([item["dc_offset"] for item in frames]),
        "clipping_fraction": percentiles([item["clipping_fraction"] for item in frames]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--frame-samples", type=int, default=441)
    parser.add_argument("--socket")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be finite and positive")
    if not 1 <= args.frame_samples <= 4410:
        parser.error("--frame-samples must be between 1 and 4410")
    frames, timeouts = [], 0
    with IodClient(socket_path=args.socket).open_stream(args.frame_samples, timeout=1) as stream:
        started = time.monotonic()
        while time.monotonic() - started < args.seconds:
            try:
                frame = stream.read_frame()
            except TimeoutError:
                timeouts += 1
                continue
            received = time.monotonic_ns()
            values = np.frombuffer(frame.pcm16le, dtype="<i2").astype(np.float32) / 32768.0
            dc = float(values.mean())
            frames.append({
                "sample_index": frame.sample_index, "sample_end": frame.sample_index + frame.sample_count,
                "sample_count": frame.sample_count, "captured_at_ns": frame.captured_at_ns,
                "received_at_ns": received, "dc_offset": dc,
                "ac_rms": float(np.sqrt(np.mean((values - dc) ** 2))),
                "clipping_fraction": float(np.mean(np.abs(values) >= 0.98)),
            })
    report = {"scope": "IOD resampled output on the same host monotonic clock",
              "requested_seconds": args.seconds, "wall_seconds": time.monotonic() - started,
              "frame_samples": args.frame_samples, "timeouts": timeouts,
              **summarize_frames(frames), "frames": frames}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "frames"}, indent=2))


if __name__ == "__main__":
    main()
