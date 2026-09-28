"""Capture and compare a golden baseline for the external CNN-BiLSTM model.

This tool deliberately lives outside the third-party model submodule.  It imports
that model without modifying it, exercises the same preprocessing, five-fold
ensemble, XHMM decoding, and LAB writer as the production entry point, and
records enough intermediate data to validate an accelerated implementation.

Example::

    python backend/tools/lstm_baseline.py capture song.wav \
        --output-dir artifacts/lstm-baseline/song \
        --warmups 1 --repeats 5

    python backend/tools/lstm_baseline.py compare \
        artifacts/lstm-baseline/song/baseline.npz candidate.npz \
        --output artifacts/lstm-baseline/song/comparison.json

The generated archive is intentionally backend-neutral.  A Hailo runner can
write the same keys and use ``compare`` without importing the legacy model.
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np


SCRIPT_PATH = Path(__file__).resolve()
BACKEND_DIR = SCRIPT_PATH.parents[1]
REPOSITORY_ROOT = BACKEND_DIR.parent
DEFAULT_MODEL_DIR = BACKEND_DIR / "models" / "chord-cnn-lstm-model"
HEAD_NAMES = ("triad", "bass", "seventh", "ninth", "eleventh", "thirteenth")
HEAD_SIZES = (73, 13, 4, 4, 3, 3)
HEAD_OFFSETS = tuple(np.cumsum((0, *HEAD_SIZES)).tolist())
DEFAULT_PROJECTIONS_SECONDS = (180, 240, 300)
FORMAT_VERSION = 1
LEGACY_PARITY_ATOL = 1e-5
LEGACY_PARITY_RTOL = 1e-5


class BaselineError(RuntimeError):
    """Raised when the legacy model contract cannot be reproduced safely."""


@contextlib.contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _array_sha256(array: np.ndarray) -> str:
    """Hash dtype, shape, and canonical C-order bytes, not just values."""
    canonical = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(canonical.dtype.str.encode("ascii"))
    digest.update(json.dumps(canonical.shape, separators=(",", ":")).encode("ascii"))
    digest.update(canonical.tobytes(order="C"))
    return digest.hexdigest()


def _array_metadata(array: np.ndarray) -> dict[str, Any]:
    return {
        "shape": list(array.shape),
        "dtype": str(array.dtype),
        "sha256": _array_sha256(array),
        "bytes": int(array.nbytes),
    }


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _git_revision(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))


def _timing_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"samples": [], "count": 0}
    return {
        "samples": [float(value) for value in values],
        "count": len(values),
        "first": float(values[0]),
        "minimum": float(min(values)),
        "mean": float(statistics.fmean(values)),
        "median": float(statistics.median(values)),
        "p95": _percentile(values, 95),
        "maximum": float(max(values)),
    }


def _peak_rss_bytes() -> int:
    maximum = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes; Linux reports KiB.
    return maximum if sys.platform == "darwin" else maximum * 1024


def _current_rss_bytes() -> int | None:
    try:
        import psutil
    except ImportError:
        return None
    return int(psutil.Process().memory_info().rss)


def _head_slices() -> tuple[slice, ...]:
    return tuple(
        slice(HEAD_OFFSETS[i], HEAD_OFFSETS[i + 1]) for i in range(len(HEAD_SIZES))
    )


def _split_heads(array: np.ndarray) -> tuple[np.ndarray, ...]:
    if array.ndim != 2 or array.shape[1] != sum(HEAD_SIZES):
        raise BaselineError(
            f"Expected a [frames, {sum(HEAD_SIZES)}] head tensor, got {array.shape}"
        )
    return tuple(array[:, head_slice] for head_slice in _head_slices())


def _sync_torch(torch_module: Any, uses_cuda: bool) -> None:
    if uses_cuda:
        torch_module.cuda.synchronize()


def _system_metadata(torch_module: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "numpy": np.__version__,
        "torch": torch_module.__version__,
        "torch_num_threads": torch_module.get_num_threads(),
        "torch_num_interop_threads": torch_module.get_num_interop_threads(),
        "cuda_available": bool(torch_module.cuda.is_available()),
        "cpu_count": os.cpu_count(),
    }
    try:
        import librosa

        metadata["librosa"] = librosa.__version__
    except (ImportError, AttributeError):
        metadata["librosa"] = None
    try:
        import psutil

        metadata["memory_total_bytes"] = int(psutil.virtual_memory().total)
    except ImportError:
        metadata["memory_total_bytes"] = None
    return metadata


def _capture_baseline(args: argparse.Namespace) -> dict[str, Any]:
    audio_path = args.audio.resolve()
    model_dir = args.model_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio input not found: {audio_path}")
    if not model_dir.is_dir():
        raise FileNotFoundError(f"External model directory not found: {model_dir}")
    if args.warmups < 0 or args.repeats <= 0:
        raise ValueError("--warmups cannot be negative and --repeats must be positive")

    output_dir.mkdir(parents=True, exist_ok=True)
    lab_path = output_dir / "baseline.lab"
    archive_path = output_dir / "baseline.npz"
    manifest_path = output_dir / "baseline.json"

    # mir.common binds WORKING_PATH to cwd at import time, so this matches the
    # production subprocess's cwd.  Resolve every user path before entering.
    sys.path.insert(0, str(model_dir))
    capture_started = time.perf_counter()
    with _working_directory(model_dir):
        import torch
        import torch.nn.functional as torch_functional

        from chord_recognition import MODEL_NAMES
        from chordnet_ismir_naive import (
            ChordNet,
            SHIFT_HIGH,
            SHIFT_STEP,
            SPEC_DIM,
        )
        from extractors.cqt import CQTV2
        from extractors.xhmm_ismir import XHMMDecoder
        from io_new.chordlab_io import ChordLabIO
        from mir import DataEntry, io
        from mir.nn.train import NetworkInterface
        from settings import DEFAULT_HOP_LENGTH, DEFAULT_SR

        if args.torch_threads is not None:
            if args.torch_threads <= 0:
                raise ValueError("--torch-threads must be positive")
            torch.set_num_threads(args.torch_threads)

        timings: dict[str, Any] = {}
        entry = DataEntry()
        entry.prop.set("sr", DEFAULT_SR)
        entry.prop.set("hop_length", DEFAULT_HOP_LENGTH)
        entry.append_file(str(audio_path), io.MusicIO, "music")

        started = time.perf_counter()
        audio = np.ascontiguousarray(entry.music, dtype=np.float32)
        timings["audio_decode"] = time.perf_counter() - started

        entry.append_extractor(CQTV2, "cqt", cache_enabled=False)
        started = time.perf_counter()
        cqt = np.ascontiguousarray(entry.cqt, dtype=np.float32)
        timings["cqt"] = time.perf_counter() - started
        crop_begin = SHIFT_HIGH * SHIFT_STEP
        cropped_cqt = np.ascontiguousarray(
            cqt[:, crop_begin : crop_begin + SPEC_DIM], dtype=np.float32
        )
        if cqt.ndim != 2 or cqt.shape[1] != 288 or cropped_cqt.shape[1] != SPEC_DIM:
            raise BaselineError(
                f"Unexpected CQT contract: raw={cqt.shape}, cropped={cropped_cqt.shape}"
            )

        fold_logits: list[np.ndarray] = []
        fold_probabilities: list[np.ndarray] = []
        fold_reports: list[dict[str, Any]] = []
        checkpoint_paths: list[Path] = []
        rss_before_models = _current_rss_bytes()

        for fold_index, model_name in enumerate(MODEL_NAMES):
            checkpoint_path = model_dir / "cache_data" / f"{model_name}.sdict"
            if not checkpoint_path.is_file():
                raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")
            checkpoint_paths.append(checkpoint_path)

            load_started = time.perf_counter()
            network = NetworkInterface(
                ChordNet(None),
                model_name,
                load_checkpoint=False,
                load_path="cache_data",
            )
            load_seconds = time.perf_counter() - load_started
            if not network.finalized:
                raise BaselineError(
                    f"Checkpoint did not load as finalized: {checkpoint_path}"
                )

            input_tensor = torch.from_numpy(cropped_cqt).view(
                1, len(cropped_cqt), SPEC_DIM
            )
            if network.net.use_gpu:
                input_tensor = input_tensor.cuda()

            with torch.inference_mode():
                warmup_started = time.perf_counter()
                for _ in range(args.warmups):
                    warmup_outputs = network.net.feed(input_tensor)
                    for output in warmup_outputs:
                        torch_functional.softmax(output, dim=1)
                _sync_torch(torch, network.net.use_gpu)
                warmup_seconds = time.perf_counter() - warmup_started

                forward_samples: list[float] = []
                softmax_samples: list[float] = []
                captured_logits: np.ndarray | None = None
                captured_probabilities: np.ndarray | None = None
                for _ in range(args.repeats):
                    _sync_torch(torch, network.net.use_gpu)
                    started = time.perf_counter()
                    outputs = network.net.feed(input_tensor)
                    _sync_torch(torch, network.net.use_gpu)
                    forward_samples.append(time.perf_counter() - started)

                    started = time.perf_counter()
                    probabilities = tuple(
                        torch_functional.softmax(output, dim=1) for output in outputs
                    )
                    _sync_torch(torch, network.net.use_gpu)
                    softmax_samples.append(time.perf_counter() - started)

                    if captured_logits is None:
                        captured_logits = np.ascontiguousarray(
                            torch.cat(outputs, dim=1).detach().cpu().numpy(),
                            dtype=np.float32,
                        )
                        captured_probabilities = np.ascontiguousarray(
                            torch.cat(probabilities, dim=1).detach().cpu().numpy(),
                            dtype=np.float32,
                        )

            assert captured_logits is not None and captured_probabilities is not None
            if captured_logits.shape != (len(cqt), sum(HEAD_SIZES)):
                raise BaselineError(
                    f"Fold {fold_index} logits violate contract: {captured_logits.shape}"
                )

            legacy_parity: dict[str, Any] | None = None
            legacy_inference_seconds: float | None = None
            if args.verify_legacy:
                legacy_started = time.perf_counter()
                legacy_heads = network.inference(cqt)
                legacy_inference_seconds = time.perf_counter() - legacy_started
                legacy_probabilities = np.ascontiguousarray(
                    np.concatenate(legacy_heads, axis=1), dtype=np.float32
                )
                absolute_error = np.abs(captured_probabilities - legacy_probabilities)
                head_argmax_agreement = {
                    name: float(
                        np.mean(
                            captured_probabilities[:, head_slice].argmax(axis=1)
                            == legacy_probabilities[:, head_slice].argmax(axis=1)
                        )
                    )
                    for name, head_slice in zip(HEAD_NAMES, _head_slices())
                }
                legacy_parity = {
                    "maximum_absolute_error": float(absolute_error.max(initial=0.0)),
                    "mean_absolute_error": float(absolute_error.mean()),
                    "array_equal": bool(
                        np.array_equal(captured_probabilities, legacy_probabilities)
                    ),
                    "allclose": bool(
                        np.allclose(
                            captured_probabilities,
                            legacy_probabilities,
                            atol=LEGACY_PARITY_ATOL,
                            rtol=LEGACY_PARITY_RTOL,
                        )
                    ),
                    "absolute_tolerance": LEGACY_PARITY_ATOL,
                    "relative_tolerance": LEGACY_PARITY_RTOL,
                    "head_argmax_agreement": head_argmax_agreement,
                }
                if not legacy_parity["allclose"] or not all(
                    agreement == 1.0 for agreement in head_argmax_agreement.values()
                ):
                    raise BaselineError(
                        f"Fold {fold_index} direct wrapper differs from legacy inference: "
                        f"max abs error {legacy_parity['maximum_absolute_error']}"
                    )
                # The fixture is the untouched production call's output.  Keep
                # direct logits for accelerator comparison, but use these exact
                # legacy probabilities for ensembling and final decoding.
                captured_probabilities = legacy_probabilities

            fold_logits.append(captured_logits)
            fold_probabilities.append(captured_probabilities)
            fold_reports.append(
                {
                    "fold": fold_index,
                    "model_name": model_name,
                    "checkpoint": str(checkpoint_path),
                    "checkpoint_sha256": _sha256_file(checkpoint_path),
                    "load_seconds": load_seconds,
                    "warmup_seconds": warmup_seconds,
                    "forward_seconds": _timing_summary(forward_samples),
                    "softmax_seconds": _timing_summary(softmax_samples),
                    "legacy_inference_seconds": legacy_inference_seconds,
                    "legacy_parity": legacy_parity,
                    "rss_after_bytes": _current_rss_bytes(),
                }
            )
            del input_tensor, network
            gc.collect()

        started = time.perf_counter()
        # This expression intentionally mirrors chord_recognition.py rather
        # than vectorizing differently, preserving NumPy's aggregation order.
        ensemble_heads = tuple(
            np.mean(
                [probability[:, head_slice] for probability in fold_probabilities],
                axis=0,
            )
            for head_slice in _head_slices()
        )
        ensemble_probabilities = np.ascontiguousarray(
            np.concatenate(ensemble_heads, axis=1), dtype=np.float32
        )
        timings["ensemble"] = time.perf_counter() - started

        started = time.perf_counter()
        decoder = XHMMDecoder(template_file=f"data/{args.chord_dict}_chord_list.txt")
        chordlab = decoder.decode_to_chordlab(entry, ensemble_heads, False)
        timings["xhmm_decode"] = time.perf_counter() - started

        started = time.perf_counter()
        entry.append_data(chordlab, ChordLabIO, "chord")
        entry.save("chord", str(lab_path))
        timings["lab_write"] = time.perf_counter() - started

        # decode_to_chordlab merges runs; call decode with the same all-ones beat
        # array to retain the frame-level acceptance signal in the fixture.
        started = time.perf_counter()
        decoded_tags = np.asarray(
            decoder.decode(ensemble_heads, np.ones((len(cqt),), dtype=np.int8)),
            dtype=np.str_,
        )
        timings["frame_decode_for_fixture"] = time.perf_counter() - started

        lab_start = np.asarray([segment[0] for segment in chordlab], dtype=np.float64)
        lab_end = np.asarray([segment[1] for segment in chordlab], dtype=np.float64)
        lab_chord = np.asarray([segment[2] for segment in chordlab], dtype=np.str_)

        archive: dict[str, np.ndarray] = {
            "input_cqt_288": cqt,
            "input_cqt_252": cropped_cqt,
            "ensemble_probabilities": ensemble_probabilities,
            "decoded_tags": decoded_tags,
            "lab_start_seconds": lab_start,
            "lab_end_seconds": lab_end,
            "lab_chords": lab_chord,
        }
        for fold_index, (logits, probabilities) in enumerate(
            zip(fold_logits, fold_probabilities)
        ):
            archive[f"fold_{fold_index}_logits"] = logits
            archive[f"fold_{fold_index}_probabilities"] = probabilities

        if args.write_archive:
            archive_started = time.perf_counter()
            np.savez_compressed(archive_path, **archive)
            timings["archive_write"] = time.perf_counter() - archive_started
        else:
            timings["archive_write"] = None

        duration_seconds = len(audio) / DEFAULT_SR
        first_compute = sum(
            report["forward_seconds"]["first"] + report["softmax_seconds"]["first"]
            for report in fold_reports
        )
        median_compute = sum(
            report["forward_seconds"]["median"] + report["softmax_seconds"]["median"]
            for report in fold_reports
        )
        legacy_compute = (
            sum(float(report["legacy_inference_seconds"]) for report in fold_reports)
            if args.verify_legacy
            else None
        )
        model_load_seconds = sum(report["load_seconds"] for report in fold_reports)
        postprocess_seconds = (
            timings["ensemble"] + timings["xhmm_decode"] + timings["lab_write"]
        )
        current_compute = (
            legacy_compute if legacy_compute is not None else first_compute
        )
        variable_current_seconds = (
            timings["audio_decode"]
            + timings["cqt"]
            + current_compute
            + postprocess_seconds
        )
        variable_median_seconds = (
            timings["audio_decode"]
            + timings["cqt"]
            + median_compute
            + postprocess_seconds
        )
        current_request_seconds = model_load_seconds + variable_current_seconds
        preloaded_request_seconds = variable_median_seconds
        per_audio_second_current = variable_current_seconds / duration_seconds
        per_audio_second_median = variable_median_seconds / duration_seconds

        source_files = (
            "chord_recognition.py",
            "chordnet_ismir_naive.py",
            "extractors/cqt.py",
            "extractors/xhmm_ismir.py",
            "io_new/chordlab_io.py",
            "mir/nn/train.py",
        )
        source_hashes = {
            relative: _sha256_file(model_dir / relative) for relative in source_files
        }
        lab_bytes = lab_path.read_bytes()
        captured_wall_seconds = time.perf_counter() - capture_started
        manifest: dict[str, Any] = {
            "format": "chordsense-cnn-bilstm-baseline",
            "format_version": FORMAT_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "contract": {
                "sample_rate": DEFAULT_SR,
                "hop_length": DEFAULT_HOP_LENGTH,
                "cqt_bins": 288,
                "cqt_bins_per_octave": 36,
                "cqt_fmin": "F#0",
                "crop_begin": crop_begin,
                "model_input_bins": SPEC_DIM,
                "model_input_shape": [1, len(cqt), SPEC_DIM],
                "folds": len(MODEL_NAMES),
                "head_names": list(HEAD_NAMES),
                "head_sizes": list(HEAD_SIZES),
                "head_offsets": list(HEAD_OFFSETS),
                "ensemble": "per-head arithmetic mean of post-softmax fold probabilities",
                "decoder": "XHMMDecoder with all frames eligible for transitions",
                "chord_dictionary": args.chord_dict,
            },
            "source": {
                "audio_path": str(audio_path),
                "audio_bytes": audio_path.stat().st_size,
                "audio_sha256": _sha256_file(audio_path),
                "decoded_samples": len(audio),
                "duration_seconds": duration_seconds,
                "cqt_frames": len(cqt),
                "frame_period_seconds": DEFAULT_HOP_LENGTH / DEFAULT_SR,
            },
            "provenance": {
                "repository_root": str(REPOSITORY_ROOT),
                "repository_revision": _git_revision(REPOSITORY_ROOT),
                "model_directory": str(model_dir),
                "model_revision": _git_revision(model_dir),
                "source_sha256": source_hashes,
                "checkpoints": [
                    {
                        "path": str(path),
                        "sha256": report["checkpoint_sha256"],
                    }
                    for path, report in zip(checkpoint_paths, fold_reports)
                ],
            },
            "runtime": _system_metadata(torch),
            "benchmark_configuration": {
                "warmups": args.warmups,
                "repeats": args.repeats,
                "verify_legacy": args.verify_legacy,
                "torch_threads_override": args.torch_threads,
            },
            "timing_seconds": {
                **timings,
                "folds": fold_reports,
                "model_load_total": model_load_seconds,
                "model_compute_first_total": first_compute,
                "model_compute_median_total": median_compute,
                "legacy_model_inference_total": legacy_compute,
                "postprocess_total": postprocess_seconds,
                "modeled_current_subprocess_request": current_request_seconds,
                "modeled_preloaded_request": preloaded_request_seconds,
                "capture_wall_including_validation_and_archive": captured_wall_seconds,
            },
            "performance": {
                "current_request_real_time_factor": current_request_seconds
                / duration_seconds,
                "preloaded_request_real_time_factor": preloaded_request_seconds
                / duration_seconds,
                "model_first_frames_per_second": len(cqt) / first_compute,
                "model_median_frames_per_second": len(cqt) / median_compute,
                "legacy_model_frames_per_second": (
                    len(cqt) / legacy_compute if legacy_compute is not None else None
                ),
                "projections": {
                    str(seconds): {
                        "audio_seconds": seconds,
                        "current_subprocess_seconds": (
                            model_load_seconds + per_audio_second_current * seconds
                        ),
                        "preloaded_seconds": per_audio_second_median * seconds,
                    }
                    for seconds in DEFAULT_PROJECTIONS_SECONDS
                },
            },
            "memory": {
                "rss_before_models_bytes": rss_before_models,
                "rss_after_capture_bytes": _current_rss_bytes(),
                "peak_rss_bytes": _peak_rss_bytes(),
            },
            "outputs": {
                "lab_path": str(lab_path),
                "lab_sha256": _sha256_bytes(lab_bytes),
                "lab_segments": len(chordlab),
                "archive_path": str(archive_path) if args.write_archive else None,
                "archive_sha256": (
                    _sha256_file(archive_path) if args.write_archive else None
                ),
                "arrays": {
                    key: _array_metadata(value) for key, value in archive.items()
                },
            },
        }
        _json_dump(manifest_path, manifest)

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def _compare_numeric(reference: np.ndarray, candidate: np.ndarray) -> dict[str, Any]:
    if reference.shape != candidate.shape:
        return {
            "shape_match": False,
            "reference_shape": list(reference.shape),
            "candidate_shape": list(candidate.shape),
        }
    reference64 = reference.astype(np.float64, copy=False)
    candidate64 = candidate.astype(np.float64, copy=False)
    difference = candidate64 - reference64
    absolute = np.abs(difference)
    denominator = np.maximum(np.abs(reference64), np.finfo(np.float64).eps)
    return {
        "shape_match": True,
        "dtype_match": reference.dtype == candidate.dtype,
        "reference_dtype": str(reference.dtype),
        "candidate_dtype": str(candidate.dtype),
        "array_equal": bool(np.array_equal(reference, candidate)),
        "maximum_absolute_error": float(absolute.max(initial=0.0)),
        "mean_absolute_error": float(absolute.mean()),
        "root_mean_square_error": float(np.sqrt(np.mean(difference**2))),
        "maximum_relative_error": float((absolute / denominator).max(initial=0.0)),
    }


def _compare_archive_payloads(
    reference: dict[str, np.ndarray], candidate: dict[str, np.ndarray]
) -> dict[str, Any]:
    common_keys = sorted(reference.keys() & candidate.keys())
    missing_keys = sorted(reference.keys() - candidate.keys())
    extra_keys = sorted(candidate.keys() - reference.keys())
    arrays: dict[str, Any] = {}
    for key in common_keys:
        reference_array = reference[key]
        candidate_array = candidate[key]
        if np.issubdtype(reference_array.dtype, np.number) and np.issubdtype(
            candidate_array.dtype, np.number
        ):
            result = _compare_numeric(reference_array, candidate_array)
            if (
                result.get("shape_match")
                and reference_array.ndim == 2
                and reference_array.shape[1] == sum(HEAD_SIZES)
            ):
                result["head_argmax_agreement"] = {
                    name: float(
                        np.mean(
                            reference_array[:, head_slice].argmax(axis=1)
                            == candidate_array[:, head_slice].argmax(axis=1)
                        )
                    )
                    for name, head_slice in zip(HEAD_NAMES, _head_slices())
                }
        else:
            result = {
                "shape_match": reference_array.shape == candidate_array.shape,
                "dtype_match": reference_array.dtype == candidate_array.dtype,
                "array_equal": bool(np.array_equal(reference_array, candidate_array)),
            }
            if key == "decoded_tags" and reference_array.shape == candidate_array.shape:
                result["frame_agreement"] = float(
                    np.mean(reference_array == candidate_array)
                )
        arrays[key] = result

    final_keys = ("decoded_tags", "lab_start_seconds", "lab_end_seconds", "lab_chords")
    final_keys_present = all(key in common_keys for key in final_keys)
    final_output_equal = final_keys_present and all(
        arrays[key].get("array_equal", False) for key in final_keys
    )
    return {
        "reference_keys": sorted(reference),
        "candidate_keys": sorted(candidate),
        "missing_keys": missing_keys,
        "extra_keys": extra_keys,
        "arrays": arrays,
        "final_output_keys_present": final_keys_present,
        "final_output_equal": final_output_equal,
    }


def _load_archive(path: Path) -> dict[str, np.ndarray]:
    if not path.is_file():
        raise FileNotFoundError(f"Fixture archive not found: {path}")
    with np.load(path, allow_pickle=False) as archive:
        return {key: np.array(archive[key], copy=True) for key in archive.files}


def _compare_archives(args: argparse.Namespace) -> dict[str, Any]:
    reference_path = args.reference.resolve()
    candidate_path = args.candidate.resolve()
    reference = _load_archive(reference_path)
    candidate = _load_archive(candidate_path)
    report = {
        "format": "chordsense-cnn-bilstm-comparison",
        "format_version": FORMAT_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "reference": {
            "path": str(reference_path),
            "sha256": _sha256_file(reference_path),
        },
        "candidate": {
            "path": str(candidate_path),
            "sha256": _sha256_file(candidate_path),
        },
        **_compare_archive_payloads(reference, candidate),
    }
    if args.output is not None:
        _json_dump(args.output.resolve(), report)
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.require_unchanged and not report["final_output_equal"]:
        return {**report, "exit_code": 2}
    return {**report, "exit_code": 0}


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser(
        "capture", help="Run the untouched legacy pipeline and save a golden fixture"
    )
    capture.add_argument("audio", type=Path)
    capture.add_argument("--output-dir", type=Path, required=True)
    capture.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    capture.add_argument(
        "--chord-dict",
        choices=("full", "ismir2017", "submission", "extended"),
        default="submission",
    )
    capture.add_argument("--warmups", type=int, default=1)
    capture.add_argument("--repeats", type=int, default=3)
    capture.add_argument("--torch-threads", type=int)
    capture.add_argument(
        "--no-verify-legacy",
        dest="verify_legacy",
        action="store_false",
        help="Skip the extra production inference call used to prove wrapper parity",
    )
    capture.add_argument(
        "--no-archive",
        dest="write_archive",
        action="store_false",
        help="Write the JSON and LAB only (useful for a lightweight timing run)",
    )
    capture.set_defaults(verify_legacy=True, write_archive=True)

    compare = subparsers.add_parser(
        "compare", help="Compare a candidate NPZ fixture with the golden fixture"
    )
    compare.add_argument("reference", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--output", type=Path)
    compare.add_argument(
        "--require-unchanged",
        action="store_true",
        help="Exit 2 unless decoded frame tags and final LAB segments are exactly equal",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "capture":
        _capture_baseline(args)
        return 0
    if args.command == "compare":
        return int(_compare_archives(args)["exit_code"])
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
