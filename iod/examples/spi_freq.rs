//! ADC frequency probe: samples the MCP3201 as fast as spidev allows, measures
//! the *actual* sample rate achieved, and estimates the fundamental frequency
//! of whatever is on the analog front-end (autocorrelation pitch detection,
//! with a zero-crossing cross-check).
//!
//! Play a known pitch (a tuner app, a single plucked string, a signal
//! generator) and confirm the reported f0 matches.
//!
//!     cd iod
//!     cargo run --release --example spi_freq
//!     cargo run --release --example spi_freq -- /dev/spidev0.0 1000000
//!
//! --release matters: the autocorrelation is O(window * max_lag) per frame.

#[path = "../src/spi.rs"]
#[allow(dead_code)] // this probe only uses part of the driver
mod spi;
#[path = "support/pitch.rs"]
mod pitch;

use std::env;
use std::time::{Duration, Instant};

use spi::{Mcp3201, raw12_to_f32};

const WINDOW: usize = 8192;
const F_MIN: f64 = 40.0; // guitars start ~82 Hz; leave room for detuned / bass
const F_MAX: f64 = 1500.0;
const MIN_CONFIDENCE: f64 = 0.5;

fn main() {
    let mut args = env::args().skip(1);
    let device = args.next().unwrap_or_else(|| "/dev/spidev0.0".to_string());
    let speed_hz: u32 = args.next().and_then(|s| s.parse().ok()).unwrap_or(1_000_000);

    let adc = Mcp3201::open(&device, speed_hz).unwrap_or_else(|err| {
        eprintln!("failed to open {device} @ {speed_hz} Hz: {err}");
        std::process::exit(1);
    });

    println!("device {device} @ {speed_hz} Hz SPI clock, {WINDOW}-sample window -- Ctrl-C to stop");

    let mut buf = vec![0.0f32; WINDOW];
    let mut last_fs = 0.0f64;

    loop {
        // fill the window, timing it to recover the real sample rate
        let start = Instant::now();
        let mut errors = 0usize;
        for s in buf.iter_mut() {
            match adc.read_raw12() {
                Ok(code) => *s = raw12_to_f32(code),
                Err(_) => {
                    *s = 0.0;
                    errors += 1;
                }
            }
        }
        let fs = WINDOW as f64 / start.elapsed().as_secs_f64();
        let rms = pitch::remove_dc(&mut buf);

        let fs_note = if last_fs > 0.0 && (fs - last_fs).abs() / last_fs > 0.05 {
            "  (!) sample rate unstable"
        } else {
            ""
        };
        last_fs = fs;

        if errors > WINDOW / 100 {
            println!("fs~{fs:7.0} Hz   {errors} read errors -- check wiring{fs_note}");
            std::thread::sleep(Duration::from_millis(300));
            continue;
        }
        if rms < 0.002 {
            println!("fs~{fs:7.0} Hz   signal too weak (rms {rms:.5}){fs_note}");
            std::thread::sleep(Duration::from_millis(300));
            continue;
        }

        match pitch::estimate_f0(&buf, fs, F_MIN, F_MAX) {
            None => {
                println!("fs~{fs:7.0} Hz   sample rate too low to resolve {F_MIN}-{F_MAX} Hz{fs_note}");
            }
            Some((f0, _)) if f0 <= 0.0 => {
                println!("fs~{fs:7.0} Hz   no clear pitch (rms {rms:.3}){fs_note}");
            }
            Some((f0, confidence)) => {
                let zc = pitch::zero_crossing_hz(&buf, fs);
                let (note, cents) = pitch::nearest_note(f0);
                let tag = if confidence < MIN_CONFIDENCE { "  LOW conf" } else { "" };
                println!(
                    "fs~{fs:7.0} Hz   f0={f0:8.2} Hz  {note:>4}{cents:+4.0}c   \
                     conf {confidence:.2}  zc~{zc:.0} Hz  rms {rms:.3}{tag}{fs_note}"
                );
            }
        }

        std::thread::sleep(Duration::from_millis(200));
    }
}
