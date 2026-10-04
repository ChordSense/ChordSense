import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iod_client import IodError
from playback_speed import (
    PlaybackSpeedController,
    PlaybackSpeedError,
    original_position,
    validate_speed,
)


class FakeIod:
    def __init__(self):
        self.path = None
        self.position = 0.0
        self.playing = False
        self.calls = []
        self.fail_next_seek = False

    def load(self, path):
        self.path = Path(path)
        self.position = 0.0
        self.playing = False
        self.calls.append(("load", self.path))
        return 120.0

    def seek(self, position):
        self.calls.append(("seek", position))
        if self.fail_next_seek:
            self.fail_next_seek = False
            raise IodError("seek failed")
        self.position = position

    def resume(self):
        self.playing = True
        self.calls.append(("resume",))

    def pause(self):
        self.playing = False
        self.calls.append(("pause",))

    def stop_playback(self):
        self.position = 0.0
        self.playing = False
        self.calls.append(("stop",))

    def status(self):
        return {
            "path": str(self.path) if self.path else None,
            "position_secs": self.position,
            "duration_secs": 120.0,
            "playing": self.playing,
            "paused": not self.playing,
            "finished": False,
        }


class PlaybackSpeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "song.mp3"
        self.source.write_bytes(b"test source")
        self.variant = Path(self.temp.name) / "slow.mp3"
        self.iod = FakeIod()
        self.controller = PlaybackSpeedController(self.iod, Path(self.temp.name))
        self.controller.load(self.source)

    def test_speed_validation_rejects_non_finite_and_outside_range(self):
        for value in (None, "no", math.nan, math.inf, -math.inf, 0.49, 1.01):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_speed(value)
        self.assertEqual(validate_speed("0.75"), 0.75)

    def test_playing_track_switches_at_latest_song_position(self):
        self.iod.position = 20.0
        self.iod.playing = True

        def finish_render(*_args):
            # The track keeps playing while FFmpeg prepares the variant.
            self.iod.position = 23.0
            return self.variant

        with patch.object(self.controller, "_prepare_variant", side_effect=finish_render), \
             patch.object(self.controller, "_prune_cache"):
            result = self.controller.set_speed(0.5)

        self.assertEqual(self.iod.calls[-3:], [
            ("load", self.variant), ("seek", 46.0), ("resume",)
        ])
        self.assertEqual(result["speed"], 0.5)
        self.assertEqual(result["position"], 23.0)
        self.assertEqual(result["duration"], 120.0)
        self.assertEqual(result["path"], str(self.source))

        self.iod.position = 80.0
        self.assertEqual(self.controller.status()["position"], 40.0)
        self.controller.seek(50.0)
        self.assertEqual(self.iod.calls[-1], ("seek", 100.0))

    def test_paused_track_stays_paused_and_stop_keeps_speed(self):
        self.iod.position = 12.0
        with patch.object(self.controller, "_prepare_variant", return_value=self.variant), \
             patch.object(self.controller, "_prune_cache"):
            result = self.controller.set_speed(0.75)
        self.assertFalse(result["playing"])
        self.assertEqual(self.iod.calls[-2:], [
            ("load", self.variant), ("seek", 16.0)
        ])
        self.controller.stop()
        self.assertEqual(self.controller.status()["speed"], 0.75)
        self.assertEqual(self.controller.status()["position"], 0.0)
        self.controller.load(self.source)
        self.assertEqual(self.controller.status()["speed"], 1.0)

    def test_failed_swap_restores_previous_track_and_position(self):
        self.iod.position = 9.0
        self.iod.playing = True
        self.iod.fail_next_seek = True
        with patch.object(self.controller, "_prepare_variant", return_value=self.variant):
            with self.assertRaisesRegex(PlaybackSpeedError, "seek failed"):
                self.controller.set_speed(0.5)
        self.assertEqual(self.controller.status()["speed"], 1.0)
        self.assertEqual(self.iod.path, self.source)
        self.assertEqual(self.iod.position, 9.0)
        self.assertTrue(self.iod.playing)

    def test_failed_variant_leaves_current_song_running(self):
        self.iod.position = 15.0
        self.iod.playing = True
        with patch.object(self.controller, "_prepare_variant", side_effect=PlaybackSpeedError("ffmpeg")):
            with self.assertRaisesRegex(PlaybackSpeedError, "ffmpeg"):
                self.controller.set_speed(0.5)
        self.assertEqual(self.controller.status()["speed"], 1.0)
        self.assertTrue(self.iod.playing)
        self.assertEqual(self.iod.position, 15.0)

    def test_new_song_supersedes_an_inflight_speed_conversion(self):
        next_song = Path(self.temp.name) / "next.mp3"
        next_song.write_bytes(b"next source")

        def load_next_song(*_args):
            self.controller.load(next_song)
            return self.variant

        with patch.object(self.controller, "_prepare_variant", side_effect=load_next_song):
            result = self.controller.set_speed(0.5)
        self.assertEqual(result["path"], str(next_song))
        self.assertEqual(result["speed"], 1.0)
        self.assertEqual(self.iod.path, next_song)

    def test_position_conversion_clamps_to_original_duration(self):
        self.assertEqual(original_position(30.0, 0.5, 120.0), 15.0)
        self.assertEqual(original_position(260.0, 0.5, 120.0), 120.0)
        self.assertEqual(original_position(-2.0, 0.5, 120.0), 0.0)


class PlaybackSpeedRouteTests(unittest.TestCase):
    def setUp(self):
        import app as backend_app

        self.backend_app = backend_app
        self.client = backend_app.app.test_client()

    def test_speed_route_returns_effective_speed_and_song_position(self):
        payload = {"speed": 0.75, "position": 24.0, "duration": 120.0}
        with patch.object(self.backend_app.playback, "set_speed", return_value=payload) as call:
            response = self.client.post("/playback/speed", json={"speed": 0.75})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, **payload})
        call.assert_called_once_with(0.75)

    def test_speed_route_rejects_invalid_speed(self):
        response = self.client.post("/playback/speed", json={"speed": "NaN"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])

    def test_seek_route_rejects_non_finite_position(self):
        response = self.client.post("/playback/seek", json={"position_secs": "Infinity"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
