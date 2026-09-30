import base64
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from iod_client import IodClient, IodError, IodSampleFrame
from live_feedback_session import LiveFeedbackSession, load_live_recognizer
from models.chordsense_cnn.template import StreamingTemplateRecognizer


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


class FakeTemplateRecognizer(StreamingTemplateRecognizer):
    model_name = "chordsense-chroma-template-experimental"

    def __init__(self):
        self.samples = 0
        self.resets = 0
        self.closed = False

    def push_samples(self, samples):
        self.samples += len(samples)
        return [SimpleNamespace(
            sample_index=self.samples, chord="C", confidence=0.9, changed=False,
            raw_chord="C", accepted=True, score=0.8, concentration=0.7,
            margin=0.3, root_margin=0.4, quality_margin=0.2,
            quality_uncertain=False,
        )]

    def reset(self):
        self.samples = 0
        self.resets += 1

    def close(self):
        self.closed = True


class SessionTests(unittest.TestCase):
    @staticmethod
    def frame(index):
        # The Pi ADC has a substantial DC offset; real guitar input also has
        # varying AC energy. Keep this fixture above a DC-corrected RMS gate.
        pcm = (5000 + np.where(np.arange(441) % 2, 1000, -1000)) \
            .astype("<i2").tobytes()
        return IodSampleFrame(index, pcm, time.monotonic_ns())

    @staticmethod
    def dc_only_frame(index):
        pcm = np.full(441, -6500, dtype="<i2").tobytes()
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

    def test_dc_offset_alone_is_silence_and_ac_signal_recovers(self):
        # Template mode uses centered AC RMS and waits 1 s before resetting.
        frames = [self.dc_only_frame(index * 441) for index in range(51)]
        frames.append(self.frame(51 * 441))
        recognizer = FakeTemplateRecognizer()
        session = LiveFeedbackSession(
            iod=FakeIod([FakeStream(frames)]),
            recognizer_factory=lambda: recognizer,
        )
        started = session.start()
        events = []
        sequence = 0
        deadline = time.monotonic() + 2
        try:
            while time.monotonic() < deadline and not any(
                e.get("type") == "prediction" and e.get("input_quality") == "ok"
                for e in events
            ):
                event = session.wait_event(started["session_id"], sequence, timeout=0.1)
                if event:
                    sequence = event["sequence"]
                    events.append(event)
            silent = [e for e in events if e.get("input_quality") == "silence"]
            self.assertTrue(silent)
            self.assertTrue(all(e.get("chord") is None for e in silent))
            self.assertTrue(any(e.get("status") == "silence" for e in events))
            self.assertTrue(any(
                e.get("type") == "prediction" and e.get("input_quality") == "ok"
                and e.get("chord") == "C" for e in events
            ))
            self.assertTrue(all(e.get("model") == recognizer.model_name for e in events
                                if e.get("type") == "prediction"))
            recovered = next(e for e in events if e.get("type") == "prediction"
                             and e.get("input_quality") == "ok")
            self.assertEqual(recovered["confidence_kind"], "template_root_margin")
            self.assertEqual(recovered["quality_uncertain"], False)
            self.assertEqual(recovered["template_evidence"]["raw_chord"], "C")
            self.assertGreater(recovered["ac_rms"], 0.008)
            self.assertGreaterEqual(recognizer.resets, 2)
        finally:
            session.stop()

    def test_real_template_emits_an_e_chord_through_session(self):
        rate = 22_050
        times = np.arange(rate * 2, dtype=np.float32) / rate
        frequencies = (164.81, 207.65, 246.94)
        waveform = sum(np.sin(2 * np.pi * frequency * times) for frequency in frequencies)
        pcm = np.asarray(waveform / 3 * 12_000, dtype="<i2")
        frames = [
            IodSampleFrame(index, pcm[index:index + 441].tobytes(), time.monotonic_ns())
            for index in range(0, len(pcm), 441)
        ]
        session = LiveFeedbackSession(
            iod=FakeIod([FakeStream(frames)]),
            recognizer_factory=StreamingTemplateRecognizer,
        )
        started = session.start()
        sequence = 0
        prediction = None
        deadline = time.monotonic() + 3
        try:
            while prediction is None and time.monotonic() < deadline:
                event = session.wait_event(started["session_id"], sequence, timeout=0.1)
                if event:
                    sequence = event["sequence"]
                    if event.get("type") == "prediction" and event.get("chord") == "E":
                        prediction = event
            self.assertIsNotNone(prediction)
            self.assertEqual(prediction["sample_index"], 9216)
            self.assertEqual(prediction["model"], StreamingTemplateRecognizer.model_name)
            self.assertEqual(prediction["input_quality"], "ok")
            self.assertTrue(prediction["template_evidence"]["accepted"])
            self.assertFalse(prediction["quality_uncertain"])
        finally:
            session.stop()

    def test_shadow_evidence_is_aligned_and_restarts_after_stream_gap(self):
        frames = [self.frame(1000), self.frame(1441), self.frame(3000)]
        primary = FakeRecognizer()
        shadow = FakeTemplateRecognizer()
        session = LiveFeedbackSession(
            iod=FakeIod([FakeStream(frames)]),
            recognizer_factory=lambda: primary,
            shadow_factory=lambda: shadow,
        )
        started = session.start()
        events = []
        sequence = 0
        deadline = time.monotonic() + 2
        try:
            while time.monotonic() < deadline and len([
                e for e in events if e.get("type") == "prediction"
            ]) < 3:
                event = session.wait_event(started["session_id"], sequence, timeout=0.1)
                if event:
                    sequence = event["sequence"]
                    events.append(event)
            predictions = [e for e in events if e.get("type") == "prediction"]
            self.assertEqual(len(predictions), 3)
            self.assertTrue(any(e.get("status") == "stream_gap" for e in events))
            self.assertEqual([e["sample_index"] for e in predictions], [1441, 1882, 3441])
            self.assertTrue(all(e["model"] == "chordsense-causal-stft-cnn"
                                for e in predictions))
            self.assertTrue(all(e["dsp_experiment"]["sample_index"] == e["sample_index"]
                                for e in predictions))
            self.assertTrue(all(e["dsp_experiment"]["signal_ok"] is True
                                for e in predictions))
            self.assertTrue(all(e["dsp_experiment"]["raw_chord"] == "C"
                                for e in predictions))
            self.assertGreaterEqual(shadow.resets, 1)
        finally:
            session.stop()
        self.assertTrue(primary.closed)
        self.assertTrue(shadow.closed)

    def test_shadow_rejects_dc_only_input_without_changing_primary_event(self):
        primary = FakeRecognizer()
        shadow = FakeTemplateRecognizer()
        session = LiveFeedbackSession(
            iod=FakeIod([FakeStream([self.dc_only_frame(0)])]),
            recognizer_factory=lambda: primary,
            shadow_factory=lambda: shadow,
        )
        started = session.start()
        sequence = 0
        prediction = None
        deadline = time.monotonic() + 2
        try:
            while prediction is None and time.monotonic() < deadline:
                event = session.wait_event(started["session_id"], sequence, timeout=0.1)
                if event:
                    sequence = event["sequence"]
                    if event.get("type") == "prediction":
                        prediction = event
            self.assertIsNotNone(prediction)
            self.assertEqual(prediction["dsp_experiment"]["signal_ok"], False)
            self.assertIsNone(prediction["dsp_experiment"]["chord"])
            self.assertEqual(prediction["dsp_experiment"]["raw_chord"], "C")
            self.assertEqual(prediction["dsp_experiment"]["sample_index"],
                             prediction["sample_index"])
        finally:
            session.stop()


