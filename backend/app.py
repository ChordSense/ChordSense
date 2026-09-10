import atexit
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

from iod_client import IodClient, IodError
from web_upload import web_upload

BASE_DIR = Path(__file__).resolve().parent
# Original: MODEL_REPO = BASE_DIR / "model_repo"
MODEL_REPO = BASE_DIR / "models" / "chord-cnn-lstm-model"
DEFAULT_CUSTOM_MODEL_HEF = (
    BASE_DIR / "models" / "chordsense_cnn" / "checkpoints" / "chordsense.hef"
)
CUSTOM_MODEL_HEF = Path(
    os.environ.get(
        "CHORDSENSE_HEF",
        str(DEFAULT_CUSTOM_MODEL_HEF),
    )
)
RUNTIME_DIR = BASE_DIR.parent / "runtime"
INPUTS_DIR = RUNTIME_DIR / "inputs"
OUTPUTS_DIR = RUNTIME_DIR / "outputs"
CAPTURES_DIR = RUNTIME_DIR / "captures"

for d in [RUNTIME_DIR, INPUTS_DIR, OUTPUTS_DIR, CAPTURES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.register_blueprint(web_upload)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024

iod = IodClient()
recording_session = None
recording_session_lock = threading.Lock()


def get_recording_session():
    global recording_session
    with recording_session_lock:
        if recording_session is None:
            from models.chordsense_cnn.streaming import StreamingChordRecognizer
            from recording_session import HailoRecordingSession

            recognizer = StreamingChordRecognizer.from_hef(CUSTOM_MODEL_HEF)
            recording_session = HailoRecordingSession(
                iod,
                recognizer,
                CAPTURES_DIR,
                OUTPUTS_DIR / "temp.lab",
            )
    return recording_session


@atexit.register
def close_recording_session():
    if recording_session is not None:
        recording_session.close()


def parse_lab_file(lab_path: Path):
    results = []
    with lab_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=2)
            if len(parts) != 3:
                continue
            start, end, chord = parts
            results.append({
                "start": float(start),
                "end": float(end),
                "chord": chord,
                "confidence": 1.0,
            })
    return results


def run_model(audio_path: Path, output_lab_path: Path, chord_dict: str):
    script_path = MODEL_REPO / "chord_recognition.py"

    if os.name == "nt":
        venv_python = MODEL_REPO / "venv" / "Scripts" / "python.exe"
    else:
        venv_python = MODEL_REPO / "venv" / "bin" / "python"

    if not script_path.exists():
        raise RuntimeError(f"Missing model script: {script_path}")
    if not venv_python.exists():
        raise RuntimeError(f"Missing model venv python: {venv_python}")

    cmd = [
        str(venv_python),
        str(script_path),
        str(audio_path),
        str(output_lab_path),
        chord_dict,
    ]

    proc = subprocess.run(
        cmd,
        cwd=str(MODEL_REPO),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    if proc.returncode != 0:
        raise RuntimeError(
            "Model inference failed.\n\n"
            f"STDOUT:\n{proc.stdout}\n\n"
            f"STDERR:\n{proc.stderr}"
        )

    if not output_lab_path.exists():
        raise RuntimeError("Model finished but did not create the .lab output file.")

    return proc.stdout, proc.stderr


@app.get("/health")
def health():
    return jsonify({
        "success": True,
        "message": "ChordSenseOfficial backend running",
        "model_repo": str(MODEL_REPO),
        "record_model_backend": "hailo",
        "record_model_hef": str(CUSTOM_MODEL_HEF),
    })


@app.post("/analyze")
def analyze():
    print("=== /analyze request received ===", flush=True)

    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "error": "Empty filename"}), 400

    chord_dict = request.form.get("chord_dict", "submission").strip() or "submission"
    safe_name = secure_filename(file.filename)
    suffix = Path(safe_name).suffix or ".wav"

    with tempfile.NamedTemporaryFile(
        dir=INPUTS_DIR,
        suffix=suffix,
        prefix="audio_",
        delete=False,
    ) as tmp_in:
        input_path = Path(tmp_in.name)

    file.save(str(input_path))
    output_lab_path = OUTPUTS_DIR / f"{input_path.stem}.lab"

    print(f"Uploaded file: {file.filename}", flush=True)
    print(f"Saved temp input: {input_path}", flush=True)
    print(f"Chord dictionary: {chord_dict}", flush=True)
    print("Starting model inference...", flush=True)

    try:
        stdout, stderr = run_model(input_path, output_lab_path, chord_dict)
        chords = parse_lab_file(output_lab_path)
        duration = chords[-1]["end"] if chords else 0.0

        print(f"Model finished. Parsed {len(chords)} chords.", flush=True)

        return jsonify({
            "success": True,
            "chords": chords,
            "total_chords": len(chords),
            "duration": duration,
            "model_used": "chord-cnn-lstm",
            "model_name": "Chord-CNN-LSTM",
            "chord_dict": chord_dict,
            "processing_time": 0.0,
            "stdout": stdout,
            "stderr": stderr,
            "lab_file": str(output_lab_path),
        })
    except Exception as e:
        print(f"Analyze failed: {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.post("/begin_recording")
def begin_recording():
    print("=== /begin_recording request received ===", flush=True)
    try:
        print("Starting iod stream and Hailo inference...", flush=True)
        get_recording_session().start()
        return jsonify({
            "success": True,
            "message": "Recording started with live Hailo inference",
            "model_used": "chordsense-cnn-hef",
        })
    except IodError as e:
        print(f"Begin recording failed: {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 502
    except Exception as e:
        print(f"Begin recording failed: {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.get("/recording_predictions")
def recording_predictions():
    if recording_session is None:
        return jsonify({
            "success": True,
            "active": False,
            "audio_seconds": 0.0,
            "prediction_count": 0,
            "latest_prediction": None,
            "error": None,
        })
    return jsonify({"success": True, **recording_session.snapshot()})


@app.post("/end_recording")
def end_recording():
    print("=== /end_recording request received ===", flush=True)

    try:
        print("Stopping iod stream and finalizing live predictions...", flush=True)
        if recording_session is None:
            raise RuntimeError("no recording is in progress")
        result = recording_session.stop()
        chords = [
            {
                "start": segment.start,
                "end": segment.end,
                "chord": segment.chord,
                "confidence": segment.confidence,
            }
            for segment in result.segments
        ]
        print(f"Recording finished with {len(chords)} chord segments.", flush=True)

        return jsonify({
            "success": True,
            "chords": chords,
            "total_chords": len(chords),
            "duration": result.duration_seconds,
            "model_used": "chordsense-cnn-hef",
            "model_name": "ChordSenseCNN (Hailo)",
            "chord_dict": "submission",
            "processing_time": 0.0,
            "stdout": "",
            "stderr": "",
            "lab_file": str(result.lab_path),
            "wav_path": str(result.wav_path),
        })
    except Exception as e:
        print(f"End recording failed: {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 500
    

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=False)
