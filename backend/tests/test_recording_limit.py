import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import recordings_library as library


class RecordingLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.captures = self.root / "captures"
        self.captures.mkdir()
        self.patches = patch.multiple(
            library, CAPTURES_DIR=self.captures,
            INDEX_FILE=self.root / "recordings-index.json",
        )
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def populate(self, count):
        for number in range(count):
            (self.captures / f"capture-{number}.wav").write_bytes(b"saved audio")

    def test_twentieth_recording_is_allowed(self):
        self.populate(19)
        start = Mock()
        library.start_recording_capture(start)
        start.assert_called_once_with()

    def test_full_and_overfull_libraries_preserve_every_recording(self):
        for count in (20, 23):
            with self.subTest(count=count):
                self.populate(count)
                before = {p.name: p.read_bytes() for p in self.captures.iterdir()}
                start = Mock()
                with self.assertRaises(library.RecordingLimitReached):
                    library.start_recording_capture(start)
                start.assert_not_called()
                self.assertEqual(len(library.list_recordings()), count)
                self.assertEqual(before, {p.name: p.read_bytes() for p in self.captures.iterdir()})

    def test_explicit_deletion_frees_a_slot(self):
        self.populate(20)
        recording = library.list_recordings()[0]
        library.delete_recording(recording["id"])
        start = Mock()
        library.start_recording_capture(start)
        start.assert_called_once_with()

    def test_failed_capture_does_not_consume_a_slot(self):
        self.populate(19)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            library.start_recording_capture(Mock(side_effect=RuntimeError("unavailable")))
        start = Mock()
        library.start_recording_capture(start)
        start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
