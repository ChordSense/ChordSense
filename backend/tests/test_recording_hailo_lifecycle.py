import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app as backend_app


class RecordingHailoLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.client = backend_app.app.test_client()
        self.wav_path = Path("/tmp/chordsense-recording-test.wav")

    def test_recording_releases_hailo_after_success(self):
        result = SimpleNamespace(
            segments=[], duration_seconds=1.0, processing_seconds=0.1,
        )
        recognizer = Mock()
        recognizer.analyze_file.return_value = result
        recognizer.write_lab_file.return_value = True
        with patch.object(backend_app.iod, "stop_capture", return_value=(self.wav_path, 1.0)), \
             patch.object(backend_app, "create_recording_recognizer", return_value=recognizer):
            response = self.client.post("/end_recording")

        self.assertEqual(response.status_code, 200)
        recognizer.close.assert_called_once_with()

    def test_recording_releases_hailo_after_analysis_failure(self):
        recognizer = Mock()
        recognizer.analyze_file.side_effect = RuntimeError("inference failed")
        with patch.object(backend_app.iod, "stop_capture", return_value=(self.wav_path, 1.0)), \
             patch.object(backend_app, "create_recording_recognizer", return_value=recognizer):
            response = self.client.post("/end_recording")

        self.assertEqual(response.status_code, 500)
        recognizer.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
