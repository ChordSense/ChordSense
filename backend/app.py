import os
import subprocess
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request
from models.chordsense_cnn.chord_recognition import ChordRecognizer
from werkzeug.utils import secure_filename
from guitar_input import Worker

BASE_DIR = Path(__file__).resolve().parent
PRETRAINED_MODEL_REPO = BASE_DIR / "models/chord-cnn-lstm-model"
CUSTOM_MODEL_REPO = BASE_DIR / "models/chordsense_cnn"

RUNTIME_DIR = BASE_DIR.parent / "runtime"
INPUTS_DIR = RUNTIME_DIR / "inputs"
OUTPUTS_DIR = RUNTIME_DIR / "outputs"

for d in [RUNTIME_DIR, INPUTS_DIR, OUTPUTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024

chord_recognizer = ChordRecognizer(PRETRAINED_MODEL_REPO / "model.pth")
worker = Worker()

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
    script_path = PRETRAINED_MODEL_REPO / "chord_recognition.py"
    venv_python = PRETRAINED_MODEL_REPO / "venv" / "bin" / "python"

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
        cwd=str(PRETRAINED_MODEL_REPO),
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
        "pretrained_model_repo": str(PRETRAINED_MODEL_REPO),
        "custom_model_repo": str(CUSTOM_MODEL_REPO),
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
        print("Starting recording...", flush=True)
        worker.start()
        return jsonify({"success": True, "message": "Recording started"})
    except Exception as e:
        print(f"Begin recording failed: {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.post("/end_recording")
def end_recording():
    print("=== /end_recording request received ===", flush=True)
    output_lab_path = OUTPUTS_DIR / "temp.lab"
    try:
        print("Ending recording...", flush=True)
        # Stop recording audio, async (not implemented rn)
        chroma_cqt, y_harmonics = worker.stop()

        if chroma is None:
            return jsonify({"success": False, "error": "No frames captured"}), 400
        chord_recognizer.from_chroma(y_harmonics, chroma_cqt)
        
        chords = parse_lab_file(output_lab_path)
        duration = chords[-1]["end"] if chords else 0.0

        print(f"Model finished. Parsed {len(chords)} chords.", flush=True)

        return jsonify({
            "success": True,
            "chords": chords,
            "total_chords": len(chords),
            "duration": duration,
            "model_used": "chordsense-cnn",
            "model_name": "ChordSenseCNN",
            "chord_dict": "ADD_HERE",
            "processing_time": 0.0,
            "stdout": "ADD_HERE",
            "stderr": "ADD_HERE",
            "lab_file": output_lab_path,
        })
    except Exception as e:
        print(f"End recording failed: {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5051, debug=False)
