import math
import time
import unittest

import numpy as np

from iod_client import IodSampleFrame
from live_input_session import LiveInputBusyError
from pitch_detection import PitchTracker, estimate_f0, midi_from_hz, nearest_note
from tuner_session import TunerSession, validate_settings
from tests.test_live_feedback import FakeIod, FakeStream


RATE = 22_050
STANDARD = {"E2": 82.4069, "A2": 110.0, "D3": 146.832, "G3": 195.998, "B3": 246.942, "E4": 329.628}


def pluck(frequency, seconds=1.0, harmonics=(1, .6, .4, .25, .15, .1), noise=0.0, seed=0):
    """A decaying string-like tone: each partial fades faster than the last."""
    rng = np.random.default_rng(seed)
    times = np.arange(int(RATE * seconds)) / RATE
    tone = sum(
        amplitude * np.sin(2 * np.pi * frequency * (k + 1) * times + rng.uniform(0, 2 * np.pi))
        * np.exp(-times * (1.5 + k))
        for k, amplitude in enumerate(harmonics)
    )
    tone = tone / np.abs(tone).max() * 0.5
    return (tone + noise * rng.standard_normal(len(times))).astype(np.float32)


def cents_error(measured, expected):
    return (midi_from_hz(measured) - midi_from_hz(expected)) * 100


def track(signal, tracker=None, chunk=441):
    tracker = tracker or PitchTracker()
    readings = []
    for start in range(0, len(signal), chunk):
        reading = tracker.push(signal[start:start + chunk])
        if reading is not None and reading.f0_hz is not None:
            readings.append(reading)
    return readings


