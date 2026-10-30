"""Single-note pitch estimation for the tuner.

The chord recognizers cannot serve a tuner: chroma folds octaves together and a
2,048-point FFT at 22,050 Hz has ~10.8 Hz bins, about two semitones at low E.
This is a numpy port of ``iod/examples/support/pitch.rs`` (normalized
autocorrelation, first strong peak, parabolic interpolation), which was already
checked against real input with the ``spi_freq`` probe. The autocorrelation is
computed through an FFT so it can run on every 20 ms stream frame.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt


NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
DEFAULT_A4_HZ = 440.0
MIN_A4_HZ = 415.0
MAX_A4_HZ = 466.0


def estimate_f0(
    signal: npt.ArrayLike,
    fs: float,
    f_min: float,
    f_max: float,
    peak_threshold: float = 0.85,
    lowpass_hz: float | None = None,
) -> tuple[float, float] | None:
    """Estimate the fundamental of a DC-removed ``signal`` within ``f_min..f_max``.

    Returns ``(f0_hz, clarity)`` where clarity is the normalized-autocorrelation
    peak height in ``0..1``. ``f0_hz`` is ``0.0`` when no clear peak exists, and
    ``None`` is returned when the window cannot resolve the requested band.
    """
    values = np.asarray(signal, dtype=np.float64)
    n = len(values)
    min_lag = max(int(math.floor(fs / f_max)), 2)
    max_lag = min(int(math.ceil(fs / f_min)), n // 2)
    if min_lag >= max_lag:
        return None

    size = 1 << (2 * n - 1).bit_length()
    spectrum = np.fft.rfft(values, size)
    if lowpass_hz is not None and lowpass_hz < fs / 2:
        # Broadband noise only roughens the correlation curve; the low
        # harmonics carry all the period information a tuner needs.
        spectrum[int(lowpass_hz * size / fs) + 1 :] = 0
        values = np.fft.irfft(spectrum, size)[:n]
        spectrum = np.fft.rfft(values, size)
    autocorrelation = np.fft.irfft(spectrum * np.conj(spectrum), size)[: max_lag + 2]
    # pitch.rs normalizes by the energy of both overlapping segments:
    # e0 = sum(x[0:n-lag]^2), e1 = sum(x[lag:n]^2).
    energy = np.concatenate(([0.0], np.cumsum(values * values)))
    lags = np.arange(max_lag + 2)
    e0 = energy[np.maximum(n - lags, 0)]
    e1 = energy[n] - energy[np.minimum(lags, n)]
    denominator = np.sqrt(e0 * e1)
    corr = np.zeros(max_lag + 2)
    valid = (lags >= 1) & (lags <= max_lag) & (denominator > 0)
    corr[valid] = autocorrelation[valid] / denominator[valid]

    # Skip the lobe around lag zero: a smooth (low-passed or low-pitched)
    # signal still correlates highly at min_lag, which would read as f_max.
    negative = np.flatnonzero(corr[1 : max_lag + 1] < 0)
    if len(negative) == 0:
        return 0.0, 0.0
    min_lag = max(min_lag, int(negative[0]) + 1)
    if min_lag >= max_lag:
        return 0.0, 0.0

    global_max = float(max(corr[min_lag : max_lag + 1].max(), 0.0))
    # First lobe reaching 85% of the global peak: rejects the octave-down
    # error where a sub-harmonic lag scores highest. Unlike pitch.rs, take the
    # top of that whole lobe rather than its first local max, which noise
    # turns into a ripple on the rising edge (tens of cents flat at low E).
    threshold = peak_threshold * global_max
    inner = np.arange(min_lag + 1, max_lag)
    above = inner[corr[inner] >= threshold]
    if global_max <= 0 or len(above) == 0:
        return 0.0, global_max
    start = int(above[0])
    end = start
    while end + 1 < max_lag and corr[end + 1] >= threshold:
        end += 1
    best = start + int(np.argmax(corr[start : end + 1]))
    if best <= min_lag or best >= max_lag:
        return 0.0, global_max

    # Parabolic interpolation around the peak for sub-sample lag precision.
    a, b, c = corr[best - 1], corr[best], corr[best + 1]
    denom = a - 2.0 * b + c
    shift = 0.5 * (a - c) / denom if denom != 0 else 0.0
    return fs / (best + shift), float(b)


def midi_from_hz(freq: float, a4_hz: float = DEFAULT_A4_HZ) -> float:
    return 69.0 + 12.0 * math.log2(freq / a4_hz)


def hz_from_midi(midi: float, a4_hz: float = DEFAULT_A4_HZ) -> float:
    return a4_hz * 2.0 ** ((midi - 69.0) / 12.0)


def nearest_note(midi: float) -> tuple[str, int, int, float]:
    """Nearest equal-tempered note for a fractional MIDI number.

    Returns ``(name, octave, midi_note, cents)`` with cents in ``-50..50``.
    """
    note = int(round(midi))
    return NOTE_NAMES[note % 12], note // 12 - 1, note, (midi - note) * 100.0


@dataclass(frozen=True)
class PitchReading:
    sample_index: int
    f0_hz: float | None
    clarity: float
    note: str | None = None
    octave: int | None = None
    midi_note: int | None = None
    cents: float | None = None


class PitchTracker:
    """Stateful tuner front end: sliding window, gating and note smoothing.

    Each ``push`` returns at most one reading for the newest window. Raw
    estimates go through a short median (rejects single-frame octave slips) and
    an EMA on the log-frequency scale, so the needle settles without lagging a
    whole note behind. A pluck's attack is muted briefly because it reads sharp.
    """

    def __init__(
        self,
        sample_rate: int = 22_050,
        window: int = 2048,
        f_min: float = 60.0,
        f_max: float = 1200.0,
        min_clarity: float = 0.8,
        median_frames: int = 5,
        smoothing: float = 0.35,
        attack_mute_seconds: float = 0.04,
        attack_ratio: float = 2.5,
        note_jump_cents: float = 60.0,
        lowpass_hz: float = 2000.0,
        a4_hz: float = DEFAULT_A4_HZ,
    ):
        if window < 256 or not 0 < f_min < f_max < sample_rate / 2:
            raise ValueError("Invalid tuner window or frequency range")
        if not 0 < smoothing <= 1 or median_frames < 1:
            raise ValueError("Invalid tuner smoothing")
        self.sample_rate = sample_rate
        self.window = window
        self.default_range = (f_min, f_max)
        self.min_clarity = min_clarity
        self.smoothing = smoothing
        self.attack_mute_samples = round(attack_mute_seconds * sample_rate)
        self.attack_ratio = attack_ratio
        self.note_jump_cents = note_jump_cents
        self.lowpass_hz = lowpass_hz
        self.a4_hz = DEFAULT_A4_HZ
        self.target_midi: int | None = None
        self._history: deque[float] = deque(maxlen=median_frames)
        self._smoothed: float | None = None
        self.configure(a4_hz=a4_hz)
        self.reset()

    def configure(self, a4_hz: float | None = None, target_midi: int | None | str = "keep") -> None:
        """Change the reference pitch or lock the search to one string.

        A target narrows the search to half an octave either side of it, which
        removes octave errors when the player has picked a string.
        """
        if a4_hz is not None:
            if not MIN_A4_HZ <= a4_hz <= MAX_A4_HZ:
                raise ValueError(f"a4_hz must be between {MIN_A4_HZ:g} and {MAX_A4_HZ:g}")
            self.a4_hz = float(a4_hz)
        if target_midi != "keep":
            if target_midi is not None and not 28 <= target_midi <= 96:
                raise ValueError("target_midi must be between 28 and 96")
            self.target_midi = target_midi
        self._history_clear()

    @property
    def search_range(self) -> tuple[float, float]:
        if self.target_midi is None:
            return self.default_range
        return (
            hz_from_midi(self.target_midi - 6, self.a4_hz),
            hz_from_midi(self.target_midi + 6, self.a4_hz),
        )

    def _history_clear(self) -> None:
        self._history.clear()
        self._smoothed = None

    def reset(self) -> None:
        self.buffer = np.zeros(0, dtype=np.float32)
        self.samples_seen = 0
        self._mute_until = 0
        self._previous_rms = 0.0
        self._history_clear()

    def push(self, samples: npt.ArrayLike) -> PitchReading | None:
        values = np.asarray(samples, dtype=np.float32)
        if values.ndim != 1 or values.size == 0:
            return None
        rms = float(np.sqrt(np.mean((values - values.mean()) ** 2)))
        if rms > self.attack_ratio * self._previous_rms:
            # A fresh pluck (or the first sound after a reset): forget the old
            # note and skip the noisy transient.
            self._history_clear()
            self._mute_until = self.samples_seen + values.size + self.attack_mute_samples
        self._previous_rms = rms
        self.buffer = np.concatenate((self.buffer, values))[-self.window :]
        self.samples_seen += values.size
        if len(self.buffer) < self.window or self.samples_seen < self._mute_until:
            return None

        frame = self.buffer - self.buffer.mean()
        f_min, f_max = self.search_range
        estimate = estimate_f0(
            frame, self.sample_rate, f_min, f_max,
            lowpass_hz=max(self.lowpass_hz, 1.5 * f_max),
        )
        if estimate is None or estimate[0] <= 0 or estimate[1] < self.min_clarity:
            return PitchReading(self.samples_seen, None, estimate[1] if estimate else 0.0)
        f0, clarity = estimate

        midi = midi_from_hz(f0, self.a4_hz)
        if self._smoothed is not None and abs(midi - self._smoothed) * 100 > self.note_jump_cents:
            self._history.clear()
            self._smoothed = None
        self._history.append(midi)
        median = float(np.median(self._history))
        self._smoothed = median if self._smoothed is None else (
            self._smoothed + self.smoothing * (median - self._smoothed)
        )
        name, octave, midi_note, cents = nearest_note(self._smoothed)
        return PitchReading(
            sample_index=self.samples_seen,
            f0_hz=hz_from_midi(self._smoothed, self.a4_hz),
            clarity=clarity,
            note=name,
            octave=octave,
            midi_note=midi_note,
            cents=cents,
        )
