"""ChordSense device API: hardware control and cached/offloaded analysis."""

import atexit
import errno
import json
import math
import os
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Request, Response, current_app, jsonify, request
from werkzeug.utils import secure_filename

from analysis_cache import identify_audio, load_cache_entry, save_cache_entry
from analysis_jobs import AnalysisJobs, AnalysisQueueFull
from device_access import install_device_access, is_local_desktop_request, pairing_manager
from iod_client import IodClient, IodError
from offline_model import ALLOWED_CHORD_DICTIONARIES, parse_lab_file, run_local_model
from playback_speed import PlaybackSpeedController, PlaybackSpeedError
from pairing import PairingError
from web_upload import ALLOWED_AUDIO_EXTENSIONS, resolve_upload_path, web_upload

from recordings_library import (
    rename_recording,
    RecordingLimitReached,
    start_recording_capture,
    list_recordings as get_saved_recordings,
    save_recording_analysis,
    delete_recording as remove_saved_recording,
)

BASE_DIR = Path(__file__).resolve().parent
MODEL_REPO = BASE_DIR / "models" / "chord-cnn-lstm-model"
DEFAULT_CUSTOM_MODEL_HEF = BASE_DIR / "models/chordsense_cnn/checkpoints/chordsense.hef"
CUSTOM_MODEL_HEF = Path(os.environ.get("CHORDSENSE_HEF", str(DEFAULT_CUSTOM_MODEL_HEF)))
RUNTIME_DIR = BASE_DIR.parent / "runtime"
INPUTS_DIR = RUNTIME_DIR / "inputs"
OUTPUTS_DIR = RUNTIME_DIR / "outputs"
for directory in (RUNTIME_DIR, INPUTS_DIR, OUTPUTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

class DeviceRequest(Request):
    @property
    def max_content_length(self):
        if request.path == "/api/pairing/claim":
            return 1024
        if is_local_desktop_request():
            return current_app.config["MAX_CONTENT_LENGTH"]
        return current_app.config["COMPANION_MAX_CONTENT_LENGTH"]


app = Flask(__name__, static_folder=None)
app.request_class = DeviceRequest
app.register_blueprint(web_upload)
app.config["COMPANION_MAX_CONTENT_LENGTH"] = int(os.environ.get("CHORDSENSE_MAX_UPLOAD_MB", "95")) * 1024 * 1024
# Keep the native app's existing 300 MiB request limit. Only companion uploads
# need the smaller tunnel-compatible cap; Flask applies it per request above.
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024
install_device_access(app)

iod = IodClient()
playback = PlaybackSpeedController(iod, RUNTIME_DIR / "playback-speed-cache")
analysis_jobs = AnalysisJobs()
# Synchronous desktop requests share the compute limit with queued jobs.
analysis_execution_lock = threading.Lock()
live_feedback_session = None
live_feedback_session_lock = threading.RLock()


def create_recording_recognizer():
    from models.chordsense_cnn.chord_recognition import HailoChordRecognizer, OFFLINE_POSTPROCESSING
    return HailoChordRecognizer(CUSTOM_MODEL_HEF, postprocessing=OFFLINE_POSTPROCESSING)


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


def _remote_analysis_enabled():
    return bool(os.environ.get("CHORDSENSE_ANALYSIS_URL", "").strip())


def _write_lab(chords: list[dict], path: Path):
    with path.open("w", encoding="utf-8") as lab:
        for chord in chords:
            lab.write(f"{chord['start']} {chord['end']} {chord['chord']}\n")


def run_model(audio_path: Path, output_lab_path: Path, chord_dict: str):
    """Select an external worker explicitly, without a costly local fallback."""
    if _remote_analysis_enabled():
        from remote_analysis import analyze_remotely
        result = analyze_remotely(audio_path, chord_dict)
        _write_lab(result["chords"], output_lab_path)
        return "", "", result
    stdout, stderr = run_local_model(audio_path, output_lab_path, chord_dict, model_repo=MODEL_REPO)
    return stdout, stderr, None


def _chord_dictionary(value):
    if not isinstance(value, str):
        raise ValueError("chord_dict must be a string")
    value = value.strip() or "submission"
    if value not in ALLOWED_CHORD_DICTIONARIES:
        raise ValueError("Unsupported chord dictionary")
    return value


def _snapshot_uploaded_audio(source: Path) -> Path:
    """Keep this inode alive when the companion replaces its library name."""
    snapshot = INPUTS_DIR / f"stored-{uuid.uuid4().hex}{source.suffix}"
    try:
        os.link(source, snapshot)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        # A separately mounted inputs directory cannot share an inode. Open
        # the source once so deletion/replacement cannot switch bytes mid-copy.
        try:
            with source.open("rb") as audio, snapshot.open("xb") as output:
                shutil.copyfileobj(audio, output, length=64 * 1024)
        except Exception:
            snapshot.unlink(missing_ok=True)
            raise
    return snapshot


def analyze_source(audio_path: Path, original_name: str, chord_dict: str) -> dict:
    """Analyze a stored source once and reuse the persistent content cache."""
    with analysis_execution_lock:
        cache_key, audio_sha256 = identify_audio(audio_path, chord_dict)
        cached = load_cache_entry(cache_key)
        if cached is not None:
            chords = parse_lab_file(cached.lab_path)
            return {
                "success": True, "chords": chords, "total_chords": len(chords),
                "duration": cached.metadata.get("duration", chords[-1]["end"] if chords else 0.0),
                "model_used": cached.metadata.get("model_used", "chord-cnn-lstm"),
                "model_name": cached.metadata.get("model_name", "Chord-CNN-LSTM"),
                "chord_dict": chord_dict, "processing_time": 0.0,
                "stdout": "", "stderr": "", "lab_file": str(cached.lab_path),
                "cached": True, "cache_key": cache_key,
            }
        with tempfile.NamedTemporaryFile(dir=OUTPUTS_DIR, suffix=".lab", delete=False) as temp:
            output_lab_path = Path(temp.name)
        try:
            started = time.perf_counter()
            stdout, stderr, remote_result = run_model(audio_path, output_lab_path, chord_dict)
            chords = remote_result["chords"] if remote_result else parse_lab_file(output_lab_path)
            duration = remote_result["duration"] if remote_result else (chords[-1]["end"] if chords else 0.0)
            model_used = remote_result["model_used"] if remote_result else "chord-cnn-lstm"
            model_name = remote_result["model_name"] if remote_result else "Chord-CNN-LSTM"
            entry = save_cache_entry(
                cache_key=cache_key, audio_sha256=audio_sha256, original_name=original_name,
                chord_dict=chord_dict, source_lab_path=output_lab_path, duration=duration,
                total_chords=len(chords), model_used=model_used, model_name=model_name,
            )
            return {
                "success": True, "chords": chords, "total_chords": len(chords),
                "duration": duration, "model_used": model_used, "model_name": model_name,
                "chord_dict": chord_dict, "processing_time": time.perf_counter() - started,
                "stdout": stdout, "stderr": stderr, "lab_file": str(entry.lab_path),
                "cached": False, "cache_key": cache_key,
            }
        finally:
            output_lab_path.unlink(missing_ok=True)


def _run_analysis_request(work, cleanup=None, overflow_result=None):
    def run():
        try:
            return work()
        except Exception as exc:
            return {"success": False, "error": str(exc) or type(exc).__name__}
        finally:
            if cleanup is not None:
                cleanup()

    if request.args.get("async") == "1":
        try:
            job_id = analysis_jobs.submit(run)
        except AnalysisQueueFull as exc:
            if cleanup is not None:
                cleanup()
            response = jsonify({"success": False, "error": str(exc), **(overflow_result or {})})
            response.headers["Retry-After"] = "5"
            return response, 503
        return jsonify({"success": True, "job_id": job_id, "status_url": f"/api/jobs/{job_id}"}), 202
    result = run()
    return jsonify(result), 200 if result.get("success") else 500


@app.errorhandler(413)
def upload_too_large(_error):
    if request.path == "/api/pairing/claim":
        return jsonify({"success": False, "error": "Pairing request is too large.", "error_code": "invalid_request"}), 413
    return jsonify({"success": False, "error": "Audio exceeds the configured upload limit."}), 413


@app.before_request
def validate_json_body():
    if request.is_json and not isinstance(request.get_json(silent=True), dict):
        return jsonify({"success": False, "error": "JSON request body must be an object."}), 400


def _companion_website_url():
    value = os.environ.get("CHORDSENSE_COMPANION_URL", "").strip()
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or "?" in value or "#" in value
                or any(character.isspace() or ord(character) < 32 for character in value)):
            return ""
        parsed.port  # Reject malformed ports before showing the address.
    except ValueError:
        return ""
    return value


