from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from analysis_worker import create_analysis_worker


class AnalysisWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

        def infer(_source, destination, _dictionary):
            destination.write_text("0.0 1.0 C:maj\n", encoding="utf-8")
            return "", ""

        self.runner = Mock(side_effect=infer)
        self.app = create_analysis_worker(runtime_dir=Path(self.directory.name), runner=self.runner, token="secret")
        self.headers = {"Authorization": "Bearer secret"}
        self.client = self.app.test_client()

    def upload(self, **extra):
        return {"file": (BytesIO(b"test-audio"), "song.wav"), **extra}

    def test_requires_auth_before_accepting_audio(self):
        response = self.client.post("/analyze", data=self.upload())
        self.assertEqual(response.status_code, 401)
        self.runner.assert_not_called()

    def test_unconfigured_worker_fails_closed(self):
        app = create_analysis_worker(runtime_dir=Path(self.directory.name), token="")
        self.assertEqual(app.test_client().get("/health").status_code, 503)

    def test_analyzes_once_then_reuses_cache_and_removes_temporary_audio(self):
        first = self.client.post("/analyze", data=self.upload(), headers=self.headers)
        second = self.client.post("/analyze", data=self.upload(), headers=self.headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["duration"], 1)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json["cached"])
        self.runner.assert_called_once()
        self.assertEqual(list((Path(self.directory.name) / "inputs").iterdir()), [])

    def test_rejects_invalid_dictionary_before_inference(self):
        response = self.client.post("/analyze", data=self.upload(chord_dict="../../secret"), headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.runner.assert_not_called()

    def test_worker_limits_parallel_inference(self):
        started = threading.Event()
        finish = threading.Event()

        def blocking(_source, destination, _dictionary):
            started.set()
            if not finish.wait(3):
                raise RuntimeError("Test timeout")
            destination.write_text("0 1 C\n", encoding="utf-8")

        self.runner.side_effect = blocking
        results = []

        def first_request():
            with self.app.test_client() as client:
                results.append(client.post("/analyze", data=self.upload(), headers=self.headers).status_code)

        thread = threading.Thread(target=first_request)
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            response = self.client.post("/analyze", data=self.upload(), headers=self.headers)
            self.assertEqual(response.status_code, 503)
        finally:
            finish.set()
            thread.join(3)
        self.assertEqual(results, [200])


if __name__ == "__main__":
    unittest.main()
