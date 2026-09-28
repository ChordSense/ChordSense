"""Compile a prepared causal-STFT ChordSense ONNX bundle on x86 Ubuntu DFC.

This script is supplied for the user to run in the Hailo AI Software Suite.
It is deliberately not invoked by the local implementation workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from hailo_sdk_client import ClientRunner


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    inputs = json.loads((bundle / "inputs.json").read_text(encoding="utf-8"))
    onnx_path = bundle / "chordsense_live.onnx"
    calibration_path = bundle / "calibration_nhwc.npy"
    if inputs.get("format") != "chordsense-live-hef-inputs-v1":
        raise ValueError("Unsupported live HEF input bundle")
    if sha256(onnx_path) != inputs["onnx_sha256"] or sha256(calibration_path) != inputs["calibration_sha256"]:
        raise ValueError("Bundle input hash mismatch")
    calibration = np.load(calibration_path, allow_pickle=False)
    if calibration.shape != (800, 12, 15, 1) or calibration.dtype != np.float32 or not np.isfinite(calibration).all():
        raise ValueError("Expected 800 finite NHWC causal-STFT calibration windows")
    hef_path = bundle / "chordsense_live.hef"
    manifest_path = bundle / "chordsense_live.candidate.json"
    if hef_path.exists() or manifest_path.exists():
        raise FileExistsError("Candidate HEF or manifest already exists; use a clean bundle directory")

    runner = ClientRunner(hw_arch="hailo10h")
    runner.translate_onnx_model(
        str(onnx_path), "chordsense_live",
        start_node_names=["live_chroma"], end_node_names=["logits"],
    )
    runner.save_har(str(bundle / "chordsense_live.parsed.har"))
    runner.optimize(np.ascontiguousarray(calibration))
    runner.save_har(str(bundle / "chordsense_live.optimized.har"))
    hef = runner.compile()
    hef_path.write_bytes(hef if isinstance(hef, bytes) else bytes(hef))
    if hef_path.stat().st_size == 0:
        raise RuntimeError("Compiler returned an empty HEF")
    manifest = {
        "format": "chordsense-live-hef-candidate-v1",
        "hef_sha256": sha256(hef_path),
        "onnx_sha256": inputs["onnx_sha256"],
        "checkpoint_sha256": inputs["checkpoint_sha256"],
        "preprocessing": inputs["preprocessing"],
        "class_names": inputs["class_names"],
        "temperature": inputs["temperature"],
        "temperature_calibrated": inputs["temperature_calibrated"],
        "calibration_windows": inputs["calibration_windows"],
        "pi_verified": False,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"hef": str(hef_path), "sha256": manifest["hef_sha256"], "manifest": str(manifest_path)}, indent=2))


if __name__ == "__main__":
    main()
