import io
import errno
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, jsonify

import analysis_cache
import app as backend_app
import web_upload
from analysis_jobs import AnalysisJobs, AnalysisQueueFull
from device_access import install_device_access
from pairing import PairingManager


class DeviceAccessTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        self.manager = PairingManager()
        install_device_access(app, self.manager)
        app.add_url_rule("/health", view_func=lambda: jsonify(success=True))
        self.client = app.test_client()
        self.environment = patch.dict(os.environ, {
            "CHORDSENSE_ALLOWED_ORIGINS": "https://chordsense.vercel.app,http://localhost:5173",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        code = self.manager.start()["code"]
        self.token = self.manager.claim(code, "https://chordsense.vercel.app")["token"]

    def test_exact_origin_and_token_are_required_for_browser(self):
        headers = {"Origin": "https://chordsense.vercel.app"}
        self.assertEqual(self.client.get("/health", headers=headers).status_code, 401)
        headers["Authorization"] = f"Bearer {self.token}"
        response = self.client.get("/health", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], headers["Origin"])
        headers["Origin"] = "https://chordsense.vercel.app.attacker.example"
        response = self.client.get("/health", headers=headers)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_preflight_does_not_require_browser_to_send_a_token(self):
        response = self.client.options("/health", headers={
            "Origin": "https://chordsense.vercel.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        })
        self.assertEqual(response.status_code, 204)
        self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])

    def test_local_desktop_stays_compatible_but_proxy_headers_never_bypass_auth(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        for name in ("X-Forwarded-For", "Forwarded", "CF-Connecting-IP", "True-Client-IP"):
            with self.subTest(header=name):
                response = self.client.get("/health", headers={name: "192.0.2.1"})
                self.assertEqual(response.status_code, 403)

    def test_unconfigured_browser_origins_fail_closed(self):
        with patch.dict(os.environ, {"CHORDSENSE_ALLOWED_ORIGINS": ""}):
            self.assertEqual(self.client.get("/health").status_code, 200)
            self.assertEqual(self.client.get("/health", headers={
                "Origin": "https://chordsense.vercel.app",
            }).status_code, 403)
            self.assertEqual(self.client.get("/health", environ_overrides={
                "REMOTE_ADDR": "192.0.2.1",
            }).status_code, 403)


class AnalysisJobsTests(unittest.TestCase):
    @staticmethod
    def wait_for(jobs, job_id):
        until = time.monotonic() + 2
        while time.monotonic() < until:
            result = jobs.status(job_id)
            if result and result["state"] in {"succeeded", "failed"}:
                return result
            time.sleep(0.005)
        raise AssertionError("Job did not finish")

    def test_queue_is_bounded_and_runs_only_one_analysis(self):
        jobs = AnalysisJobs(max_pending=1)
        entered, release = threading.Event(), threading.Event()

        def work():
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test worker timed out")
            return {"success": True}

        first = jobs.submit(work)
        self.assertTrue(entered.wait(1))
        try:
            second = jobs.submit(lambda: {"success": True})
            self.assertEqual(jobs.status(first)["state"], "running")
            self.assertEqual(jobs.status(second)["state"], "queued")
            with self.assertRaises(AnalysisQueueFull):
                jobs.submit(lambda: {"success": True})
        finally:
            release.set()
        self.assertEqual(self.wait_for(jobs, second)["state"], "succeeded")

    def test_failure_is_reported_and_completed_results_are_bounded(self):
        jobs = AnalysisJobs(max_results=1)
        failed = jobs.submit(lambda: {"success": False, "error": "worker offline"})
        self.assertEqual(self.wait_for(jobs, failed)["error"], "worker offline")
        succeeded = jobs.submit(lambda: {"success": True})
        self.wait_for(jobs, succeeded)
        self.assertIsNone(jobs.status(failed))


class DeviceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.inputs, self.outputs, self.uploads, self.captures = (
            root / name for name in ("inputs", "outputs", "uploads", "captures")
        )
        for directory in (self.inputs, self.outputs, self.uploads, self.captures):
            directory.mkdir()
        self.source = self.uploads / "song.wav"
        self.source.write_bytes(b"audio source")
        for target, name, value in (
            (backend_app, "INPUTS_DIR", self.inputs),
            (backend_app, "OUTPUTS_DIR", self.outputs),
            (web_upload, "UPLOADS_DIR", self.uploads),
            (analysis_cache, "CACHE_DIR", root / "cache"),
            (backend_app, "analysis_jobs", AnalysisJobs()),
        ):
            item = patch.object(target, name, value)
            item.start()
            self.addCleanup(item.stop)
        item = patch.dict(os.environ, {
            "CHORDSENSE_ANALYSIS_URL": "",
            "CHORDSENSE_ALLOWED_ORIGINS": "https://chordsense.vercel.app",
            "CHORDSENSE_CAPTURES_DIR": str(self.captures),
        })
        item.start()
        self.addCleanup(item.stop)
        self.client = backend_app.app.test_client()
        manager = PairingManager()
        item = patch.dict(backend_app.app.extensions, {"chordsense_pairing": manager})
        item.start()
        self.addCleanup(item.stop)
        self.token = manager.claim(manager.start()["code"], "https://chordsense.vercel.app")["token"]

    def fake_model(self, source, output, dictionary):
        self.assertEqual(source.read_bytes(), b"audio source")
        output.write_text("0.0 1.0 C\n")
        return "", "", None

    def test_stored_upload_analysis_reuses_source_and_cache(self):
        with patch.object(backend_app, "run_model", side_effect=self.fake_model) as model:
            first = self.client.post("/analyze_uploaded", json={"filename": "song.wav"})
            second = self.client.post("/analyze_uploaded", json={"filename": "song.wav"})
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.get_json()["cached"])
        self.assertTrue(second.get_json()["cached"])
        model.assert_called_once()
        self.assertTrue(self.source.is_file())
        self.assertEqual(list(self.inputs.iterdir()), [])
        self.assertEqual(list(self.outputs.iterdir()), [])

    def test_queued_stored_analysis_keeps_original_audio_after_companion_replacement(self):
        original_key, original_hash = analysis_cache.identify_audio(self.source, "submission")
        original_inode = self.source.stat().st_ino
        entered, release = threading.Event(), threading.Event()

        def occupy_worker():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test worker timed out")
            return {"success": True}

        blocker = backend_app.analysis_jobs.submit(occupy_worker)
        self.assertTrue(entered.wait(1))
        headers = {"Origin": "https://chordsense.vercel.app", "Authorization": f"Bearer {self.token}"}

        def replace_library(contents):
            self.assertEqual(self.client.delete("/api/uploads/song.wav", headers=headers).status_code, 200)
            uploaded = self.client.post("/api/uploads", headers=headers, data={
                "file": (io.BytesIO(contents), "song.wav"),
            })
            self.assertEqual(uploaded.status_code, 201)
            self.assertEqual(uploaded.get_json()["file"]["name"], "song.wav")

        def model(source, output, dictionary):
            contents = source.read_bytes()
            self.assertIn(contents, (b"audio source", b"replacement audio"))
            output.write_text(f"0.0 1.0 {'C' if contents == b'audio source' else 'D'}\n")
            return "", "", None

        with patch.object(backend_app, "run_model", side_effect=model) as run_model:
            try:
                queued = self.client.post("/analyze_uploaded?async=1", json={"filename": "song.wav"})
                self.assertEqual(queued.status_code, 202)
                job_id = queued.get_json()["job_id"]
                self.assertEqual(backend_app.analysis_jobs.status(job_id)["state"], "queued")
                snapshots = list(self.inputs.iterdir())
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0].stat().st_ino, original_inode)
                replace_library(b"replacement audio")
            finally:
                release.set()
                AnalysisJobsTests.wait_for(backend_app.analysis_jobs, blocker)
            result = AnalysisJobsTests.wait_for(backend_app.analysis_jobs, job_id)
            self.assertEqual(result["state"], "succeeded")
            self.assertEqual(result["result"]["cache_key"], original_key)
            self.assertEqual(result["result"]["chords"][0]["chord"], "C")
            self.assertEqual(analysis_cache.load_cache_entry(original_key).metadata["audio_sha256"], original_hash)
            self.assertEqual(list(self.inputs.iterdir()), [])

            replacement = self.client.post("/analyze_uploaded", json={"filename": "song.wav"}).get_json()
            self.assertFalse(replacement["cached"])
            self.assertNotEqual(replacement["cache_key"], original_key)
            self.assertEqual(replacement["chords"][0]["chord"], "D")
            replace_library(b"audio source")
            restored = self.client.post("/analyze_uploaded", json={"filename": "song.wav"}).get_json()
            self.assertTrue(restored["cached"])
            self.assertEqual(restored["chords"][0]["chord"], "C")
            self.assertEqual(run_model.call_count, 2)
        self.assertEqual(list(self.inputs.iterdir()), [])
        self.assertEqual(list(self.outputs.iterdir()), [])

    def test_started_stored_analysis_cannot_switch_audio_between_hash_and_model(self):
        original_key, _ = analysis_cache.identify_audio(self.source, "submission")

        def hash_then_replace(snapshot, dictionary):
            self.assertNotEqual(snapshot, self.source)
            self.assertEqual(snapshot.stat().st_ino, self.source.stat().st_ino)
            identity = analysis_cache.identify_audio(snapshot, dictionary)
            self.source.unlink()
            self.source.write_bytes(b"replacement audio")
            return identity

        with patch.object(backend_app, "identify_audio", side_effect=hash_then_replace), \
             patch.object(backend_app, "run_model", side_effect=self.fake_model):
            response = self.client.post("/analyze_uploaded", json={"filename": "song.wav"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["cache_key"], original_key)
        self.assertEqual(response.get_json()["chords"][0]["chord"], "C")
        self.assertEqual(self.source.read_bytes(), b"replacement audio")
        self.assertEqual(list(self.inputs.iterdir()), [])

    def test_stored_snapshot_is_removed_on_queue_rejection_and_model_failure(self):
        with patch.object(backend_app.analysis_jobs, "submit", side_effect=AnalysisQueueFull("busy")):
            rejected = self.client.post("/analyze_uploaded?async=1", json={"filename": "song.wav"})
        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(list(self.inputs.iterdir()), [])
        with patch.object(backend_app, "run_model", side_effect=RuntimeError("worker unavailable")):
            failed = self.client.post("/analyze_uploaded?async=1", json={"filename": "song.wav"})
            status = AnalysisJobsTests.wait_for(backend_app.analysis_jobs, failed.get_json()["job_id"])
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"], "worker unavailable")
        self.assertEqual(list(self.inputs.iterdir()), [])
        self.assertEqual(list(self.outputs.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), b"audio source")

    def test_cross_device_snapshot_streams_from_one_open_source(self):
        copy_stream = backend_app.shutil.copyfileobj

        def replace_then_copy(audio, output, length):
            self.assertEqual(length, 64 * 1024)
            self.source.unlink()
            self.source.write_bytes(b"replacement audio")
            return copy_stream(audio, output, length=length)

        with patch.object(backend_app.os, "link", side_effect=OSError(errno.EXDEV, "cross-device")), \
             patch.object(backend_app.shutil, "copyfileobj", side_effect=replace_then_copy):
            snapshot = backend_app._snapshot_uploaded_audio(self.source)
        try:
            self.assertEqual(snapshot.read_bytes(), b"audio source")
            self.assertEqual(self.source.read_bytes(), b"replacement audio")
        finally:
            snapshot.unlink()
        self.assertEqual(list(self.inputs.iterdir()), [])

    def test_failed_cross_device_copy_cleans_partial_snapshot(self):
        def fail_copy(audio, output, length):
            output.write(audio.read(3))
            raise OSError(errno.ENOSPC, "disk full")

        with patch.object(backend_app.os, "link", side_effect=OSError(errno.EXDEV, "cross-device")), \
             patch.object(backend_app.shutil, "copyfileobj", side_effect=fail_copy), \
             patch.object(backend_app, "run_model") as model:
            response = self.client.post("/analyze_uploaded", json={"filename": "song.wav"})
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.get_json()["success"])
        self.assertEqual(list(self.inputs.iterdir()), [])
        model.assert_not_called()

    def test_snapshot_disappearance_and_other_io_errors_have_json_responses(self):
        for error, status in ((FileNotFoundError(errno.ENOENT, "removed"), 404),
                              (PermissionError(errno.EACCES, "denied"), 500)):
            with self.subTest(error=error), \
                 patch.object(backend_app.os, "link", side_effect=error), \
                 patch.object(backend_app, "run_model") as model:
                response = self.client.post("/analyze_uploaded", json={"filename": "song.wav"})
                self.assertEqual(response.status_code, status)
                self.assertFalse(response.get_json()["success"])
                self.assertEqual(list(self.inputs.iterdir()), [])
                model.assert_not_called()

    def test_uploads_commit_complete_files_and_preserve_duplicates(self):
        first = self.client.post("/api/uploads", data={"file": (io.BytesIO(b"first"), "new.wav")})
        second = self.client.post("/api/uploads", data={"file": (io.BytesIO(b"second"), "new.wav")})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.get_json()["file"]["name"], second.get_json()["file"]["name"])
        self.assertEqual((self.uploads / first.get_json()["file"]["name"]).read_bytes(), b"first")
        self.assertEqual((self.uploads / second.get_json()["file"]["name"]).read_bytes(), b"second")
        self.assertEqual(list(self.uploads.glob("*.upload")), [])

    def test_async_uploaded_file_releases_request_and_removes_temporary_input(self):
        entered, release = threading.Event(), threading.Event()

        def model(*args):
            entered.set()
            release.wait(2)
            return self.fake_model(*args)

        with patch.object(backend_app, "run_model", side_effect=model):
            response = self.client.post("/analyze?async=1", data={
                "file": (io.BytesIO(b"audio source"), "song.wav"),
            })
            self.assertEqual(response.status_code, 202)
            self.assertTrue(entered.wait(1))
            status_url = response.get_json()["status_url"]
            self.assertEqual(self.client.get(status_url).get_json()["state"], "running")
            release.set()
            status = AnalysisJobsTests.wait_for(backend_app.analysis_jobs, response.get_json()["job_id"])
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["result"]["chords"][0]["chord"], "C")
        self.assertEqual(list(self.inputs.iterdir()), [])

    def test_full_queue_cleans_uploaded_input(self):
        with patch.object(backend_app.analysis_jobs, "submit", side_effect=AnalysisQueueFull("busy")):
            response = self.client.post("/analyze?async=1", data={
                "file": (io.BytesIO(b"audio source"), "song.wav"),
            })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(list(self.inputs.iterdir()), [])

    def test_local_recording_remote_worker_failure_preserves_take(self):
        recording = self.captures / "capture-123.wav"
        recording.write_bytes(b"captured guitar")
        with patch.dict(os.environ, {"CHORDSENSE_ANALYSIS_URL": "https://worker.example/analyze"}), \
             patch.object(backend_app.iod, "stop_capture", return_value=(recording, 1.0)), \
             patch("remote_analysis.analyze_remotely", side_effect=RuntimeError("worker unavailable")), \
             patch.object(backend_app, "create_recording_recognizer") as local:
            response = self.client.post("/end_recording")
        self.assertEqual(response.status_code, 500)
        self.assertTrue(recording.is_file())
        local.assert_not_called()
        self.assertEqual(response.get_json()["wav_path"], str(recording))
        self.assertEqual(recording.read_bytes(), b"captured guitar")

    def test_companion_library_download_supports_ranges_and_rejects_escape(self):
        self.source.write_bytes(b"0123456789")
        headers = {"Origin": "https://chordsense.vercel.app",
                   "Authorization": f"Bearer {self.token}", "Range": "bytes=2-5"}
        response = self.client.get("/api/uploads/song.wav", headers=headers)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"2345")
        response.close()
        outside = Path(self.temp.name) / "outside.wav"
        outside.write_bytes(b"private")
        (self.uploads / "escape.wav").symlink_to(outside)
        self.assertEqual(self.client.get("/api/uploads/escape.wav", headers=headers).status_code, 400)
        self.assertEqual(self.client.get("/api/uploads/../outside.wav", headers=headers).status_code, 400)

    def test_remote_companion_can_only_use_health_and_library_crud(self):
        headers = {"Origin": "https://chordsense.vercel.app", "Authorization": f"Bearer {self.token}"}
        self.assertEqual(self.client.get("/health", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/uploads", headers=headers).status_code, 200)
        uploaded = self.client.post("/api/uploads", headers=headers, data={
            "file": (io.BytesIO(b"companion song"), "new.wav"),
        })
        self.assertEqual(uploaded.status_code, 201)
        url = uploaded.get_json()["file"]["download_url"]
        download = self.client.get(url, headers=headers)
        self.assertEqual(download.data, b"companion song")
        download.close()
        self.assertEqual(self.client.delete(url, headers=headers).status_code, 200)

    def test_remote_control_and_inference_routes_are_rejected_before_work(self):
        routes = (
            ("POST", "/analyze?async=1"), ("POST", "/analyze_uploaded?async=1"),
            ("GET", "/api/jobs/unknown"), ("POST", "/begin_recording"),
            ("POST", "/end_recording"), ("POST", "/feedback/start"),
            ("POST", "/feedback/pause"), ("POST", "/feedback/resume"),
            ("POST", "/feedback/stop"), ("GET", "/feedback/events?session_id=unknown"),
            ("GET", "/feedback/status"), ("POST", "/tuner/start"),
            ("POST", "/tuner/config"), ("POST", "/tuner/stop"),
            ("GET", "/tuner/status"), ("GET", "/tuner/events?session_id=unknown"),
            ("POST", "/playback/load"),
            ("GET", "/playback/status"), ("GET", "/api/recordings/take.wav"),
            ("POST", "/health"), ("DELETE", "/api/uploads"), ("POST", "/api/uploads/song.wav"),
        )
        with patch.object(backend_app, "get_live_feedback_session") as feedback, \
             patch.object(backend_app, "get_tuner_session") as tuner, \
             patch.object(backend_app.iod, "start_capture") as begin, \
             patch.object(backend_app.iod, "stop_capture") as end, \
             patch.object(backend_app.playback, "load") as load, \
             patch.object(backend_app, "analyze_source") as analyze:
            for remote_headers in (
                {"Origin": "https://chordsense.vercel.app", "Authorization": f"Bearer {self.token}"},
                {"X-Forwarded-For": "192.0.2.1", "Authorization": f"Bearer {self.token}"},
            ):
                for method, route in routes:
                    with self.subTest(route=route, headers=remote_headers):
                        self.assertEqual(self.client.open(route, method=method, headers=remote_headers).status_code, 403)
            for action in (feedback, tuner, begin, end, load, analyze):
                action.assert_not_called()

    def test_native_local_controls_keep_original_contracts_without_session_ids(self):
        with patch.object(backend_app.iod, "start_capture") as begin:
            response = self.client.post("/begin_recording")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "success": True, "message": "Recording started", "model_used": "chordsense-cnn-hef",
        })
        begin.assert_called_once()
        with patch.object(backend_app, "get_live_feedback_session") as feedback:
            feedback.return_value.stop.return_value = {"state": "idle", "session_id": "local-session"}
            response = self.client.post("/feedback/stop")
        self.assertEqual(response.status_code, 200)
        feedback.return_value.stop.assert_called_once()

    def test_local_tuner_routes_validate_and_report_busy_hardware(self):
        from live_input_session import LiveInputBusyError
        from tuner_session import TunerSession

        session = TunerSession(iod=object())
        with patch.object(backend_app, "get_tuner_session", return_value=session):
            response = self.client.post("/tuner/start", json={"a4_hz": 900})
            self.assertEqual(response.status_code, 400)
            self.assertIn("a4_hz", response.get_json()["error"])
            response = self.client.post("/tuner/config", json={"a4_hz": 442, "target_midi": 40})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["a4_hz"], 442.0)
            self.assertEqual(response.get_json()["target_midi"], 40)
            self.assertEqual(self.client.post("/tuner/config", json=[]).status_code, 400)
            for body in ([], False, 1, "invalid"):
                self.assertEqual(self.client.post("/tuner/start", json=body).status_code, 400)
            self.assertEqual(self.client.get("/tuner/status").get_json()["state"], "idle")
            self.assertEqual(
                self.client.get("/tuner/events?session_id=unknown").get_json()["error"],
                "unknown tuner session",
            )
            with patch.object(session, "start", side_effect=LiveInputBusyError("cannot start stream while capturing")):
                response = self.client.post("/tuner/start", json={})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(self.client.post("/tuner/stop").get_json()["state"], "idle")

    def test_remote_control_preflight_is_also_rejected(self):
        response = self.client.options("/feedback/start", headers={
            "Origin": "https://chordsense.vercel.app", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        })
        self.assertEqual(response.status_code, 403)

    def test_companion_upload_cap_does_not_narrow_native_request_limit(self):
        with patch.dict(backend_app.app.config, {"MAX_CONTENT_LENGTH": 2048, "COMPANION_MAX_CONTENT_LENGTH": 32}):
            local = self.client.post("/api/uploads", data={"file": (io.BytesIO(b"song"), "local.wav")})
            companion = self.client.post("/api/uploads", data={"file": (io.BytesIO(b"song"), "remote.wav")}, headers={
                "Origin": "https://chordsense.vercel.app", "Authorization": f"Bearer {self.token}",
            })
        self.assertEqual(local.status_code, 201)
        self.assertEqual(companion.status_code, 413)

    def test_bad_json_and_filenames_are_client_errors(self):
        for body in ([], {"filename": 2}, {"filename": "../song.wav"}, {"filename": "song.wav", "chord_dict": []}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/analyze_uploaded", json=body).status_code, 400)

    def test_device_api_does_not_serve_web_pages_or_static_files(self):
        self.assertEqual(self.client.get("/upload").status_code, 404)
        self.assertEqual(self.client.get("/static/upload.js").status_code, 404)


if __name__ == "__main__":
    unittest.main()