def _native_pairing_response(result):
    return jsonify({"success": True, **result, "website_url": _companion_website_url()})


@app.errorhandler(PairingError)
def pairing_failed(error):
    return jsonify({"success": False, "error": str(error), "error_code": error.code}), error.status


@app.post("/api/pairing/start")
def start_upload_pairing():
    return _native_pairing_response(pairing_manager(app).start())


@app.get("/api/pairing/status")
def upload_pairing_status():
    return _native_pairing_response(pairing_manager(app).status())


@app.post("/api/pairing/cancel")
def cancel_upload_pairing():
    return _native_pairing_response(pairing_manager(app).cancel())


@app.post("/api/pairing/revoke")
def revoke_upload_sessions():
    return _native_pairing_response(pairing_manager(app).revoke_all())


@app.post("/api/pairing/claim")
def claim_upload_pairing():
    body = request.get_json(silent=True) or {}
    return jsonify({"success": True, **pairing_manager(app).claim(body.get("code"), request.headers.get("Origin"))})


@app.post("/api/pairing/logout")
def logout_upload_session():
    authorization = request.headers.get("Authorization", "")
    pairing_manager(app).logout(authorization[7:], request.headers.get("Origin"))
    return jsonify({"success": True})


@app.get("/health")
def health():
    return jsonify({
        "success": True, "message": "ChordSense device API running",
        "analysis_backend": "remote" if _remote_analysis_enabled() else "local",
        "record_model_backend": "remote" if _remote_analysis_enabled() else "hailo",
        "max_upload_bytes": request.max_content_length,
        "capabilities": {"companion_uploads": True, "local_analysis": True, "local_live_feedback": True, "local_device_playback": True},
    })


