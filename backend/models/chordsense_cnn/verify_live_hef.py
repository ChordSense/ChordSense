"""Compare a candidate live HEF with Torch on the held-out guitar windows on Pi.

Only a passing comparison writes the runtime manifest, which the feedback
service requires before it will load the HEF.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from .inference_backends import HailoInferenceBackend


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
    candidate = json.loads((bundle / "chordsense_live.candidate.json").read_text(encoding="utf-8"))
    if candidate.get("format") != "chordsense-live-hef-candidate-v1":
        raise ValueError("Unsupported HEF candidate manifest")
    paths = {
        "evaluation_nchw.npy": "evaluation_sha256",
        "evaluation_labels.npy": "evaluation_labels_sha256",
        "evaluation_torch_logits.npy": "evaluation_torch_logits_sha256",
    }
    for filename, key in paths.items():
        if sha256(bundle / filename) != inputs[key]:
            raise ValueError(f"Evaluation file hash mismatch: {filename}")
    hef_path = bundle / "chordsense_live.hef"
    if sha256(hef_path) != candidate["hef_sha256"]:
        raise ValueError("Candidate HEF hash mismatch")
    features = np.load(bundle / "evaluation_nchw.npy", allow_pickle=False)
    labels = np.load(bundle / "evaluation_labels.npy", allow_pickle=False)
    torch_logits = np.load(bundle / "evaluation_torch_logits.npy", allow_pickle=False)
    if features.shape != (200, 1, 12, 15) or labels.shape != (200,) or torch_logits.shape != (200, 25):
        raise ValueError("Unexpected held-out evaluation shapes")
    predictions = []
    durations_ms = []
    backend = HailoInferenceBackend(hef_path, (1, 1, 12, 15), 25)
    try:
        for item in features:
            started = time.perf_counter_ns()
            predictions.append(backend.infer(item[None, ...])[0].copy())
            durations_ms.append((time.perf_counter_ns() - started) / 1_000_000)
    finally:
        backend.close()
    hailo_logits = np.asarray(predictions, dtype=np.float32)
    torch_labels = torch_logits.argmax(axis=1)
    hailo_labels = hailo_logits.argmax(axis=1)
    agreement = float(np.mean(torch_labels == hailo_labels))
    torch_accuracy = float(np.mean(torch_labels == labels))
    hailo_accuracy = float(np.mean(hailo_labels == labels))
    metrics = {
        "samples": len(features),
        "top1_agreement": agreement,
        "torch_accuracy": torch_accuracy,
        "hailo_accuracy": hailo_accuracy,
        "accuracy_drop": torch_accuracy - hailo_accuracy,
        "p95_inference_ms": float(np.percentile(durations_ms, 95)),
        "mean_abs_logit_difference": float(np.mean(np.abs(torch_logits - hailo_logits))),
    }
    (bundle / "pi-verification.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    if agreement < 0.95 or torch_accuracy - hailo_accuracy > 0.05:
        raise RuntimeError("Live HEF did not pass the held-out Pi comparison")
    manifest_path = bundle / "chordsense_live.json"
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite {manifest_path}")
    candidate["format"] = "chordsense-live-hef-verified-v1"
    candidate["pi_verified"] = True
    candidate["verification"] = metrics
    manifest_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    print(f"Verified runtime manifest: {manifest_path}")


if __name__ == "__main__":
    main()
