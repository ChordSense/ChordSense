import serial
import serial.tools.list_ports
import numpy as np
import librosa
import struct
import time
import threading

SAMPLE_RATE  = 22050
FFT_SIZE     = 2048
BAUD         = 115200
MARKER       = 0xDEADBEEF
MARKER_BYTES = struct.pack('<I', MARKER)

FMIN         = librosa.note_to_hz('E2')
N_BINS       = 12
BINS_PER_OCT = 12

def find_esp_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if any(x in p.description for x in ['USB Serial']):
            print(f"Found ESP on {p.device}: {p.description}")
            return p.device
    print("Could not auto-detect ESP port. Available ports:")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p.device} — {p.description}")
    return ports[0].device

def find_marker(ser):
    buf = b''
    while True:
        byte = ser.read(1)
        if not byte:
            return False
        buf += byte
        if len(buf) > 4:
            buf = buf[-4:]
        if buf == MARKER_BYTES:
            return True

def read_frame(ser):
    if not find_marker(ser):
        return None
    raw = ser.read(FFT_SIZE * 2)
    if len(raw) != FFT_SIZE * 2:
        return None
    samples = np.frombuffer(raw, dtype=np.uint16).astype(np.float32)
    samples = (samples - 2048.0) / 2048.0
    return samples


class Worker:
    def __init__(self, port=None):
        self._stop_event = threading.Event()
        self._thread = None
        self._samples = []
        self._frame_count = 0
        self._port = port
        self._ser = None

    def _run(self):
        self._ser.reset_input_buffer()
        while not self._stop_event.is_set():
            frame = read_frame(self._ser)
            if frame is None:
                continue
            self._frame_count += 1
            self._samples.append(frame)

    def start(self):
        """Open serial and start recording. Returns immediately."""
        if self._port is None:
            self._port = find_esp_port()

        self._ser = serial.Serial(self._port, BAUD, timeout=2)
        time.sleep(1.5)

        self._stop_event.clear()
        self._samples = []
        self._frame_count = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"Recording started on {self._port}...")

    def stop(self):
        """Stop recording, close serial, return (chroma_cqt, harmonic)."""
        self._stop_event.set()
        self._thread.join()

        if self._ser:
            self._ser.close()
            self._ser = None

        if self._frame_count == 0:
            print("No frames captured.")
            return None, None

        signal = np.concatenate(self._samples)
        print(f"Recorded {self._frame_count} frames, {len(signal)} samples ({len(signal)/SAMPLE_RATE:.2f}s)")

        harmonic = librosa.effects.harmonic(signal)

        chroma = np.abs(librosa.feature.chroma_cqt(
            y=signal,
            sr=SAMPLE_RATE,
            hop_length=512,
            fmin=FMIN,
            n_chroma=N_BINS,
            bins_per_octave=BINS_PER_OCT
        ))

        return chroma, harmonic


if __name__ == '__main__':
    worker = Worker()
    worker.start()
    input("Press Enter to stop recording...\n")
    chroma, harmonic = worker.stop()
    if chroma is not None:
        print(f"Chroma shape: {chroma.shape}")
        print(f"Harmonic signal length: {len(harmonic)}")
