import unittest
import tempfile
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
             patch.object(backend_app, "create_recording_recognizer", return_value=recognizer), \
             patch.object(backend_app, "save_recording_analysis", return_value={"name": "Recording #1"}) as save_recording:
            response = self.client.post("/end_recording")

        self.assertEqual(response.status_code, 200)
        recognizer.close.assert_called_once_with()
        save_recording.assert_called_once_with(self.wav_path, [], 1.0)

    def test_recording_releases_hailo_after_analysis_failure(self):
        recognizer = Mock()
        recognizer.analyze_file.side_effect = RuntimeError("inference failed")
        with patch.object(backend_app.iod, "stop_capture", return_value=(self.wav_path, 1.0)), \
             patch.object(backend_app, "create_recording_recognizer", return_value=recognizer):
            response = self.client.post("/end_recording")

        self.assertEqual(response.status_code, 500)
        recognizer.close.assert_called_once_with()

    def test_remote_analysis_preserves_saved_recording_catalog_contract(self):
        chords = [{"start": 0.0, "end": 1.0, "chord": "C", "confidence": 1.0}]
        result = {"chords": chords, "duration": 1.0, "processing_time": 0.1,
                  "model_used": "chord-cnn-lstm", "model_name": "Chord-CNN-LSTM"}
        with tempfile.TemporaryDirectory() as output, \
             patch.dict(backend_app.os.environ, {"CHORDSENSE_ANALYSIS_URL": "https://worker.example/analyze"}), \
             patch.object(backend_app, "OUTPUTS_DIR", Path(output)), \
             patch.object(backend_app.iod, "stop_capture", return_value=(self.wav_path, 1.0)), \
             patch("remote_analysis.analyze_remotely", return_value=result), \
             patch.object(backend_app, "create_recording_recognizer") as local, \
             patch.object(backend_app, "save_recording_analysis", return_value={"name": "Recording #2"}) as save:
            response = self.client.post("/end_recording")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["recording_name"], "Recording #2")
        self.assertEqual(response.get_json()["wav_path"], str(self.wav_path))
        save.assert_called_once_with(self.wav_path, chords, 1.0)
        local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
