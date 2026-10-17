import io
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import FileStorage
import library_titles
import web_upload


class UploadLimitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for module, name, value in (
            (web_upload, "UPLOADS_DIR", self.root),
            (library_titles, "TITLES_FILE", self.root / "titles.json"),
        ):
            patched = patch.object(module, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.app = Flask(__name__)
        self.app.register_blueprint(web_upload.web_upload)

    def populate(self, count):
        for number in range(count):
            (self.root / f"song-{number}.wav").write_bytes(f"audio {number}".encode())

    def upload(self, name="new.wav"):
        with self.app.test_client() as client:
            return client.post("/api/uploads", data={"file": (io.BytesIO(b"new audio"), name)})

    def test_tenth_upload_is_allowed_and_eleventh_is_rejected(self):
        self.populate(9)
        self.assertEqual(self.upload().status_code, 201)
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        with patch.object(FileStorage, "save", side_effect=AssertionError("Full libraries must reject before writing")):
            response = self.upload("eleventh.wav")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error_code"], "upload_limit_reached")
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_overfull_library_is_preserved_and_remains_listed(self):
        self.populate(12)
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        self.assertEqual(self.upload().status_code, 409)
        with self.app.test_client() as client:
            self.assertEqual(len(client.get("/api/uploads").get_json()["files"]), 12)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_explicit_deletion_frees_a_slot(self):
        self.populate(10)
        with self.app.test_client() as client:
            self.assertEqual(client.delete("/api/uploads/song-0.wav").status_code, 200)
        self.assertEqual(self.upload().status_code, 201)

    def test_non_audio_and_incomplete_files_do_not_use_slots(self):
        self.populate(9)
        (self.root / "notes.txt").write_text("notes")
        (self.root / "pending.upload").write_bytes(b"incomplete")
        self.assertEqual(self.upload().status_code, 201)

    def test_simultaneous_uploads_cannot_both_take_the_last_slot(self):
        self.populate(9)
        barrier = threading.Barrier(2)
        original_save = FileStorage.save

        def save_then_wait(file, destination, *args, **kwargs):
            original_save(file, destination, *args, **kwargs)
            barrier.wait(timeout=5)

        with patch.object(FileStorage, "save", save_then_wait), ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(self.upload, ("one.wav", "two.wav")))
        self.assertEqual(sorted(response.status_code for response in responses), [201, 409])
        self.assertEqual(len(list(self.root.glob("*.wav"))), 10)
        self.assertEqual(list(self.root.glob("*.upload")), [])


if __name__ == "__main__":
    unittest.main()
