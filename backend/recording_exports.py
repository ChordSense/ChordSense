"""Read-only downloads of original guitar captures for dataset collection."""

import csv
import io
import json
import tempfile
import wave
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, send_file

import recordings_library as library

recording_exports = Blueprint("recording_exports", __name__)


def _path(item):
    path = Path(item["wav_path"])
    if (path.is_symlink() or path.resolve().parent != library.CAPTURES_DIR.resolve()
            or not path.name.startswith("capture-") or path.suffix != ".wav"):
        raise FileNotFoundError("Recording not found.")
    return path


def _duration(path):
    # iod finalizes the WAV header on Stop. Never export an unfinished take.
    try:
        with wave.open(str(path), "rb") as audio:
            frames = audio.getnframes()
            if frames <= 0 or frames * audio.getnchannels() * audio.getsampwidth() > path.stat().st_size:
                return None
            return frames / audio.getframerate()
    except (OSError, EOFError, wave.Error, ZeroDivisionError):
        return None


def public_recordings():
    result = []
    for item in library.list_recordings():
        try:
            path = _path(item)
        except FileNotFoundError:
            continue
        duration = _duration(path)
        result.append({
            "id": item["id"], "name": item["name"], "filename": path.name,
            "size": item["size"], "modified": item["modified"],
            "duration": duration, "ready": duration is not None,
            "download_url": f"/api/recordings/{item['id']}/audio",
        })
    return result


@recording_exports.get("/api/recordings/<int:recording_id>/audio")
def download_recording(recording_id):
    try:
        item = next((r for r in library.list_recordings() if r["id"] == recording_id), None)
        if item is None:
            raise FileNotFoundError()
        path = _path(item)
        if _duration(path) is None:
            return jsonify(success=False, error_code="recording_not_ready"), 409
        return send_file(path, mimetype="audio/wav", as_attachment=True, download_name=path.name)
    except FileNotFoundError:
        return jsonify(success=False, error_code="recording_not_found"), 404


@recording_exports.get("/api/recordings/export")
def export_recordings():
    items = public_recordings()
    if not items:
        return jsonify(success=False, error_code="recordings_empty"), 404
    if any(not item["ready"] for item in items):
        return jsonify(success=False, error_code="recording_not_ready"), 409
    # Spill to disk beyond 8 MiB instead of holding the whole dataset in Pi RAM.
    archive = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            for item in items:
                path = _path({"wav_path": library.CAPTURES_DIR / item["filename"]})
                bundle.write(path, arcname=item["filename"])
            labels = io.StringIO(newline="")
            writer = csv.writer(labels)
            writer.writerow(["file", "session", "label", "technique", "notes"])
            for item in items:
                writer.writerow([item["filename"], "", "", "", ""])
            bundle.writestr("labels.csv", labels.getvalue())
            # Display titles are reference metadata, never assumed ground truth.
            bundle.writestr("recordings.json", json.dumps(items, indent=2, ensure_ascii=False))
        size = archive.tell()
        archive.seek(0)
        response = send_file(archive, mimetype="application/zip", as_attachment=True,
                             download_name="chordsense-recordings.zip")
        response.content_length = size
        response.direct_passthrough = False
        response.call_on_close(archive.close)
        return response
    except FileNotFoundError:
        archive.close()
        return jsonify(success=False, error_code="recording_not_found"), 404
    except Exception:
        archive.close()
        raise
