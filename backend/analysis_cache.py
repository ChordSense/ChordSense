from __future__ import annotations

import hashlib
import json
import shutil

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

RUNTIME_DIR = BASE_DIR.parent / "runtime"

CACHE_DIR = (
    RUNTIME_DIR /
    "analysis_cache"
)

CACHE_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# IMPORTANT:
#
# Change this whenever the model or preprocessing
# changes enough that old analyses should no longer
# be reused.
CACHE_VERSION = "offline-cnn-lstm-v1"


@dataclass(frozen=True)
class CacheEntry:
    cache_key: str
    lab_path: Path
    metadata: dict


def _sha256_file(
    path: Path
) -> str:

    hasher = hashlib.sha256()

    with path.open("rb") as file:

        while True:

            chunk = file.read(
                1024 * 1024
            )

            if not chunk:
                break

            hasher.update(chunk)

    return hasher.hexdigest()


def identify_audio(
    audio_path: Path,
    chord_dict: str
) -> tuple[str, str]:

    audio_sha256 = _sha256_file(
        audio_path
    )

    # Cache identity depends on:
    # 1. actual audio contents
    # 2. chord dictionary
    # 3. model/cache version
    identity = (
        f"{CACHE_VERSION}\0"
        f"{chord_dict}\0"
        f"{audio_sha256}"
    )

    cache_key = hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()

    return (
        cache_key,
        audio_sha256
    )


def load_cache_entry(
    cache_key: str
) -> CacheEntry | None:

    entry_dir = (
        CACHE_DIR /
        cache_key
    )

    lab_path = (
        entry_dir /
        "chords.lab"
    )

    metadata_path = (
        entry_dir /
        "metadata.json"
    )

    if (
        not lab_path.is_file() or
        not metadata_path.is_file()
    ):
        return None

    try:

        with metadata_path.open(
            "r",
            encoding="utf-8"
        ) as file:

            metadata = json.load(
                file
            )

    except (
        OSError,
        json.JSONDecodeError
    ):
        return None

    if (
        metadata.get(
            "cache_version"
        ) != CACHE_VERSION
    ):
        return None

    if (
        metadata.get(
            "cache_key"
        ) != cache_key
    ):
        return None

    return CacheEntry(
        cache_key=cache_key,
        lab_path=lab_path,
        metadata=metadata
    )


def save_cache_entry(
    *,
    cache_key: str,
    audio_sha256: str,
    original_name: str,
    chord_dict: str,
    source_lab_path: Path,
    duration: float,
    total_chords: int,
    model_used: str,
    model_name: str
) -> CacheEntry:

    entry_dir = (
        CACHE_DIR /
        cache_key
    )

    entry_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    lab_path = (
        entry_dir /
        "chords.lab"
    )

    temp_lab_path = (
        entry_dir /
        "chords.lab.tmp"
    )

    shutil.copy2(
        source_lab_path,
        temp_lab_path
    )

    temp_lab_path.replace(
        lab_path
    )


    metadata = {
        "cache_version":
            CACHE_VERSION,

        "cache_key":
            cache_key,

        "audio_sha256":
            audio_sha256,

        "original_name":
            original_name,

        "chord_dict":
            chord_dict,

        "duration":
            duration,

        "total_chords":
            total_chords,

        "model_used":
            model_used,

        "model_name":
            model_name,

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


    metadata_path = (
        entry_dir /
        "metadata.json"
    )

    temp_metadata_path = (
        entry_dir /
        "metadata.json.tmp"
    )

    with temp_metadata_path.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            metadata,
            file,
            indent=2
        )

    temp_metadata_path.replace(
        metadata_path
    )


    return CacheEntry(
        cache_key=cache_key,
        lab_path=lab_path,
        metadata=metadata
    )