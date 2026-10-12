"""Hardware-independent offline inference, shared by the device and worker."""

from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess

ALLOWED_CHORD_DICTIONARIES = {"submission", "full", "ismir2017", "extended"}
DEFAULT_MODEL_REPO = Path(__file__).resolve().parent / "models" / "chord-cnn-lstm-model"


def parse_lab_file(path: Path) -> list[dict]:
    chords = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        start, end = float(parts[0]), float(parts[1])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise ValueError("Model produced invalid chord times")
        chords.append({"start": start, "end": end, "chord": parts[2], "confidence": 1.0})
    return chords


def run_local_model(audio_path: Path, lab_path: Path, chord_dict: str,
                    model_repo: Path | None = None) -> tuple[str, str]:
    if chord_dict not in ALLOWED_CHORD_DICTIONARIES:
        raise ValueError("Unsupported chord dictionary")
    repo = Path(model_repo or os.environ.get("CHORDSENSE_MODEL_REPO", DEFAULT_MODEL_REPO))
    script = repo / "chord_recognition.py"
    default_python = repo / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python = Path(os.environ.get("CHORDSENSE_MODEL_PYTHON", default_python))
    if not script.is_file() or not python.is_file():
        raise RuntimeError("Offline model and its Python environment must be installed on this host")
    # Callers may reserve the destination with NamedTemporaryFile. Ensure a
    # successful process really created new output rather than retaining that
    # empty placeholder or a stale analysis.
    Path(lab_path).unlink(missing_ok=True)
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env.setdefault(name, "1")
    result = subprocess.run(
        [str(python.resolve()), str(script.resolve()), str(Path(audio_path).resolve()),
         str(Path(lab_path).resolve()), chord_dict],
        cwd=repo, capture_output=True, text=True, env=env,
        timeout=float(os.environ.get("CHORDSENSE_MODEL_TIMEOUT", "900")),
    )
    if result.returncode != 0:
        # Keep diagnostics in server logs rather than exposing paths in API responses.
        raise RuntimeError(f"Offline model inference failed: {result.stderr[-2000:]}")
    if not Path(lab_path).is_file():
        raise RuntimeError("Offline model produced no chord output")
    return result.stdout, result.stderr
