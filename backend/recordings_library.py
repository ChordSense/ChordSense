"""Persistent catalog of WAV files recorded by the ChordSense iod daemon."""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent.parent / "runtime"
CAPTURES_DIR = Path(os.environ.get(
    "CHORDSENSE_CAPTURES_DIR", str(RUNTIME_DIR / "captures")
)).resolve()
INDEX_FILE = RUNTIME_DIR / "recordings-index.json"
_LOCK = threading.Lock()
MAX_RECORDINGS = 20


class RecordingLimitReached(RuntimeError):
    """The user must explicitly delete a recording before making another."""


def start_recording_capture(start_capture):
    """Check existing WAVs before starting; never evict an old recording."""
    with _LOCK:
        index = _read()
        if _sync(index):
            _write(index)
        if len(index["recordings"]) >= MAX_RECORDINGS:
            raise RecordingLimitReached(
                "Recording library full (20 recordings). "
                "Delete a recording in Browse Tracks > Recordings before recording again."
            )
        # Serialize the check and capture start. iod rejects a second active capture.
        start_capture()


def _read():
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {"next_id": 1, "recordings": []}


def _write(index):
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = INDEX_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, indent=2), encoding="utf-8")
    temporary.replace(INDEX_FILE)


def _sync(index):
    """Discover previously recorded WAVs without renaming or copying them."""
    before = len(index["recordings"])
    index["recordings"] = [
        item for item in index["recordings"]
        if Path(item["wav_path"]).is_file()
        and Path(item["wav_path"]).resolve().parent == CAPTURES_DIR
    ]
    changed = before != len(index["recordings"])
    known = {item["wav_path"] for item in index["recordings"]}
    if CAPTURES_DIR.exists():
        paths = sorted(
            CAPTURES_DIR.glob("capture-*.wav"),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
        for file in paths:
            if not file.is_file() or file.is_symlink():
                continue
            full_path = str(file.resolve())
            if full_path not in known:
                number = index["next_id"]
                index["next_id"] += 1
                index["recordings"].append({
                    "id": number,
                    "wav_path": full_path,
                    "duration": 0,
                    "chords": [],
                })
                known.add(full_path)
                changed = True
    return changed


def _public(item):
    path = Path(item["wav_path"])
    stat = path.stat()
    return {
        "id": item["id"],
        "name": f"Recording #{item['id']}",
        "wav_path": str(path),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(
            stat.st_mtime, timezone.utc
        ).isoformat(),
        "duration": item.get("duration", 0),
        "chords": item.get("chords", []),
    }


def list_recordings():
    with _LOCK:
        index = _read()
        if _sync(index):
            _write(index)
        return [
            _public(item)
            for item in sorted(index["recordings"], key=lambda r: r["id"], reverse=True)
        ]


def save_recording_analysis(wav_path, chords, duration):
    """Store chord results; if analysis fails, WAV stays available without chords."""
    with _LOCK:
        index = _read()
        _sync(index)
        path = str(Path(wav_path).resolve())
        item = next((r for r in index["recordings"] if r["wav_path"] == path), None)
        if item is None:
            raise FileNotFoundError(f"Recording not in ChordSense captures: {path}")
        item["chords"] = chords
        item["duration"] = duration
        _write(index)
        return _public(item)


def delete_recording(recording_id):
    with _LOCK:
        index = _read()
        _sync(index)
        item = next((r for r in index["recordings"] if r["id"] == recording_id), None)
        if item is None:
            raise FileNotFoundError("Recording not found")
        path = Path(item["wav_path"])
        if path.resolve().parent != CAPTURES_DIR or not path.name.startswith("capture-") or path.suffix != ".wav":
            raise ValueError("Invalid recording path")
        path.unlink()
        index["recordings"].remove(item)
        _write(index)
