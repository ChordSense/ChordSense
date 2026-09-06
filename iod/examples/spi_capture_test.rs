//! End-to-end timing test of the real capture path.
//!
//! Runs the actual `AdcSampler` free-running loop from `capture.rs` (batched
//! SPI reads + wall-clock-locked resampling to `SAMPLE_RATE_HZ`), records a few
//! seconds to a WAV while you feed a steady known tone, then analyzes that WAV:
//!
//!   * whole-file f0        -> is the *average* sample timing correct?
//!   * per-window f0 spread  -> how much does timing jitter wobble the pitch?
//!   * WAV vs wall duration -> did output emission keep pace with real time?
//!
//! Usage (feed a constant tone the whole time):
//!
//!     cd iod
//!     cargo run --release --example spi_capture_test                 # 6 s
//!     cargo run --release --example spi_capture_test -- 449          # expect 449 Hz
//!     cargo run --release --example spi_capture_test -- 449 8 /dev/spidev0.0 1000000

#[path = "../src/spi.rs"]
#[allow(dead_code)]
mod spi;
#[path = "../src/capture.rs"]
#[allow(dead_code)] // this test drives capture via AdcSampler's public API only
mod capture;
#[path = "support/pitch.rs"]
mod pitch;

use std::env;
use std::time::{Duration, Instant};

use capture::{AdcSampler, SAMPLE_RATE_HZ};
use spi::Mcp3201;

const ANALYSIS_WINDOW: usize = 4096;
const ANALYSIS_HOP: usize = 2048;
const F_MIN: f64 = 40.0;
const F_MAX: f64 = 2000.0;

fn main() {
    let mut args = env::args().skip(1);
    let expected_hz: Option<f64> = args.next().and_then(|s| s.parse().ok());
    let record_secs: f64 = args.next().and_then(|s| s.parse().ok()).unwrap_or(6.0);
    let device = args.next().unwrap_or_else(|| "/dev/spidev0.0".to_string());
    let speed_hz: u32 = args.next().and_then(|s| s.parse().ok()).unwrap_or(1_000_000);

    let adc = Mcp3201::open(&device, speed_hz).unwrap_or_else(|err| {
        eprintln!("failed to open {device} @ {speed_hz} Hz: {err}");
        std::process::exit(1);
    });

    let sampler = AdcSampler::spawn(adc);
    println!("sampler thread started on {device} @ {speed_hz} Hz; letting it settle...");
    std::thread::sleep(Duration::from_millis(800));

    let wav_path = env::temp_dir().join(format!(
        "chordsense-capture-test-{}.wav",
        std::process::id()
    ));

    if let Some(hz) = expected_hz {
        println!("\n>>> feed a steady {hz:.2} Hz tone now <<<");
    } else {
        println!("\n>>> feed a steady tone now <<<");
    }
    for n in (1..=3).rev() {
        println!("    recording in {n}...");
        std::thread::sleep(Duration::from_secs(1));
    }

    sampler
        .start_capture(&wav_path)
        .unwrap_or_else(|err| fail(&format!("start_capture: {err}")));

    let wall_start = Instant::now();
    println!("    recording {record_secs:.1} s...");
    std::thread::sleep(Duration::from_secs_f64(record_secs));

    let result = sampler
        .stop_capture()
        .unwrap_or_else(|err| fail(&format!("stop_capture: {err}")));
    let wall_elapsed = wall_start.elapsed().as_secs_f64();
    sampler.shutdown();

    println!("\nwrote {}", result.wav_path.display());
    println!(
        "WAV duration {:.3} s vs wall clock {:.3} s  (delta {:+.1} ms, {:+.2}%)",
        result.duration_s,
        wall_elapsed,
        (result.duration_s - wall_elapsed) * 1000.0,
        (result.duration_s - wall_elapsed) / wall_elapsed * 100.0,
    );

    // ---- read the WAV back ----
    let mut reader = hound::WavReader::open(&result.wav_path)
        .unwrap_or_else(|err| fail(&format!("open wav: {err}")));
    let spec = reader.spec();
    let mut signal: Vec<f32> = reader
        .samples::<i16>()
        .map(|s| s.unwrap_or(0) as f32 / 32768.0)
        .collect();
    println!(
        "read {} samples @ {} Hz declared\n",
        signal.len(),
        spec.sample_rate
    );

    let fs = spec.sample_rate as f64;
    let rms = pitch::remove_dc(&mut signal);
    if rms < 0.003 {
        fail(&format!("signal RMS {rms:.5} — no tone captured, nothing to analyze"));
    }

    // ---- whole-file fundamental ----
    match pitch::estimate_f0(&signal, fs, F_MIN, F_MAX) {
        Some((f0, conf)) if f0 > 0.0 => {
            let (note, cents) = pitch::nearest_note(f0);
            println!("whole-file f0 = {f0:.3} Hz  ({note}{cents:+.0}c, conf {conf:.2}, rms {rms:.3})");
            if let Some(hz) = expected_hz {
                let err_cents = 1200.0 * (f0 / hz).log2();
                println!(
                    "   vs expected {hz:.2} Hz -> {:+.3} Hz  ({err_cents:+.1} cents)",
                    f0 - hz
                );
                println!(
                    "   implied mean sample rate = {:.1} Hz (nominal {SAMPLE_RATE_HZ})",
                    fs * hz / f0
                );
            }
        }
        _ => println!("whole-file f0: no clear pitch — is the tone steady?"),
    }

    // ---- per-window spread (timing jitter) ----
    let mut freqs: Vec<f64> = Vec::new();
    let mut start = 0;
    while start + ANALYSIS_WINDOW <= signal.len() {
        let mut window = signal[start..start + ANALYSIS_WINDOW].to_vec();
        let w_rms = pitch::remove_dc(&mut window);
        if w_rms >= rms * 0.5 {
            if let Some((f0, conf)) = pitch::estimate_f0(&window, fs, F_MIN, F_MAX) {
                if f0 > 0.0 && conf >= 0.7 {
                    freqs.push(f0);
                }
            }
        }
        start += ANALYSIS_HOP;
    }

    if freqs.len() < 3 {
        println!("\nnot enough clean windows for a jitter estimate ({} usable)", freqs.len());
    } else {
        freqs.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let n = freqs.len();
        let median = freqs[n / 2];
        let mean = freqs.iter().sum::<f64>() / n as f64;
        let var = freqs.iter().map(|f| (f - mean).powi(2)).sum::<f64>() / n as f64;
        let std = var.sqrt();
        let min = freqs[0];
        let max = freqs[n - 1];
        let spread_cents = 1200.0 * (max / min).log2();
        println!(
            "\nper-window f0 over {n} windows ({} ms each):",
            (ANALYSIS_WINDOW as f64 / fs * 1000.0).round()
        );
        println!("   median {median:.2} Hz   mean {mean:.2} Hz   std {std:.3} Hz ({:.1} cents)", 1200.0 * (1.0 + std / mean).log2());
        println!("   range  {min:.2} .. {max:.2} Hz   ({spread_cents:.1} cents peak-to-peak)");
        let verdict = if spread_cents < 5.0 {
            "excellent — jitter is not affecting pitch"
        } else if spread_cents < 20.0 {
            "fine for chroma / chord recognition"
        } else if spread_cents < 50.0 {
            "marginal — noticeable wobble, watch for it downstream"
        } else {
            "poor — timing jitter is significantly distorting the signal"
        };
        println!("   -> {verdict}");
    }

    let _ = std::fs::remove_file(&result.wav_path);
}

fn fail(msg: &str) -> ! {
    eprintln!("spi_capture_test: {msg}");
    std::process::exit(1);
}
