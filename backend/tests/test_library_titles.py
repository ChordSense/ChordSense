import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import app as backend_app
import library_titles
import recordings_library
import web_upload


class LibraryTitlesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.uploads = self.root / "uploads"
        self.captures = self.root / "captures"
        self.uploads.mkdir()
        self.captures.mkdir()
        for module, name, value in (
            (library_titles, "TITLES_FILE", self.root / "titles.json"),
            (web_upload, "UPLOADS_DIR", self.uploads),
            (recordings_library, "CAPTURES_DIR", self.captures),
            (recordings_library, "INDEX_FILE", self.root / "recordings.json"),
        ):
            patched = patch.object(module, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.client = backend_app.app.test_client()
        (self.uploads / "song.wav").write_bytes(b"original audio")
        (self.captures / "capture-1.wav").write_bytes(b"recorded audio")

    def rename(self, **payload):
        return self.client.post("/api/library/title", json=payload)

    def test_upload_title_survives_reload_without_changing_file_identity(self):
        before = self.client.get("/api/uploads").get_json()["files"][0]
        response = self.rename(kind="songs", filename="song.wav", title="  Café rehearsal  ")
        self.assertEqual(response.status_code, 200)
        after = self.client.get("/api/uploads").get_json()["files"][0]
        self.assertEqual(after["title"], "Café rehearsal")
        for key in ("name", "download_url", "size", "modified"):
            self.assertEqual(before[key], after[key])
        with self.client.get(after["download_url"]) as download:
            self.assertEqual(download.data, b"original audio")
        key = library_titles.upload_title_key(self.uploads / "song.wav")
        self.assertEqual(json.loads(library_titles.TITLES_FILE.read_text(encoding="utf-8"))[key], "Café rehearsal")

    def test_recording_title_survives_analysis_and_preserves_other_recordings(self):
        first = recordings_library.list_recordings()[0]
        self.assertEqual(first["name"], "Recording #1")
        response = self.rename(kind="recordings", id=first["id"], title="First take")
        self.assertEqual(response.status_code, 200)
        saved = recordings_library.save_recording_analysis(first["wav_path"], [{"chord": "C"}], 5)
        self.assertEqual(saved["name"], "First take")
        self.assertEqual(Path(first["wav_path"]).read_bytes(), b"recorded audio")
        (self.captures / "capture-2.wav").write_bytes(b"second take")
        self.assertEqual(recordings_library.list_recordings()[0]["name"], "Recording #2")

    def test_replacement_upload_does_not_inherit_a_deleted_tracks_title(self):
        self.rename(kind="songs", filename="song.wav", title="Old take")
        path = self.uploads / "song.wav"
        previous_time = path.stat().st_mtime_ns
        self.assertEqual(self.client.delete("/api/uploads/song.wav").status_code, 200)
        path.write_bytes(b"new song")
        os.utime(path, ns=(previous_time + 1_000_000, previous_time + 1_000_000))
        listed = self.client.get("/api/uploads").get_json()["files"][0]
        self.assertEqual(listed["title"], "song.wav")

    def test_invalid_titles_do_not_replace_a_saved_title(self):
        self.rename(kind="songs", filename="song.wav", title="Keep this")
        for title in (None, 42, "", "   ", "x" * 81, "a\nb", "a\x00b"):
            with self.subTest(title=title):
                self.assertEqual(self.rename(kind="songs", filename="song.wav", title=title).status_code, 400)
        self.assertEqual(library_titles.get_title(library_titles.upload_title_key(self.uploads / "song.wav"), ""), "Keep this")

    def test_unknown_or_unsafe_targets_are_rejected(self):
        for filename, status in (("missing.wav", 404), ("../song.wav", 400), (None, 400)):
            self.assertEqual(self.rename(kind="songs", filename=filename, title="Test").status_code, status)
        self.assertEqual(self.rename(kind="recordings", id=999, title="Test").status_code, 404)
        for recording_id in (True, -1, "1"):
            self.assertEqual(self.rename(kind="recordings", id=recording_id, title="Test").status_code, 400)
        self.assertEqual(self.rename(kind="other", title="Test").status_code, 400)

    def test_remote_browser_cannot_rename_tracks(self):
        response = self.client.post("/api/library/title", json={"kind": "songs", "filename": "song.wav", "title": "Remote"}, headers={"Origin": "https://example.com"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(library_titles.TITLES_FILE.exists())

    def test_concurrent_titles_are_all_preserved(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(lambda number: library_titles.set_title(f"upload:{number}", f"Song {number}"), range(20)))
        saved = json.loads(library_titles.TITLES_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 20)

    def test_malformed_request_and_write_failure_return_errors(self):
        self.assertEqual(self.client.post("/api/library/title", json=[]).status_code, 400)
        with patch.object(library_titles, "set_title", side_effect=OSError("disk full")):
            self.assertEqual(self.rename(kind="songs", filename="song.wav", title="Test").status_code, 500)


if __name__ == "__main__":
    unittest.main()
