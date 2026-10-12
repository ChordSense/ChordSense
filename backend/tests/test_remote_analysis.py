import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from remote_analysis import RemoteAnalysisError, analyze_remotely, validate_analysis


RESULT = {
    "success": True, "duration": 1.0,
    "chords": [{"start": 0, "end": 1, "chord": "C:maj", "confidence": 0.8}],
    "model_used": "test", "model_name": "Test model", "processing_time": 0.1,
}


class RemoteAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "audio.wav"
        self.path.write_bytes(b"audio" * 40_000)
        self.env = patch.dict(os.environ, {
            "CHORDSENSE_ANALYSIS_URL": "https://worker.example/analyze",
            "CHORDSENSE_ANALYSIS_TOKEN": "private-test-token",
            "CHORDSENSE_ANALYSIS_TIMEOUT": "60",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def connection(self, *, status=200, body=None):
        connection = Mock()
        connection.getresponse.return_value = Mock(
            status=status, read=Mock(return_value=body if body is not None else json.dumps(RESULT).encode()),
        )
        return connection

    def test_streaming_multipart_has_exact_length_and_bounded_chunks(self):
        connection = self.connection()
        with patch("remote_analysis.http.client.HTTPSConnection", return_value=connection):
            result = analyze_remotely(self.path)
        self.assertEqual(result["chords"], RESULT["chords"])
        headers = dict(call.args for call in connection.putheader.call_args_list)
        parts = [call.args[0] for call in connection.send.call_args_list]
        self.assertEqual(int(headers["Content-Length"]), sum(map(len, parts)))
        self.assertEqual(headers["Authorization"], "Bearer private-test-token")
        self.assertTrue(all(len(part) <= 64 * 1024 for part in parts))
        self.assertIn(self.path.read_bytes(), b"".join(parts))
        connection.close.assert_called_once()

    def test_remote_failure_never_runs_a_local_model(self):
        connection = self.connection(status=503)
        with patch("remote_analysis.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(RemoteAnalysisError, "HTTP 503"):
                analyze_remotely(self.path)
        self.assertTrue(self.path.is_file())
        connection.close.assert_called_once()

    def test_rejects_insecure_remote_url_before_sending_token(self):
        with patch.dict(os.environ, {"CHORDSENSE_ANALYSIS_URL": "http://worker.example/analyze"}):
            with patch("remote_analysis.http.client.HTTPConnection") as connection:
                with self.assertRaisesRegex(RemoteAnalysisError, "HTTPS"):
                    analyze_remotely(self.path)
                connection.assert_not_called()

    def test_rejects_bad_response_and_closes_connection(self):
        connection = self.connection(body=b"invalid JSON")
        with patch("remote_analysis.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaises(RemoteAnalysisError):
                analyze_remotely(self.path)
        connection.close.assert_called_once()

    def test_rejects_invalid_chord_data(self):
        for chord in [
            {"start": 0, "end": float("nan"), "chord": "C"},
            {"start": 0, "end": 1, "chord": "C\n0 1 F"},
            {"start": 0, "end": 1, "chord": "C", "confidence": 2},
            {"start": -1, "end": 1, "chord": "C"},
        ]:
            with self.subTest(chord=chord), self.assertRaises(RemoteAnalysisError):
                validate_analysis({**RESULT, "chords": [chord]})


if __name__ == "__main__":
    unittest.main()
