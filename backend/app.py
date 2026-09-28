import atexit
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
import time

from flask import Flask, Response, jsonify, request
from werkzeug.utils import secure_filename

from iod_client import IodClient, IodError
from web_upload import web_upload

from analysis_cache import (
    identify_audio,
    load_cache_entry,
    save_cache_entry,
)

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

for d in [RUNTIME_DIR, INPUTS_DIR, OUTPUTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.register_blueprint(web_upload)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024

iod = IodClient()
recording_recognizer = None
live_feedback_session = None
live_feedback_session_lock = threading.Lock()


def get_recording_recognizer():
    global recording_recognizer
    if recording_recognizer is None:
        from models.chordsense_cnn.chord_recognition import (
            HailoChordRecognizer,
            OFFLINE_POSTPROCESSING,
        )

        recording_recognizer = HailoChordRecognizer(
            CUSTOM_MODEL_HEF,
            postprocessing=OFFLINE_POSTPROCESSING,
        )
    return recording_recognizer


@atexit.register
def close_recording_recognizer():
    if recording_recognizer is not None:
        recording_recognizer.close()


def get_live_feedback_session():
    global live_feedback_session
    with live_feedback_session_lock:
        if live_feedback_session is None:
            from live_feedback_session import LiveFeedbackSession

            live_feedback_session = LiveFeedbackSession(iod=iod)
    return live_feedback_session


@atexit.register
def close_live_feedback_session():
    if live_feedback_session is not None:
        live_feedback_session.stop()


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

    print(
        "=== /analyze request received ===",
        flush=True
    )

    if "file" not in request.files:

        return jsonify({
            "success": False,
            "error": "No file uploaded"
        }), 400


    file = request.files["file"]

    if not file.filename:

        return jsonify({
            "success": False,
            "error": "Empty filename"
        }), 400


    chord_dict = (
        request.form.get(
            "chord_dict",
            "submission"
        ).strip()
        or
        "submission"
    )


    safe_name = (
        secure_filename(
            file.filename
        )
        or
        "audio.wav"
    )

    suffix = (
        Path(safe_name).suffix
        or
        ".wav"
    )

    input_path = None
    output_lab_path = None

    try:

        # Save incoming audio temporarily.
        with tempfile.NamedTemporaryFile(
            dir=INPUTS_DIR,
            suffix=suffix,
            prefix="audio_",
            delete=False,
        ) as tmp_in:

            input_path = Path(tmp_in.name)

        file.save(str(input_path))
        print(
            f"Uploaded file: {file.filename}",
            flush=True
        )


        # --------------------------------
        # CACHE LOOKUP
        # --------------------------------

        (
            cache_key,
            audio_sha256
        ) = identify_audio(
            input_path,
            chord_dict
        )
        cached_entry = (load_cache_entry(cache_key))
        if cached_entry is not None:

            print(
                f"Analysis cache hit: "
                f"{cache_key}",
                flush=True
            )
            chords = parse_lab_file(cached_entry.lab_path)
            duration = (
                cached_entry.metadata.get(
                    "duration"
                )
                or
                (
                    chords[-1]["end"]
                    if chords
                    else 0.0
                )
            )


            return jsonify({

                "success": True,

                "chords":chords,

                "total_chords":len(chords),

                "duration":duration,

                "model_used":
                    cached_entry.metadata.get(
                        "model_used",
                        "chord-cnn-lstm"
                    ),

                "model_name":
                    cached_entry.metadata.get(
                        "model_name",
                        "Chord-CNN-LSTM"
                    ),

                "chord_dict":chord_dict,

                "processing_time":0.0,

                "stdout":"",

                "stderr":"",

                "lab_file":str(cached_entry.lab_path),

                "cached":True,

                "cache_key":cache_key,
            })


        # --------------------------------
        # CACHE MISS — RUN MODEL
        # --------------------------------
        print(
            "No cached analysis found.",
            flush=True
        )
        print(
            "Starting model inference...",
            flush=True
        )
        output_lab_path = (OUTPUTS_DIR / f"{input_path.stem}.lab")
        start_time = (time.perf_counter())
        stdout, stderr = run_model(input_path, output_lab_path, chord_dict)

        processing_time = (time.perf_counter() - start_time)

        chords = parse_lab_file(
            output_lab_path
        )

        duration = (
            chords[-1]["end"]
            if chords
            else 0.0
        )


        # --------------------------------
        # SAVE PERSISTENT CACHE
        # --------------------------------

        cache_entry = (
            save_cache_entry(

                cache_key=cache_key,

                audio_sha256=audio_sha256,

                original_name=safe_name,

                chord_dict=chord_dict,

                source_lab_path=output_lab_path,

                duration=duration,

                total_chords=len(chords),

                model_used="chord-cnn-lstm",

                model_name="Chord-CNN-LSTM"
            )
        )
        print(f"Saved analysis cache: "f"{cache_entry.lab_path}",flush=True)

        return jsonify({
            "success": True,
            "chords":chords,
            "total_chords":len(chords),
            "duration":duration,
            "model_used":"chord-cnn-lstm",
            "model_name":"Chord-CNN-LSTM",
            "chord_dict":chord_dict,
            "processing_time":processing_time,
            "stdout":stdout,
            "stderr":stderr,
            "lab_file":str(cache_entry.lab_path),
            "cached":False,
            "cache_key":cache_key,
        })
    except Exception as error:

        print(
            f"Analyze failed: {error}",
            flush=True
        )
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


    finally:
        # These files are temporary.
        # The persistent .lab is already
        # stored in analysis_cache.
        if (
            input_path is not None and
            input_path.exists()
        ):
            input_path.unlink()


        if (
            output_lab_path is not None and
            output_lab_path.exists()
        ):
            output_lab_path.unlink()


@app.post("/begin_recording")
def begin_recording():
    print("=== /begin_recording request received ===", flush=True)
    try:
        print("Attaching iod capture sink...", flush=True)
        iod.start_capture()
        return jsonify({
            "success": True,
            "message": "Recording started",
            "model_used": "chordsense-cnn-hef",
        })
    except IodError as e:
        print(f"Begin recording failed: {e}", flush=True)
        status = 409 if "stream" in str(e).lower() else 502
        return jsonify({"success": False, "error": str(e)}), status
    except Exception as e:
        print(f"Begin recording failed: {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.post("/end_recording")
def end_recording():
    print("=== /end_recording request received ===", flush=True)
    output_lab_path = OUTPUTS_DIR / "temp.lab"

    try:
        print("Detaching iod capture sink...", flush=True)
        wav_path, capture_duration = iod.stop_capture()
        print(f"Capture written to {wav_path} ({capture_duration:.2f}s)", flush=True)

        print("Running whole-recording inference on Hailo...", flush=True)
        recognizer = get_recording_recognizer()
        result = recognizer.analyze_file(wav_path)
        if not recognizer.write_lab_file(result, output_lab_path):
            raise RuntimeError("Chord recognition produced no output")
        chords = [
            {
                "start": segment.start,
                "end": segment.end,
                "chord": segment.chord,
                "confidence": segment.confidence,
            }
            for segment in result.segments
        ]
        print(f"Hailo inference produced {len(chords)} chord segments.", flush=True)

        return jsonify({
            "success": True,
            "chords": chords,
            "total_chords": len(chords),
            "duration": result.duration_seconds,
            "model_used": "chordsense-cnn-hef",
            "model_name": "ChordSenseCNN (Hailo)",
            "chord_dict": "submission",
            "processing_time": result.processing_seconds,
            "stdout": "",
            "stderr": "",
            "lab_file": str(output_lab_path),
            "wav_path": str(wav_path),
        })
    except IodError as e:
        print(f"End recording failed (iod): {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 502
    except Exception as e:
        print(f"End recording failed: {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.post("/feedback/start")
def start_live_feedback():
    from live_feedback_session import FeedbackBusyError

    try:
        return jsonify({"success": True, **get_live_feedback_session().start()}), 201
    except FeedbackBusyError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 503


@app.post("/feedback/pause")
def pause_live_feedback():
    from live_feedback_session import FeedbackStateError

    try:
        return jsonify({"success": True, **get_live_feedback_session().pause()})
    except FeedbackStateError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409


@app.post("/feedback/resume")
def resume_live_feedback():
    from live_feedback_session import FeedbackStateError

    try:
        return jsonify({"success": True, **get_live_feedback_session().resume()})
    except FeedbackStateError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except IodError as exc:
        status = 409 if "captur" in str(exc).lower() else 502
        return jsonify({"success": False, "error": str(exc)}), status


@app.post("/feedback/stop")
def stop_live_feedback():
    try:
        return jsonify({"success": True, **get_live_feedback_session().stop()})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.get("/feedback/status")
def live_feedback_status():
    return jsonify({"success": True, **get_live_feedback_session().status()})


@app.get("/feedback/events")
def live_feedback_events():
    from live_feedback_session import FeedbackStateError

    session = get_live_feedback_session()
    session_id = request.args.get("session_id", "")
    try:
        after = max(0, int(request.args.get("after", "0")))
    except ValueError:
        return jsonify({"success": False, "error": "invalid after sequence"}), 400
    if session_id != session.status()["session_id"]:
        return jsonify({"success": False, "error": "unknown feedback session"}), 404

    def generate():
        sequence = after
        while True:
            try:
                event = session.wait_event(session_id, sequence)
            except FeedbackStateError:
                break
            if event is None:
                yield ": keepalive\n\n"
                if session.status()["state"] == "idle":
                    break
                continue
            sequence = event["sequence"]
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
            if event.get("status") in {"stopped", "error"}:
                break

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=False)
