"""Export the causal-STFT CNN and balanced real-feature Hailo calibration data.

Run on a machine with the project's model Python dependencies and the cached
rodriler/isolated-guitar-chords dataset. The held-out test split is used only
for ONNX parity and later Pi Hailo verification, never for quantization.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
from datasets import Audio, load_dataset

from .audio_processing import PreprocessingConfig, create_feature_windows, load_dataset_audio, preprocess_audio
from .config import CHORD_CLASSES, NUM_CLASSES
from .model import build_model
from .streaming import DEFAULT_STREAMING_PREPROCESSING_CONFIG


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_preprocessing(checkpoint: dict) -> PreprocessingConfig:
    if (checkpoint.get("model_name") != "baseline" or
            checkpoint.get("class_names") != CHORD_CLASSES or checkpoint.get("seed") != 42):
        raise ValueError("Expected the 25-class baseline causal-STFT checkpoint")
    config = PreprocessingConfig(**checkpoint["preprocessing"])
    actual = asdict(config)
    expected = asdict(DEFAULT_STREAMING_PREPROCESSING_CONFIG)
    actual.pop("version")
    expected.pop("version")
    if actual != expected:
        raise ValueError("Checkpoint preprocessing differs from live extractor")
    return config


def selected_windows(dataset, indices: list[int], config: PreprocessingConfig, count: int, noise_count: int):
    windows = []
    labels = []
    for index in indices:
        row = dataset[index]
        processed = preprocess_audio(load_dataset_audio(row), config)
        values = create_feature_windows(processed.chroma, config).values
        # Use interior windows: the first and final windows often contain only
        # attack or decay, and would bias the verification toward silence.
        window_count = noise_count if int(row["label"]) == CHORD_CLASSES.index("Noise") else count
        offsets = np.linspace(0, len(values) - 1, window_count + 2, dtype=np.int64)[1:-1]
        windows.extend(values[offsets])
        labels.extend([int(row["label"])] * window_count)
    return np.ascontiguousarray(np.stack(windows), dtype=np.float32), np.asarray(labels, dtype=np.int64)


def balanced_indices(dataset, forbidden_names: set[str], recordings_per_class: int, seed: int, noise_recordings: int = 2):
    encoded = dataset.cast_column("audio", Audio(decode=False))
    by_class = {index: [] for index in range(NUM_CLASSES)}
    for index, row in enumerate(encoded):
        if Path(row["audio"]["path"]).name not in forbidden_names:
            by_class[int(row["label"])].append(index)
    rng = np.random.default_rng(seed)
    selected = []
    for class_index, candidates in by_class.items():
        required = noise_recordings if CHORD_CLASSES[class_index] == "Noise" else recordings_per_class
        if len(candidates) < required:
            raise ValueError(f"Only {len(candidates)} eligible recordings for {CHORD_CLASSES[class_index]}")
        selected.extend(int(i) for i in rng.choice(candidates, required, replace=False))
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--validation-recordings", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260927)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    config = check_preprocessing(checkpoint)
    with args.validation_recordings.open(newline="", encoding="utf-8") as source:
        validation_names = {row["path"] for row in csv.DictReader(source)}
    dataset = load_dataset("rodriler/isolated-guitar-chords")
    if dataset["train"].features["label"].names != CHORD_CLASSES:
        raise ValueError("Dataset class order differs from checkpoint")
    calibration_indices = balanced_indices(dataset["train"], validation_names, 8, args.seed)
    evaluation_indices = balanced_indices(dataset["test"], set(), 4, args.seed + 1)
    calibration, calibration_labels = selected_windows(dataset["train"], calibration_indices, config, 4, 16)
    evaluation, evaluation_labels = selected_windows(dataset["test"], evaluation_indices, config, 2, 4)
    if not np.isfinite(calibration).all() or not np.isfinite(evaluation).all():
        raise ValueError("Feature windows contain non-finite values")
    if calibration.shape != (800, 12, 15) or evaluation.shape != (200, 12, 15):
        raise ValueError("Unexpected calibration or evaluation shape")

    # DFC accepts NHWC, while ONNX and our Torch/Hailo inference wrapper use NCHW.
    calibration_nhwc = np.ascontiguousarray(calibration[..., None])
    evaluation_nchw = np.ascontiguousarray(evaluation[:, None, ...])
    np.save(out / "calibration_nhwc.npy", calibration_nhwc)
    np.save(out / "evaluation_nchw.npy", evaluation_nchw)
    np.save(out / "evaluation_labels.npy", evaluation_labels)

    model = build_model(NUM_CLASSES, "baseline")
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    onnx_path = out / "chordsense_live.onnx"
    with torch.inference_mode():
        torch.onnx.export(
            model, torch.zeros(1, 1, 12, 15), str(onnx_path),
            input_names=["live_chroma"], output_names=["logits"],
            opset_version=11, dynamo=False,
        )
        reference = model(torch.from_numpy(evaluation_nchw)).numpy()
    onnx.checker.check_model(str(onnx_path), full_check=True)
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    onnx_logits = np.stack([
        session.run(["logits"], {"live_chroma": item[None, ...]})[0][0]
        for item in evaluation_nchw
    ])
    maximum_difference = float(np.max(np.abs(reference - onnx_logits)))
    agreement = float(np.mean(reference.argmax(axis=1) == onnx_logits.argmax(axis=1)))
    if maximum_difference > 1e-4 or agreement < 1.0:
        raise RuntimeError(f"Torch/ONNX export mismatch: {maximum_difference=}, {agreement=}")
    np.save(out / "evaluation_torch_logits.npy", reference)
    metadata = {
        "format": "chordsense-live-hef-inputs-v1",
        "model": "e1b-causal-stft-seed-42",
        "checkpoint_sha256": sha256(args.checkpoint),
        "onnx_sha256": sha256(onnx_path),
        "calibration_sha256": sha256(out / "calibration_nhwc.npy"),
        "evaluation_sha256": sha256(out / "evaluation_nchw.npy"),
        "evaluation_labels_sha256": sha256(out / "evaluation_labels.npy"),
        "evaluation_torch_logits_sha256": sha256(out / "evaluation_torch_logits.npy"),
        "preprocessing": asdict(config),
        "class_names": CHORD_CLASSES,
        "calibration_recordings": len(calibration_indices),
        "calibration_windows": len(calibration),
        "calibration_windows_per_class": np.bincount(calibration_labels, minlength=NUM_CLASSES).tolist(),
        "evaluation_recordings": len(evaluation_indices),
        "evaluation_windows": len(evaluation),
        "torch_onnx_max_abs_diff": maximum_difference,
        "torch_onnx_top1_agreement": agreement,
        "temperature": float(checkpoint.get("calibration", {}).get("temperature", 1.0)),
        "temperature_calibrated": "calibration" in checkpoint,
    }
    (out / "inputs.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
