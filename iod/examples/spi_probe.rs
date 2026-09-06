//! Standalone ADC signal probe: opens the MCP3201 over spidev, samples as fast
//! as it can in short bursts, and prints per-burst statistics so you can see
//! whether a real signal is coming through the analog front-end.
//!
//! Run on the Pi (after `dtparam=spi=on` is enabled and /dev/spidev0.0 exists):
//!
//!     cd iod
//!     cargo run --example spi_probe                 # /dev/spidev0.0 @ 1 MHz
//!     cargo run --example spi_probe -- /dev/spidev0.0 500000
//!
//! Then strum the guitar / inject a tone and watch pk-pk and RMS jump.

#[path = "../src/spi.rs"]
#[allow(dead_code)] // this probe only uses part of the driver
mod spi;

use std::env;
use std::time::{Duration, Instant};

use spi::{Mcp3201, raw12_to_f32};

/// samples per printed line (~0.1 s worth of audio at 22.05 kHz-ish)
const BURST: usize = 4096;

fn main() {
    let mut args = env::args().skip(1);
    let device = args.next().unwrap_or_else(|| "/dev/spidev0.0".to_string());
    let speed_hz: u32 = args
        .next()
        .and_then(|s| s.parse().ok())
        .unwrap_or(1_000_000);

    let adc = match Mcp3201::open(&device, speed_hz) {
        Ok(adc) => adc,
        Err(err) => {
            eprintln!("failed to open {device} @ {speed_hz} Hz: {err}");
            eprintln!("  - is SPI enabled? (`dtparam=spi=on` in /boot/firmware/config.txt, then reboot)");
            eprintln!("  - does the node exist? (`ls -l /dev/spidev*`)");
            std::process::exit(1);
        }
    };

    println!("probing {device} @ {speed_hz} Hz -- Ctrl-C to stop");
    println!(
        "{:>8}  {:>6}  {:>6}  {:>6}  {:>7}  {:>7}  {:>6}",
        "code_min", "max", "mean", "pk-pk", "rms", "kS/s", "signal"
    );

    loop {
        let start = Instant::now();
        let mut min = u16::MAX;
        let mut max = 0u16;
        let mut sum = 0.0f64;
        let mut sum_sq = 0.0f64;
        let mut errors = 0usize;

        for _ in 0..BURST {
            match adc.read_raw12() {
                Ok(code) => {
                    min = min.min(code);
                    max = max.max(code);
                    let v = raw12_to_f32(code) as f64;
                    sum += v;
                    sum_sq += v * v;
                }
                Err(_) => errors += 1,
            }
        }

        let elapsed = start.elapsed();
        let good = (BURST - errors) as f64;
        if good == 0.0 {
            eprintln!("all {BURST} reads failed -- check wiring / spidev permissions");
            std::thread::sleep(Duration::from_millis(500));
            continue;
        }

        let mean = sum / good;
        let rms = (sum_sq / good - mean * mean).max(0.0).sqrt();
        let pkpk = max.saturating_sub(min);
        let ksps = good / elapsed.as_secs_f64() / 1000.0;

        // pk-pk over ~40 codes (~1% of full scale) is well above ADC noise
        let verdict = if errors > BURST / 2 {
            "READ ERR"
        } else if pkpk >= 40 {
            "YES"
        } else {
            "flat"
        };

        println!(
            "{min:>8}  {max:>6}  {mean_code:>6}  {pkpk:>6}  {rms:>7.4}  {ksps:>7.1}  {verdict:>6}",
            mean_code = ((mean * 2048.0) + 2048.0) as i32,
        );

        std::thread::sleep(Duration::from_millis(400));
    }
}
