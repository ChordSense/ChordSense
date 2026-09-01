import struct
import threading
import time

import numpy as np
import serial
import serial.tools.list_ports

from models.chordsense_cnn.audio_processing import (
    AudioBuffer,
    DEFAULT_PREPROCESSING_CONFIG,
)

SAMPLE_RATE = DEFAULT_PREPROCESSING_CONFIG.sample_rate
FRAME_SIZE = 2048
BAUD_RATE = 115200
MARKER_BYTES = struct.pack("<I", 0xDEADBEEF)


def find_esp_port() -> str:
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if "USB Serial" in port.description:
            print(f"Found ESP on {port.device}: {port.description}")
            return port.device

    print("Could not auto-detect ESP port. Available ports:")
    for index, port in enumerate(ports):
        print(f"  [{index}] {port.device} — {port.description}")
    return ports[int(input("Enter port number: "))].device


def find_marker(connection: serial.Serial) -> bool:
    buffer = b""
    while True:
        byte = connection.read(1)
        if not byte:
            return False
        buffer = (buffer + byte)[-4:]
        if buffer == MARKER_BYTES:
            return True


def read_frame(connection: serial.Serial) -> np.ndarray | None:
    if not find_marker(connection):
        return None
    raw = connection.read(FRAME_SIZE * 2)
    if len(raw) != FRAME_SIZE * 2:
        return None
    samples = np.frombuffer(raw, dtype=np.uint16).astype(np.float32)
    return (samples - 2048.0) / 2048.0


class Worker:
    def __init__(self, port: str | None = None):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples: list[np.ndarray] = []
        self._port = port
        self._connection: serial.Serial | None = None

    def _run(self) -> None:
        self._connection.reset_input_buffer()
        while not self._stop_event.is_set():
            frame = read_frame(self._connection)
            if frame is not None:
                self._samples.append(frame)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Recording is already active")
        if self._port is None:
            self._port = find_esp_port()

        self._connection = serial.Serial(self._port, BAUD_RATE, timeout=2)
        time.sleep(1.5)
        self._stop_event.clear()
        self._samples = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"Recording started on {self._port}...")

    def stop(self) -> AudioBuffer | None:
        if self._thread is None:
            return None

        self._stop_event.set()
        self._thread.join()
        self._thread = None

        if self._connection is not None:
            self._connection.close()
            self._connection = None

        if not self._samples:
            print("No frames captured.")
            return None

        samples = np.concatenate(self._samples)
        audio = AudioBuffer(samples, SAMPLE_RATE)
        print(
            f"Recorded {len(self._samples)} frames, {len(samples)} samples "
            f"({audio.duration_seconds:.2f}s)"
        )
        return audio


if __name__ == "__main__":
    worker = Worker()
    worker.start()
    input("Press Enter to stop recording...\n")
    recording = worker.stop()
    if recording is not None:
        print(f"Audio duration: {recording.duration_seconds:.2f}s")