@app.get("/api/jobs/<job_id>")
def analysis_job_status(job_id):
    status = analysis_jobs.status(job_id)
    if status is None:
        return jsonify({"success": False, "error": "Analysis job not found or expired."}), 404
    return jsonify({"success": True, "job_id": job_id, **status})


@app.post("/analyze")
def analyze():
    audio_file = request.files.get("file")
    if audio_file is None or not audio_file.filename:
        return jsonify({"success": False, "error": "No file uploaded"}), 400
    safe_name = secure_filename(audio_file.filename)
    if Path(safe_name).suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        return jsonify({"success": False, "error": "Unsupported audio type."}), 400
    try:
        chord_dict = _chord_dictionary(request.form.get("chord_dict", "submission"))
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    with tempfile.NamedTemporaryFile(dir=INPUTS_DIR, suffix=Path(safe_name).suffix, delete=False) as temp:
        input_path = Path(temp.name)
    try:
        audio_file.save(input_path)
    except Exception:
        input_path.unlink(missing_ok=True)
        raise
    return _run_analysis_request(
        lambda: analyze_source(input_path, safe_name, chord_dict),
        cleanup=lambda: input_path.unlink(missing_ok=True),
    )


@app.post("/analyze_uploaded")
def analyze_uploaded():
    body = request.get_json(silent=True) or {}
    try:
        audio_path = resolve_upload_path(body.get("filename"))
        chord_dict = _chord_dictionary(body.get("chord_dict", "submission"))
        input_path = _snapshot_uploaded_audio(audio_path)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    except OSError as exc:
        return jsonify({"success": False, "error": f"Unable to prepare stored audio: {exc}"}), 500
    return _run_analysis_request(
        lambda: analyze_source(input_path, audio_path.name, chord_dict),
        cleanup=lambda: input_path.unlink(missing_ok=True),
    )