class RecognizerSelectionTests(unittest.TestCase):
    def test_shadow_flag_enables_template_only_for_cnn_primary(self):
        with patch.dict(os.environ, {
            "CHORDSENSE_LIVE_RECOGNIZER": "cnn",
            "CHORDSENSE_DSP_SHADOW": "1",
        }, clear=True):
            session = LiveFeedbackSession(recognizer_factory=FakeRecognizer)
            self.assertIs(session.shadow_factory, StreamingTemplateRecognizer)
        with patch.dict(os.environ, {
            "CHORDSENSE_LIVE_RECOGNIZER": "template",
            "CHORDSENSE_DSP_SHADOW": "1",
        }, clear=True):
            session = LiveFeedbackSession(recognizer_factory=FakeTemplateRecognizer)
            self.assertIsNone(session.shadow_factory)
        with patch.dict(os.environ, {"CHORDSENSE_DSP_SHADOW": "1"}, clear=True):
            session = LiveFeedbackSession(recognizer_factory=FakeTemplateRecognizer)
            self.assertIsNone(session.shadow_factory)

    def test_default_and_explicit_template_modes_do_not_require_hef_or_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "does-not-exist")
            for mode in (None, "template"):
                with self.subTest(mode=mode), patch.dict(os.environ, {
                    "CHORDSENSE_LIVE_HEF": missing,
                    "CHORDSENSE_LIVE_MANIFEST": missing,
                }, clear=True):
                    if mode is not None:
                        os.environ["CHORDSENSE_LIVE_RECOGNIZER"] = mode
                    recognizer = load_live_recognizer()
                    try:
                        self.assertIsInstance(recognizer, StreamingTemplateRecognizer)
                    finally:
                        recognizer.close()

    def test_explicit_cnn_mode_still_requires_verified_hef(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "does-not-exist")
            with patch.dict(os.environ, {
                "CHORDSENSE_LIVE_RECOGNIZER": "cnn",
                "CHORDSENSE_LIVE_HEF": missing,
                "CHORDSENSE_LIVE_MANIFEST": missing,
            }, clear=True):
                with self.assertRaisesRegex(RuntimeError, "HEF and manifest"):
                    load_live_recognizer()

            hef = Path(directory) / "candidate.hef"
            manifest = Path(directory) / "candidate.json"
            hef.write_bytes(b"candidate")
            manifest.write_text(json.dumps({"pi_verified": False}), encoding="utf-8")
            with patch.dict(os.environ, {
                "CHORDSENSE_LIVE_RECOGNIZER": "cnn",
                "CHORDSENSE_LIVE_HEF": str(hef),
                "CHORDSENSE_LIVE_MANIFEST": str(manifest),
            }, clear=True):
                with self.assertRaisesRegex(ValueError, "Pi Torch/Hailo verification"):
                    load_live_recognizer()

    def test_unknown_recognizer_mode_is_rejected(self):
        with patch.dict(os.environ, {"CHORDSENSE_LIVE_RECOGNIZER": "typo"}):
            with self.assertRaises(ValueError):
                load_live_recognizer()


if __name__ == "__main__":
    unittest.main()
