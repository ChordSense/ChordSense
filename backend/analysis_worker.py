"""Standalone offline-analysis service. Never imports iod or Hailo bindings.

Run this on a separate compute host, not in a Vercel Function or on the Pi.
The device queues requests so this service can use normal HTTP responses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

from analysis_cache import CACHE_VERSION
from offline_model import ALLOWED_CHORD_DICTIONARIES, parse_lab_file, run_local_model

ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}


def create_analysis_worker(*, runtime_dir: Path | None = None, runner=None, token: str | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("CHORDSENSE_WORKER_MAX_UPLOAD_MB", "300")) * 1024 * 1024
    worker_token = token if token is not None else os.environ.get("CHORDSENSE_WORKER_TOKEN", "")
    directory = Path(runtime_dir or Path(__file__).resolve().parent.parent / "runtime" / "worker")
    inputs = directory / "inputs"
    cache = directory / "cache"
    inputs.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    inference = runner or run_local_model
    slot = threading.BoundedSemaphore(1)

    @app.before_request
    def authorize():
        if not worker_token:
            return jsonify(success=False, error="Analysis worker authentication is not configured"), 503
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {worker_token}".encode()):
            return jsonify(success=False, error="Unauthorized"), 401

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(success=False, error="Audio exceeds the worker upload limit"), 413

    @app.get("/health")
    def health():
        return jsonify(success=True, role="offline-analysis-worker", hardware_required=False)

    @app.post("/analyze")
    def analyze():
        audio = request.files.get("file")
        if audio is None or not audio.filename:
            return jsonify(success=False, error="No audio file uploaded"), 400
        name = secure_filename(audio.filename)
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_AUDIO_EXTENSIONS:
            return jsonify(success=False, error="Unsupported audio type"), 400
        chord_dict = request.form.get("chord_dict", "submission").strip() or "submission"
        if chord_dict not in ALLOWED_CHORD_DICTIONARIES:
            return jsonify(success=False, error="Unsupported chord dictionary"), 400
        if not slot.acquire(blocking=False):
            return jsonify(success=False, error="Analysis worker is busy; retry later"), 503
        try:
            # The only large temporary asset is the incoming audio. Repeated
            # analyses reuse small JSON results identified by content + model.
            with tempfile.TemporaryDirectory(prefix="analysis-", dir=inputs) as temporary:
                source = Path(temporary) / f"audio{suffix}"
                lab = Path(temporary) / "chords.lab"
                audio.save(source)
                if source.stat().st_size == 0:
                    return jsonify(success=False, error="Audio file is empty"), 400
                digest = hashlib.sha256()
                with source.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                key = hashlib.sha256(f"{CACHE_VERSION}\0{chord_dict}\0{digest.hexdigest()}".encode()).hexdigest()
                cached = cache / f"{key}.json"
                if cached.is_file():
                    try:
                        result = json.loads(cached.read_text(encoding="utf-8"))
                        return jsonify(**{**result, "cached": True, "processing_time": 0.0})
                    except (OSError, ValueError):
                        pass
                started = time.perf_counter()
                inference(source, lab, chord_dict)
                chords = parse_lab_file(lab)
                result = {
                    "success": True, "chords": chords, "total_chords": len(chords),
                    "duration": chords[-1]["end"] if chords else 0.0,
                    "processing_time": time.perf_counter() - started,
                    "model_used": "chord-cnn-lstm", "model_name": "Chord-CNN-LSTM (remote worker)",
                    "chord_dict": chord_dict, "cached": False, "cache_key": key,
                }
                staging = Path(temporary) / "result.json"
                staging.write_text(json.dumps(result), encoding="utf-8")
                staging.replace(cached)
                return jsonify(result)
        except Exception:
            app.logger.exception("Offline analysis failed")
            return jsonify(success=False, error="Offline analysis failed; inspect worker logs"), 500
        finally:
            slot.release()

    return app


if __name__ == "__main__":
    if not os.environ.get("CHORDSENSE_WORKER_TOKEN"):
        raise SystemExit("Set CHORDSENSE_WORKER_TOKEN before starting the worker")
    from waitress import serve

    serve(create_analysis_worker(), host=os.environ.get("CHORDSENSE_WORKER_HOST", "127.0.0.1"),
          port=int(os.environ.get("CHORDSENSE_WORKER_PORT", "5052")), threads=2)
