from __future__ import annotations

import uuid
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename
from library_titles import get_title, upload_title_key

ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}
MAX_UPLOADS = 10

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR.parent / "runtime"
UPLOADS_DIR = RUNTIME_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

web_upload = Blueprint("web_upload", __name__)
_upload_commit_lock = threading.Lock()


def _is_allowed_audio(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_AUDIO_EXTENSIONS


def _uploads_full():
    return sum(
        1 for path in UPLOADS_DIR.iterdir()
        if path.is_file() and not path.is_symlink() and _is_allowed_audio(path.name)
    ) >= MAX_UPLOADS


def _upload_limit_response():
    return jsonify(
        success=False,
        error="Song library full (10 songs). Delete an uploaded song before uploading another.",
        error_code="upload_limit_reached",
    ), 409


def _unique_filename(original_name: str) -> str:
    safe_name = secure_filename(original_name) or "audio.wav"
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix.lower()

    candidate = f"{stem}{suffix}"
    if not (UPLOADS_DIR / candidate).exists():
        return candidate
    return f"{stem}-{uuid.uuid4().hex[:12]}{suffix}"


def resolve_upload_path(filename: str) -> Path:
    """Resolve only a single library filename, never a caller-provided path."""
    if not isinstance(filename, str) or not filename or secure_filename(filename) != filename:
        raise ValueError("Invalid filename.")
    path = UPLOADS_DIR / filename
    if path.resolve().parent != UPLOADS_DIR.resolve():
        raise ValueError("Invalid filename.")
    if not path.is_file() or not _is_allowed_audio(path.name):
        raise FileNotFoundError("Audio file not found.")
    return path


def _serialize_audio_file(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "title": get_title(upload_title_key(path), path.name),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "download_url": f"/api/uploads/{path.name}",
    }


@web_upload.get("/api/uploads")
def list_uploads():
    files = [
        _serialize_audio_file(path)
        for path in UPLOADS_DIR.iterdir()
        if path.is_file() and not path.is_symlink() and _is_allowed_audio(path.name)
    ]
    files.sort(key=lambda item: item["modified"], reverse=True)
    return jsonify({"success": True, "files": files})


@web_upload.post("/api/uploads")
def upload_audio():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded."}), 400

    audio_file = request.files["file"]
    if not audio_file.filename:
        return jsonify({"success": False, "error": "The uploaded file has no filename."}), 400

    if not _is_allowed_audio(audio_file.filename):
        return jsonify({
            "success": False,
            "error": "Unsupported audio type. Allowed: WAV, MP3, OGG, FLAC, M4A.",
        }), 400

    with _upload_commit_lock:
        if _uploads_full():
            return _upload_limit_response()

    with tempfile.NamedTemporaryFile(dir=UPLOADS_DIR, suffix=".upload", delete=False) as temp:
        temporary = Path(temp.name)
    try:
        audio_file.save(temporary)
        # Commit complete files atomically and prevent simultaneous uploads
        # with the same name from replacing each other's audio.
        with _upload_commit_lock:
            # Recheck after saving: another upload may have taken the last slot.
            if _uploads_full():
                return _upload_limit_response()
            stored_name = _unique_filename(audio_file.filename)
            destination = UPLOADS_DIR / stored_name
            temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    return jsonify({
        "success": True,
        "message": "Audio uploaded to ChordSense.",
        "file": _serialize_audio_file(destination),
    }), 201


@web_upload.get("/api/uploads/<path:filename>")
def download_audio(filename: str):
    try:
        path = resolve_upload_path(filename)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid filename."}), 400
    except FileNotFoundError:
        return jsonify({"success": False, "error": "Audio file not found."}), 404

    return send_from_directory(UPLOADS_DIR, path.name, as_attachment=False)


@web_upload.delete("/api/uploads/<path:filename>")
def delete_audio(filename: str):
    try:
        path = resolve_upload_path(filename)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid filename."}), 400
    except FileNotFoundError:
        return jsonify({"success": False, "error": "Audio file not found."}), 404

    path.unlink()
    return jsonify({"success": True, "message": f"Deleted {path.name}."})
