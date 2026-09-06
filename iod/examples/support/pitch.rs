//! Shared pitch-analysis helpers for the `spi_*` diagnostic examples.
//! Included with `#[path = "support/pitch.rs"] mod pitch;` — not an example
//! target itself (see `autoexamples = false` in Cargo.toml).

#![allow(dead_code)] // not every example uses every helper

/// Estimate the fundamental frequency of `signal` (already DC-removed) sampled
/// at `fs` Hz, searching `f_min..=f_max`. Returns `(f0_hz, confidence)` where
/// confidence is the normalized-autocorrelation peak height in `0.0..=1.0`, or
/// `None` if the sample rate can't resolve the requested band.
pub fn estimate_f0(signal: &[f32], fs: f64, f_min: f64, f_max: f64) -> Option<(f64, f64)> {
    let n = signal.len();
    let min_lag = ((fs / f_max).floor() as usize).max(2);
    let max_lag = ((fs / f_min).ceil() as usize).min(n / 2);
    if min_lag >= max_lag {
        return None;
    }

    let mut corr = vec![0.0f64; max_lag + 2];
    let mut global_max = 0.0f64;
    for lag in min_lag..=max_lag {
        let (mut ac, mut e0, mut e1) = (0.0f64, 0.0f64, 0.0f64);
        for i in 0..(n - lag) {
            let a = signal[i] as f64;
            let b = signal[i + lag] as f64;
            ac += a * b;
            e0 += a * a;
            e1 += b * b;
        }
        let norm = if e0 > 0.0 && e1 > 0.0 {
            ac / (e0 * e1).sqrt()
        } else {
            0.0
        };
        corr[lag] = norm;
        if norm > global_max {
            global_max = norm;
        }
    }

    // first local max reaching 85% of the global peak — rejects the
    // octave-down error where a sub-harmonic lag scores highest
    let threshold = 0.85 * global_max;
    let mut best_lag = 0usize;
    for lag in (min_lag + 1)..max_lag {
        if corr[lag] >= threshold && corr[lag] >= corr[lag - 1] && corr[lag] >= corr[lag + 1] {
            best_lag = lag;
            break;
        }
    }
    if best_lag == 0 {
        return Some((0.0, global_max));
    }

    // parabolic interpolation around the peak for sub-sample lag precision
    let (a, b, c) = (corr[best_lag - 1], corr[best_lag], corr[best_lag + 1]);
    let denom = a - 2.0 * b + c;
    let shift = if denom != 0.0 { 0.5 * (a - c) / denom } else { 0.0 };
    let f0 = fs / (best_lag as f64 + shift);
    Some((f0, b))
}

/// Remove the DC offset from `signal` in place and return its RMS.
pub fn remove_dc(signal: &mut [f32]) -> f64 {
    if signal.is_empty() {
        return 0.0;
    }
    let mean = signal.iter().copied().sum::<f32>() / signal.len() as f32;
    for s in signal.iter_mut() {
        *s -= mean;
    }
    (signal.iter().map(|v| (*v as f64).powi(2)).sum::<f64>() / signal.len() as f64).sqrt()
}

/// Zero-crossing-rate frequency estimate — an independent sanity check on
/// `estimate_f0` for clean tones.
pub fn zero_crossing_hz(signal: &[f32], fs: f64) -> f64 {
    let crossings = signal
        .windows(2)
        .filter(|w| (w[0] <= 0.0) != (w[1] <= 0.0))
        .count();
    crossings as f64 * fs / (2.0 * signal.len() as f64)
}

/// Nearest equal-tempered note name (A4 = 440 Hz) and signed cents offset.
pub fn nearest_note(freq: f64) -> (String, f64) {
    const NAMES: [&str; 12] = [
        "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
    ];
    let midi = 69.0 + 12.0 * (freq / 440.0).log2();
    let nearest = midi.round();
    let cents = (midi - nearest) * 100.0;
    let n = nearest as i64;
    let name = format!("{}{}", NAMES[n.rem_euclid(12) as usize], n.div_euclid(12) - 1);
    (name, cents)
}