@app.get("/api/recordings")
def recordings_list():
    try:
        return jsonify({
            "success": True,
            "recordings": get_saved_recordings(),
        })
    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error),
        }), 500


@app.delete("/api/recordings/<int:recording_id>")
def recordings_delete(recording_id):
    try:
        remove_saved_recording(recording_id)
        return jsonify({
            "success": True,
            "message": "Recording deleted.",
        })
    except FileNotFoundError:
        return jsonify({
            "success": False,
            "error": "Recording not found.",
        }), 404
    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error),
        }), 500

@app.post("/api/library/title")
def rename_library_track():
    from library_titles import set_title, validate_title, upload_title_key
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(success=False, error="Expected a title request."), 400
    try:
        title = validate_title(payload.get("title"))
        if payload.get("kind") == "recordings":
            recording_id = payload.get("id")
            if type(recording_id) is not int or recording_id < 1:
                raise ValueError("Invalid recording ID.")
            title = rename_recording(recording_id, title)
        elif payload.get("kind") == "songs":
            from web_upload import _upload_commit_lock
            with _upload_commit_lock:
                path = resolve_upload_path(payload.get("filename"))
                title = set_title(upload_title_key(path), title)
        else:
            raise ValueError("Choose a song or recording.")
        return jsonify(success=True, title=title)
    except ValueError as error:
        return jsonify(success=False, error=str(error)), 400
    except FileNotFoundError as error:
        return jsonify(success=False, error=str(error)), 404
    except OSError:
        return jsonify(success=False, error="Could not save the title. Please try again."), 500


