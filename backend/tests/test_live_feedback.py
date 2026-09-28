import base64
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from iod_client import IodClient, IodError, IodSampleFrame
from live_feedback_session import LiveFeedbackSession


class IodStreamTests(unittest.TestCase):
    def test_stream_ack_and_pcm_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "iod.sock")
            ready = threading.Event()
            requests = []

            def server():
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(path)
                    listener.listen(1)
                    ready.set()
                    connection, _ = listener.accept()
                    with connection:
                        requests.append(json.loads(connection.recv(4096).split(b"\n")[0]))
                        frame = {
                            "sample_index": 123,
                            "captured_at_ns": 456,
                            "samples": base64.b64encode(b"\x01\x00\xff\x7f").decode(),
                        }
                        connection.sendall(
                            (json.dumps({"ok": True}) + "\n" + json.dumps(frame) + "\n")
                            .encode()
                        )

            worker = threading.Thread(target=server)
            worker.start()
            self.assertTrue(ready.wait(2))
            with IodClient(socket_path=path).open_stream(frame_samples=2) as stream:
                frame = stream.read_frame()
            worker.join(2)
            self.assertEqual(requests, [{"cmd": "start_stream", "frame_samples": 2}])
            self.assertEqual(frame.sample_index, 123)
            self.assertEqual(frame.sample_count, 2)
            self.assertEqual(frame.captured_at_ns, 456)

    def test_rejected_stream_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "iod.sock")
            ready = threading.Event()

            def server():
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(path)
                    listener.listen(1)
                    ready.set()
                    connection, _ = listener.accept()
                    with connection:
                        connection.recv(4096)
                        connection.sendall(b'{"ok":false,"error":"cannot stream while capturing"}\n')

            worker = threading.Thread(target=server)
            worker.start()
            self.assertTrue(ready.wait(2))
            with self.assertRaisesRegex(IodError, "capturing"):
                IodClient(socket_path=path).open_stream()
            worker.join(2)


class FakeStream:
    def __init__(self, frames):
        self.frames = list(frames)
        self.closed = False

    def read_frame(self):
        if self.frames:
            return self.frames.pop(0)
        time.sleep(0.01)
        raise TimeoutError()

    def close(self):
        self.closed = True


class FailingStream(FakeStream):
    def read_frame(self):
        raise IodError("stream disconnected")


class FakeIod:
    def __init__(self, streams):
        self.streams = list(streams)

    def open_stream(self, frame_samples):
        return self.streams.pop(0)


class FakeRecognizer:
    def __init__(self):
        self.samples = 0
        self.resets = 0
        self.closed = False

    def push_samples(self, samples):
        self.samples += len(samples)
        return [SimpleNamespace(
            sample_index=self.samples, chord="C", confidence=0.9, changed=False,
        )]

    def reset(self):
        self.samples = 0
        self.resets += 1

    def close(self):
        self.closed = True


class SessionTests(unittest.TestCase):
    @staticmethod
    def frame(index):
        pcm = np.full(441, 5000, dtype="<i2").tobytes()
        return IodSampleFrame(index, pcm, time.monotonic_ns())

    def test_start_gap_pause_resume_and_stop(self):
        first = FakeStream([self.frame(1000), self.frame(1441), self.frame(3000)])
        second = FakeStream([self.frame(5000)])
        recognizer = FakeRecognizer()
        session = LiveFeedbackSession(
            iod=FakeIod([first, second]),
            recognizer_factory=lambda: recognizer,
        )
        started = session.start()
        self.assertEqual(started["state"], "running")
        session_id = started["session_id"]
        events = []
        after = 0
        until = time.monotonic() + 2
        while time.monotonic() < until and not any(
            e.get("status") == "stream_gap" for e in events
        ):
            event = session.wait_event(session_id, after, timeout=0.1)
            if event:
                events.append(event)
                after = event["sequence"]
        self.assertTrue(any(e.get("status") == "stream_gap" for e in events))
        self.assertTrue(any(e.get("type") == "prediction" for e in events))
        self.assertGreaterEqual(recognizer.resets, 1)
        self.assertEqual(session.pause()["state"], "paused")
        self.assertTrue(first.closed)
        resumed = session.resume()
        self.assertEqual(resumed["generation"], 1)
        self.assertEqual(session.stop()["state"], "idle")
        self.assertTrue(second.closed)
        self.assertTrue(recognizer.closed)

    def test_worker_failure_releases_model_and_stream(self):
        stream = FailingStream([])
        recognizer = FakeRecognizer()
        session = LiveFeedbackSession(
            iod=FakeIod([stream]),
            recognizer_factory=lambda: recognizer,
        )
        session.start()
        deadline = time.monotonic() + 2
        while (session.status()["state"] != "error" or not recognizer.closed) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(session.status()["state"], "error")
        self.assertTrue(stream.closed)
        self.assertTrue(recognizer.closed)
        self.assertEqual(session.stop()["state"], "idle")


if __name__ == "__main__":
    unittest.main()
