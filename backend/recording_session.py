"""Live Record-mode orchestration for ``iod`` and streaming inference."""

from __future__ import annotations

import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from iod_client import IodAudioStream, IodClient
from models.chordsense_cnn.streaming import LivePrediction, StreamingChordRecognizer


@dataclass(frozen=True)
class RecordingSegment:
    start: float
    end: float
    chord: str
    confidence: float


@dataclass(frozen=True)
class RecordingResult:
    wav_path: Path
    lab_path: Path
    duration_seconds: float
    segments: list[RecordingSegment]


class HailoRecordingSession:
    """Save and classify one live ``iod`` stream in real time."""

    def __init__(
        self,
        iod: IodClient,
        recognizer: StreamingChordRecognizer,
        captures_dir: str | Path,
        output_lab_path: str | Path,
        frame_samples: int = 441,
    ):
        self.iod = iod
        self.recognizer = recognizer
        self.captures_dir = Path(captures_dir)
        self.output_lab_path = Path(output_lab_path)
        self.frame_samples = frame_samples
        self._lock = threading.Lock()
        self._stream: IodAudioStream | None = None
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()
        self._predictions: list[LivePrediction] = []
        self._sample_count = 0
        self._wav_path: Path | None = None
        self._worker_error: Exception | None = None

    def start(self) -> None:
        with self._lock:
            if self._worker is not None:
                raise RuntimeError("a recording is already in progress")

            self.recognizer.reset()
            self._predictions = []
            self._sample_count = 0
            self._worker_error = None
            self._stopping.clear()
            self.captures_dir.mkdir(parents=True, exist_ok=True)
            timestamp_ms = time.time_ns() // 1_000_000
            self._wav_path = self.captures_dir / f"capture-{timestamp_ms}.wav"
            stream = self.iod.start_stream(self.frame_samples)
            self._stream = stream
            self._worker = threading.Thread(
                target=self._consume_stream,
                args=(stream, self._wav_path),
                name="chordsense-hailo-recording",
                daemon=True,
            )
            self._worker.start()

    def _consume_stream(self, stream: IodAudioStream, wav_path: Path) -> None:
        try:
            with wave.open(str(wav_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(self.recognizer.preprocessing.sample_rate)
                for frame in stream:
                    output.writeframesraw(frame.pcm16)
                    samples = np.frombuffer(frame.pcm16, dtype="<i2")
                    predictions = self.recognizer.push_samples(samples)
                    with self._lock:
                        self._sample_count += len(samples)
                        self._predictions.extend(predictions)
        except Exception as exc:
            if not self._stopping.is_set():
                with self._lock:
                    self._worker_error = exc
        finally:
            stream.close()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            latest = self._predictions[-1] if self._predictions else None
            return {
                "active": self._worker is not None and self._worker.is_alive(),
                "audio_seconds": (
                    self._sample_count / self.recognizer.preprocessing.sample_rate
                ),
                "prediction_count": len(self._predictions),
                "latest_prediction": (
                    {
                        "timestamp_seconds": latest.timestamp_seconds,
                        "chord": latest.chord,
                        "confidence": latest.confidence,
                        "changed": latest.changed,
                    }
                    if latest is not None
                    else None
                ),
                "error": str(self._worker_error) if self._worker_error else None,
            }

    def stop(self) -> RecordingResult:
        with self._lock:
            stream = self._stream
            worker = self._worker
        if stream is None or worker is None:
            raise RuntimeError("no recording is in progress")

        self._stopping.set()
        stream.close()
        worker.join(timeout=5.0)
        if worker.is_alive():
            raise RuntimeError("timed out while stopping the live audio stream")

        with self._lock:
            predictions = list(self._predictions)
            sample_count = self._sample_count
            wav_path = self._wav_path
            worker_error = self._worker_error
            self._stream = None
            self._worker = None
            self._wav_path = None

        if worker_error is not None:
            raise RuntimeError(f"live chord inference failed: {worker_error}")
        if wav_path is None or sample_count == 0:
            raise RuntimeError("the live audio stream produced no samples")

        duration = sample_count / self.recognizer.preprocessing.sample_rate
        segments = self._build_segments(predictions, duration)
        self._write_lab(segments)
        return RecordingResult(
            wav_path=wav_path,
            lab_path=self.output_lab_path,
            duration_seconds=duration,
            segments=segments,
        )

    @staticmethod
    def _build_segments(
        predictions: list[LivePrediction],
        duration_seconds: float,
    ) -> list[RecordingSegment]:
        if not predictions:
            return [RecordingSegment(0.0, duration_seconds, "N", 0.0)]

        segments: list[RecordingSegment] = []
        start = 0.0
        chord = predictions[0].chord
        confidences: list[float] = []
        for prediction in predictions:
            if prediction.chord != chord:
                segments.append(
                    RecordingSegment(
                        start,
                        min(prediction.timestamp_seconds, duration_seconds),
                        chord,
                        float(np.mean(confidences)) if confidences else 0.0,
                    )
                )
                start = min(prediction.timestamp_seconds, duration_seconds)
                chord = prediction.chord
                confidences = []
            confidences.append(prediction.confidence)
        segments.append(
            RecordingSegment(
                start,
                duration_seconds,
                chord,
                float(np.mean(confidences)) if confidences else 0.0,
            )
        )
        return segments

    def _write_lab(self, segments: list[RecordingSegment]) -> None:
        self.output_lab_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_lab_path.open("w", encoding="utf-8") as output:
            for segment in segments:
                output.write(f"{segment.start}\t{segment.end}\t{segment.chord}\n")

    def close(self) -> None:
        with self._lock:
            stream = self._stream
            worker = self._worker
        if stream is not None:
            self._stopping.set()
            stream.close()
        if worker is not None:
            worker.join(timeout=5.0)
        self.recognizer.close()
