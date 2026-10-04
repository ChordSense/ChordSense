"""Pitch-preserving playback speed for the I2S audio player.

The chord chart and feedback use positions in the original recording. FFmpeg's
``atempo`` filter produces a stretched backing track, while this controller
translates iod's stretched-file positions back into original song seconds.
Variants are prepared before swapping the player, so playback, seeking and
status polling can continue while FFmpeg is working.
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import tempfile
import threading
from pathlib import Path

from iod_client import IodError


MIN_SPEED = 0.5
MAX_SPEED = 1.0
DEFAULT_SPEED = 1.0
MAX_CACHE_BYTES = 384 * 1024 * 1024


class PlaybackSpeedError(RuntimeError):
    """A speed variant could not be created or playback could not be switched."""


def validate_speed(value: object) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("speed must be a number from 0.5 to 1.0") from exc
    if not math.isfinite(speed) or not MIN_SPEED <= speed <= MAX_SPEED:
        raise ValueError("speed must be a finite number from 0.5 to 1.0")
    return speed


def original_position(iod_position: float, speed: float, duration: float | None) -> float:
    """Convert a stretched track position to the chart's original timeline."""
    position = max(0.0, iod_position * speed)
    return min(position, duration) if duration is not None else position


class PlaybackSpeedController:
    def __init__(self, iod, cache_dir: Path):
        self.iod = iod
        self.cache_dir = Path(cache_dir)
        self._lock = threading.RLock()
        self._source_path: Path | None = None
        self._duration: float | None = None
        self._speed = DEFAULT_SPEED
        self._generation = 0

    def _variant_path(self, source: Path, speed: float) -> Path:
        stat = source.stat()
        identity = f"{source.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}\0{speed:.4f}"
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        return self.cache_dir / f"{digest}.mp3"

    def _prepare_variant(self, source: Path, speed: float) -> Path:
        if speed == DEFAULT_SPEED:
            return source
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            target = self._variant_path(source, speed)
            if target.is_file() and target.stat().st_size > 0:
                return target
        except OSError as exc:
            raise PlaybackSpeedError(f"Could not prepare slower audio: {exc}") from exc

        # Give FFmpeg a distinct temporary path, then expose the complete file
        # atomically. A failed conversion never replaces a usable cache entry.
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f"{target.stem}-", suffix=".mp3", dir=self.cache_dir, delete=False
            ) as temp:
                temporary = Path(temp.name)
        except OSError as exc:
            raise PlaybackSpeedError(f"Could not prepare slower audio: {exc}") from exc
        try:
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-map", "0:a:0", "-vn",
                    "-af", f"atempo={speed:.4f}",
                    "-c:a", "libmp3lame", "-q:a", "4", "-threads", "2",
                    str(temporary),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=300,
            )
            if temporary.stat().st_size == 0:
                raise PlaybackSpeedError("FFmpeg produced an empty audio track")
            temporary.replace(target)
            return target
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise PlaybackSpeedError(f"Could not prepare slower audio: {detail.strip()}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _prune_cache(self, keep: Path) -> None:
        """Bound disk use without removing the file iod may need to reload."""
        try:
            files = sorted(
                (p for p in self.cache_dir.glob("*.mp3") if p != keep),
                key=lambda p: p.stat().st_mtime,
            )
            total = keep.stat().st_size if keep.is_file() else 0
            total += sum(path.stat().st_size for path in files)
            for path in files:
                if total <= MAX_CACHE_BYTES:
                    break
                size = path.stat().st_size
                path.unlink(missing_ok=True)
                total -= size
        except OSError:
            # Cache cleanup is best effort and must not interrupt playback.
            pass

    def load(self, path: Path) -> dict:
        path = Path(path)
        with self._lock:
            duration = self.iod.load(path)
            self._generation += 1
            self._source_path = path
            self._duration = duration
            self._speed = DEFAULT_SPEED
            return {"path": str(path), "duration": duration, "speed": self._speed}

    def status(self) -> dict:
        with self._lock:
            state = self.iod.status()
            raw_position = float(state.get("position_secs") or 0.0)
            return {
                "playing": state.get("playing", False),
                "paused": state.get("paused", False),
                "finished": state.get("finished", False),
                "position": original_position(raw_position, self._speed, self._duration),
                "duration": self._duration if self._source_path else state.get("duration_secs"),
                "path": str(self._source_path) if self._source_path else state.get("path"),
                "output_device": state.get("output_device"),
                "speed": self._speed,
            }

    def set_speed(self, value: object) -> dict:
        speed = validate_speed(value)
        with self._lock:
            if self._source_path is None:
                raise PlaybackSpeedError("Load a song before changing playback speed")
            if speed == self._speed:
                return self.status()
            source = self._source_path
            self._generation += 1
            generation = self._generation

        # Keep the existing track running while a new tempo is rendered.
        target = self._prepare_variant(source, speed)

        with self._lock:
            if generation != self._generation:
                # A newer load or speed request superseded this conversion.
                return self.status()
            previous = self.iod.status()
            old_path = Path(previous["path"]) if previous.get("path") else source
            position = original_position(
                float(previous.get("position_secs") or 0.0), self._speed, self._duration
            )
            was_playing = bool(previous.get("playing"))
            old_speed = self._speed
            try:
                self.iod.load(target)
                self.iod.seek(position / speed)
                if was_playing:
                    self.iod.resume()
            except IodError as exc:
                # A failed swap should leave the old song ready at its old tempo.
                try:
                    self.iod.load(old_path)
                    self.iod.seek(position / old_speed)
                    if was_playing:
                        self.iod.resume()
                except IodError:
                    pass
                raise PlaybackSpeedError(f"Could not change playback speed: {exc}") from exc
            self._speed = speed
            self._prune_cache(target)
            return self.status()

    def seek(self, position: float) -> None:
        if not math.isfinite(position):
            raise ValueError("position_secs must be finite")
        with self._lock:
            position = max(0.0, position)
            if self._duration is not None:
                position = min(position, self._duration)
            self.iod.seek(position / self._speed)

    def resume(self) -> None:
        with self._lock:
            self.iod.resume()

    def pause(self) -> None:
        with self._lock:
            self.iod.pause()

    def stop(self) -> None:
        with self._lock:
            self.iod.stop_playback()
