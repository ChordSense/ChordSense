from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR.parent / "runtime"
UPLOADS_DIR = RUNTIME_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

web_upload = Blueprint(
    "web_upload",
    __name__,
    template_folder="templates",
    static_folder="static",
)


def _is_allowed_audio(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_AUDIO_EXTENSIONS


def _unique_filename(original_name: str) -> str:
    safe_name = secure_filename(original_name) or "audio.wav"
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix.lower()

    candidate = f"{stem}{suffix}"
    if not (UPLOADS_DIR / candidate).exists():
        return candidate

    return f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"


def _serialize_audio_file(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "download_url": f"/api/uploads/{path.name}",
    }


@web_upload.get("/upload")
def upload_page():
    return render_template("upload.html")


@web_upload.get("/api/uploads")
def list_uploads():
    files = [
        _serialize_audio_file(path)
        for path in UPLOADS_DIR.iterdir()
        if path.is_file() and _is_allowed_audio(path.name)
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

    stored_name = _unique_filename(audio_file.filename)
    destination = UPLOADS_DIR / stored_name
    audio_file.save(destination)

    return jsonify({
        "success": True,
        "message": "Audio uploaded to ChordSense.",
        "file": _serialize_audio_file(destination),
    }), 201


@web_upload.get("/api/uploads/<path:filename>")
def download_audio(filename: str):
    safe_name = secure_filename(filename)
    if not safe_name or safe_name != filename:
        return jsonify({"success": False, "error": "Invalid filename."}), 400

    path = UPLOADS_DIR / safe_name
    if not path.is_file() or not _is_allowed_audio(path.name):
        return jsonify({"success": False, "error": "Audio file not found."}), 404

    return send_from_directory(UPLOADS_DIR, safe_name, as_attachment=False)


@web_upload.delete("/api/uploads/<path:filename>")
def delete_audio(filename: str):
    safe_name = secure_filename(filename)
    if not safe_name or safe_name != filename:
        return jsonify({"success": False, "error": "Invalid filename."}), 400

    path = UPLOADS_DIR / safe_name
    if not path.is_file() or not _is_allowed_audio(path.name):
        return jsonify({"success": False, "error": "Audio file not found."}), 404

    path.unlink()
    return jsonify({"success": True, "message": f"Deleted {safe_name}."})
