import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from offline_model import run_local_model


class OfflineModelTests(unittest.TestCase):
    def test_successful_process_must_create_new_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "chord_recognition.py").touch()
            python = root / "venv/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
            lab = root / "chords.lab"
            lab.write_text("0 1 stale\n")
            with patch.dict(os.environ, {"CHORDSENSE_MODEL_PYTHON": str(python)}), \
                 patch("offline_model.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
                with self.assertRaisesRegex(RuntimeError, "no chord output"):
                    run_local_model(root / "audio.wav", lab, "submission", model_repo=root)


if __name__ == "__main__":
    unittest.main()
