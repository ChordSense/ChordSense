"""Persistent display titles; audio filenames and analysis references stay stable."""
import json
import threading
import unicodedata
from pathlib import Path

TITLES_FILE = Path(__file__).resolve().parent.parent / "runtime" / "library-titles.json"
_LOCK = threading.Lock()
MAX_TITLE_LENGTH = 80


def validate_title(title):
    if not isinstance(title, str):
        raise ValueError("Enter a title.")
    title = title.strip()
    if not title or len(title) > MAX_TITLE_LENGTH:
        raise ValueError("Use a title between 1 and 80 characters.")
    if any(unicodedata.category(char).startswith("C") for char in title):
        raise ValueError("Titles cannot contain control characters.")
    return title


def _read():
    if not TITLES_FILE.exists():
        return {}
    return json.loads(TITLES_FILE.read_text(encoding="utf-8"))


def get_title(key, fallback):
    with _LOCK:
        return _read().get(key, fallback)


def upload_title_key(path):
    # A later upload with the same filename must not inherit the deleted song's title.
    return f"upload:{path.name}:{path.stat().st_mtime_ns}"


def set_title(key, title):
    title = validate_title(title)
    with _LOCK:
        titles = _read()
        titles[key] = title
        TITLES_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = TITLES_FILE.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(titles, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(TITLES_FILE)
    return title