@app.post("/begin_recording")
def begin_recording():
    print("=== /begin_recording request received ===", flush=True)
    try:
        print("Attaching iod capture sink...", flush=True)
        start_recording_capture(iod.start_capture)
        return jsonify({
            "success": True,
            "message": "Recording started",
            "model_used": "chordsense-cnn-hef",
        })
    except RecordingLimitReached as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except IodError as e:
        print(f"Begin recording failed: {e}", flush=True)
        status = 409 if "stream" in str(e).lower() else 502
        return jsonify({"success": False, "error": str(e)}), status
    except Exception as e:
        print(f"Begin recording failed: {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 500


def analyze_recording(wav_path: Path, capture_duration: float) -> dict:
    output_lab_path = OUTPUTS_DIR / f"recording-{uuid.uuid4().hex}.lab"
    try:
        with analysis_execution_lock:
            if _remote_analysis_enabled():
                _, _, result = run_model(wav_path, output_lab_path, "submission")
                chords = result["chords"]
                duration, processing_time = result["duration"], result["processing_time"]
                model_used, model_name = result["model_used"], result["model_name"]
            else:
                # Do not pin the physical accelerator after a recording finishes.
                with closing(create_recording_recognizer()) as recognizer:
                    result = recognizer.analyze_file(wav_path)
                    if not recognizer.write_lab_file(result, output_lab_path):
                        raise RuntimeError("Chord recognition produced no output")
                chords = [
                    {"start": segment.start, "end": segment.end, "chord": segment.chord, "confidence": segment.confidence}
                    for segment in result.segments
                ]
                duration, processing_time = result.duration_seconds, result.processing_seconds
                model_used, model_name = "chordsense-cnn-hef", "ChordSenseCNN (Hailo)"
        saved_recording = save_recording_analysis(wav_path, chords, duration)
        return {
            "success": True, "chords": chords, "total_chords": len(chords),
            "duration": duration, "model_used": model_used, "model_name": model_name,
            "chord_dict": "submission", "processing_time": processing_time,
            "stdout": "", "stderr": "", "lab_file": str(output_lab_path),
            "wav_path": str(wav_path),
            "recording_name": saved_recording["name"],
        }
    except Exception as exc:
        output_lab_path.unlink(missing_ok=True)
        # Preserve the take, including when the remote worker is unavailable.
        return {"success": False, "error": str(exc), "wav_path": str(wav_path),
                "duration": capture_duration}


@app.post("/end_recording")
def end_recording():
    try:
        wav_path, capture_duration = iod.stop_capture()
    except IodError as exc:
        return jsonify({"success": False, "error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500
    return _run_analysis_request(
        lambda: analyze_recording(wav_path, capture_duration),
        overflow_result={"wav_path": str(wav_path), "duration": capture_duration},
    )


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
# -- playback: iod plays audio out of the I2S DAC to the pedal's headphone jack --

def _iod_call(action):
    try:
        return jsonify({"success": True, **(action() or {})})
    except (IodError, PlaybackSpeedError) as e:
        print(f"Playback command failed: {e}", flush=True)
        return jsonify({"success": False, "error": str(e)}), 502


def _resolve_playback_path(body: dict) -> Path:
    """An uploaded song by name (``upload``) or a local file (``path``)."""
    if body.get("upload"):
        return resolve_upload_path(body["upload"])
    elif body.get("path"):
        if not is_local_desktop_request():
            raise ValueError("Remote playback requires an uploaded filename")
        if not isinstance(body["path"], str):
            raise ValueError("path must be a string")
        path = Path(body["path"]).expanduser().resolve()
    else:
        raise ValueError("Provide 'path' or 'upload'")
    if path.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(f"Unsupported audio type: {path.suffix}")
    if not path.is_file():
        raise ValueError(f"Audio file not found: {path}")
    return path


@app.post("/playback/load")
def playback_load():
    try:
        path = _resolve_playback_path(request.get_json(silent=True) or {})
    except (ValueError, FileNotFoundError) as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return _iod_call(lambda: playback.load(path))


@app.post("/playback/play")
def playback_play():
    return _iod_call(playback.resume)


@app.post("/playback/pause")
def playback_pause():
    return _iod_call(playback.pause)


@app.post("/playback/stop")
def playback_stop():
    return _iod_call(playback.stop)


@app.post("/playback/seek")
def playback_seek():
    body = request.get_json(silent=True) or {}
    try:
        position = float(body["position_secs"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"success": False, "error": "position_secs is required"}), 400
    if not math.isfinite(position):
        return jsonify({"success": False, "error": "position_secs must be finite"}), 400
    return _iod_call(lambda: playback.seek(position))


@app.post("/playback/speed")
def playback_speed():
    body = request.get_json(silent=True) or {}
    try:
        return _iod_call(lambda: playback.set_speed(body.get("speed")))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.post("/playback/volume")
def playback_volume():
    body = request.get_json(silent=True) or {}
    try:
        volume = float(body["volume"])
        if not math.isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("invalid volume")
    except (KeyError, TypeError, ValueError):
        return jsonify({"success": False, "error": "volume must be finite and from 0 to 1"}), 400
    return _iod_call(lambda: iod.set_volume(volume))


@app.get("/playback/status")
def playback_status():
    return _iod_call(playback.status)


if __name__ == "__main__":
    app.run(host=os.environ.get("CHORDSENSE_API_HOST", "0.0.0.0"), port=5051, debug=False)