def reference_estimate_f0(signal, fs, f_min, f_max):
    """Direct transcription of pitch.rs' normalized autocorrelation."""
    n = len(signal)
    min_lag = max(int(fs // f_max), 2)
    max_lag = min(math.ceil(fs / f_min), n // 2)
    corr = np.zeros(max_lag + 2)
    for lag in range(min_lag, max_lag + 1):
        a, b = signal[:n - lag], signal[lag:]
        e0, e1 = float(a @ a), float(b @ b)
        corr[lag] = float(a @ b) / math.sqrt(e0 * e1) if e0 > 0 and e1 > 0 else 0.0
    return corr


class EstimatorTests(unittest.TestCase):
    def test_fft_correlation_matches_the_pitch_rs_loop(self):
        window = pluck(110.0)[:2048].astype(np.float64)
        window -= window.mean()
        expected = reference_estimate_f0(window, RATE, 60, 1200)
        f0, clarity = estimate_f0(window, RATE, 60, 1200)
        best = round(RATE / f0)
        self.assertAlmostEqual(clarity, expected[best], places=9)
        self.assertLess(abs(cents_error(f0, 110.0)), 0.2)

    def test_band_the_window_cannot_resolve_is_reported(self):
        # 30 samples allow lags up to 15, below 1,200 Hz's 18-sample period.
        self.assertIsNone(estimate_f0(np.ones(30), RATE, 20, 1200))

    def test_no_periodicity_is_zero_hz(self):
        noise = np.random.default_rng(3).standard_normal(2048)
        f0, _ = estimate_f0(noise, RATE, 60, 1200, lowpass_hz=2000)
        self.assertTrue(f0 == 0.0 or _ < 0.8)

    def test_noise_does_not_pull_low_e_onto_a_rising_edge_ripple(self):
        # Regression: the first-local-max rule read up to ~70 cents flat here.
        signal = pluck(82.4069, seconds=1.2, noise=0.01, seed=1)
        for start in (0.3, 0.5, 0.7):
            window = signal[int(start * RATE):int(start * RATE) + 2048]
            f0, clarity = estimate_f0(window - window.mean(), RATE, 60, 1200, lowpass_hz=2000)
            self.assertGreater(clarity, 0.8)
            self.assertLess(abs(cents_error(f0, 82.4069)), 5)

    def test_low_passed_low_e_does_not_read_as_the_top_of_the_band(self):
        # Regression: the lag-zero lobe still exceeded 85% at min_lag.
        window = pluck(82.4069, noise=0.01)[int(0.9 * RATE):int(0.9 * RATE) + 2048]
        f0, _ = estimate_f0(window - window.mean(), RATE, 60, 1200, lowpass_hz=1500)
        self.assertLess(abs(cents_error(f0, 82.4069)), 10)


class NoteNamingTests(unittest.TestCase):
    def test_nearest_note_and_cents(self):
        self.assertEqual(nearest_note(69.0), ("A", 4, 69, 0.0))
        name, octave, midi, cents = nearest_note(midi_from_hz(82.4069))
        self.assertEqual((name, octave, midi), ("E", 2, 40))
        self.assertLess(abs(cents), 0.01)
        name, _, _, cents = nearest_note(60.49)
        self.assertEqual(name, "C")
        self.assertAlmostEqual(cents, 49.0)


class TrackerTests(unittest.TestCase):
    def test_every_open_string_settles_within_two_cents(self):
        timbres = {
            "bright": (1, .6, .4, .25, .15, .1),
            "weak fundamental": (0.15, 1, .8, .6, .4, .3),
        }
        for timbre, harmonics in timbres.items():
            for name, frequency in STANDARD.items():
                for offset in (-20, -5, -1, 0, 1, 5, 20):
                    with self.subTest(timbre=timbre, string=name, offset=offset):
                        target = frequency * 2 ** (offset / 1200)
                        readings = track(pluck(target, harmonics=harmonics, noise=0.002))
                        # Judge the sustained part: after ~150 ms of settling.
                        settled = [r for r in readings if r.sample_index > 0.25 * RATE]
                        self.assertGreater(len(settled), 20)
                        errors = [abs(cents_error(r.f0_hz, target)) for r in settled]
                        self.assertLess(max(errors), 2.0)
                        expected_note = nearest_note(midi_from_hz(target))
                        self.assertTrue(all(r.note == expected_note[0] for r in settled))

    def test_first_reading_arrives_within_150_ms_of_a_pluck(self):
        readings = track(pluck(110.0))
        self.assertTrue(readings)
        self.assertLessEqual(readings[0].sample_index, 0.15 * RATE)

    def test_a_new_note_is_followed_without_gliding_through_the_old_one(self):
        signal = np.concatenate((pluck(82.4069, 0.6), pluck(110.0, 0.6) * 1.0))
        tracker = PitchTracker()
        readings = track(signal, tracker)
        after = [r for r in readings if r.sample_index > 0.6 * RATE + 0.2 * RATE]
        self.assertTrue(after)
        self.assertTrue(all(r.note == "A" for r in after))

    def test_reference_pitch_changes_the_cents_reading(self):
        tracker = PitchTracker(a4_hz=432.0)
        readings = track(pluck(108.0), tracker)
        settled = readings[-10:]
        self.assertTrue(all(r.note == "A" and abs(r.cents) < 2 for r in settled))

    def test_string_lock_narrows_the_search_around_the_target(self):
        tracker = PitchTracker()
        tracker.configure(target_midi=40)
        low, high = tracker.search_range
        self.assertAlmostEqual(low, 82.4069 * 2 ** (-0.5), places=2)
        self.assertAlmostEqual(high, 82.4069 * 2 ** 0.5, places=2)
        # Badly flat low E (a semitone and a half down) still reads as E's region.
        readings = track(pluck(82.4069 * 2 ** (-150 / 1200)), tracker)
        self.assertTrue(readings)
        self.assertTrue(all(abs(cents_error(r.f0_hz, 82.4069) + 150) < 3 for r in readings[-10:]))
        tracker.configure(target_midi=None)
        self.assertEqual(tracker.search_range, (60.0, 1200.0))

    def test_invalid_configuration_is_rejected(self):
        tracker = PitchTracker()
        with self.assertRaises(ValueError):
            tracker.configure(a4_hz=300)
        with self.assertRaises(ValueError):
            tracker.configure(target_midi=10)


def frames_from(signal, start_index=0):
    pcm = np.clip(signal * 32767, -32768, 32767).astype("<i2")
    return [
        IodSampleFrame(start_index + index, pcm[index:index + 441].tobytes(), time.monotonic_ns())
        for index in range(0, len(pcm) - 440, 441)
    ]


def collect(session, session_id, until, timeout=3.0):
    events, sequence = [], 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not until(events):
        event = session.wait_event(session_id, sequence, timeout=0.1)
        if event:
            sequence = event["sequence"]
            events.append(event)
    return events


class TunerSessionTests(unittest.TestCase):
    def test_pitch_events_silence_and_clipping_transitions(self):
        silence = np.zeros(int(0.3 * RATE), dtype=np.float32)
        clipped = np.full(441 * 3, 1.0, dtype=np.float32)
        signal = np.concatenate((silence, pluck(110.0, 0.6), silence, clipped))
        stream = FakeStream(frames_from(signal))
        session = TunerSession(iod=FakeIod([stream]))
        started = session.start()
        self.assertEqual(started["state"], "running")
        self.assertEqual(started["a4_hz"], 440.0)
        try:
            events = collect(session, started["session_id"], lambda events: any(
                e.get("status") == "clipping" for e in events
            ))
        finally:
            stopped = session.stop()
        statuses = [e["status"] for e in events if e["type"] == "status"]
        self.assertEqual(statuses[:2], ["warming_up", "silence"])
        self.assertIn("listening", statuses)
        self.assertEqual(statuses[-1], "clipping")
        pitches = [e for e in events if e["type"] == "pitch" and e["note"]]
        self.assertTrue(pitches)
        self.assertTrue(all(e["note"] == "A" and e["octave"] == 2 for e in pitches[-5:]))
        self.assertLess(abs(pitches[-1]["cents"]), 3)
        self.assertEqual(pitches[-1]["input_quality"], "ok")
        self.assertIsNone(pitches[-1]["target_midi"])
        self.assertEqual(stopped["state"], "idle")
        self.assertTrue(stream.closed)

    def test_configuration_applies_to_a_running_session(self):
        stream = FakeStream(frames_from(pluck(110.0, 1.5)))
        session = TunerSession(iod=FakeIod([stream]))
        started = session.start()
        try:
            self.assertEqual(session.configure(target_midi=45)["target_midi"], 45)
            events = collect(session, started["session_id"], lambda events: any(
                e.get("type") == "pitch" and e.get("target_midi") == 45 and e.get("note")
                for e in events
            ))
        finally:
            session.stop()
        self.assertTrue(any(e.get("target_midi") == 45 for e in events if e["type"] == "pitch"))
        self.assertEqual(session.status()["target_midi"], 45)

    def test_stream_gap_resets_and_is_reported(self):
        frames = frames_from(pluck(110.0, 0.4))
        frames += frames_from(pluck(110.0, 0.4), start_index=100_000)
        session = TunerSession(iod=FakeIod([FakeStream(frames)]))
        started = session.start()
        try:
            events = collect(session, started["session_id"], lambda events: any(
                e.get("status") == "stream_gap" for e in events
            ))
        finally:
            session.stop()
        self.assertTrue(any(e.get("status") == "stream_gap" for e in events))

    def test_second_start_is_busy_with_a_tuner_message(self):
        session = TunerSession(iod=FakeIod([FakeStream([])]))
        session.start()
        try:
            with self.assertRaisesRegex(LiveInputBusyError, "^tuner is already running"):
                session.start()
        finally:
            session.stop()

    def test_request_settings_are_validated(self):
        self.assertEqual(validate_settings(442, None), {"a4_hz": 442.0, "target_midi": None})
        self.assertEqual(validate_settings(), {})
        for bad in ({"a4_hz": "440"}, {"a4_hz": True}, {"a4_hz": 500},
                    {"target_midi": 40.0}, {"target_midi": 200}, {"target_midi": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_settings(**bad)


if __name__ == "__main__":
    unittest.main()
