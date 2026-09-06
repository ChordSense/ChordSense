mod capture;
mod i2s;
mod protocol;
mod spi;

use std::env;
use std::path::PathBuf;

use capture::AdcSampler;
use i2s::Playback;
use protocol::Daemon;
use spi::Mcp3201;

const DEFAULT_SPI_DEVICE: &str = "/dev/spidev0.0";
/// MCP3201 max clock: confirm against the schematic's actual VDD to this
/// chip before deploying (the datasheet caps this lower at 3.3V than 5V).
const DEFAULT_SPI_MAX_SPEED_HZ: u32 = 1_000_000;

fn main() {
    let socket_path =
        env::var("CHORDSENSE_IOD_SOCKET").unwrap_or_else(|_| default_socket_path());
    let spi_device =
        env::var("CHORDSENSE_SPI_DEVICE").unwrap_or_else(|_| DEFAULT_SPI_DEVICE.to_string());
    let i2s_device_match = env::var("CHORDSENSE_I2S_DEVICE_MATCH").ok();
    let captures_dir = resolve_captures_dir();

    let spi = Mcp3201::open(&spi_device, DEFAULT_SPI_MAX_SPEED_HZ).unwrap_or_else(|err| {
        eprintln!("chordsense-iod: failed to open {spi_device}: {err}");
        std::process::exit(1);
    });
    let sampler = AdcSampler::spawn(spi);

    let playback = Playback::open(i2s_device_match.as_deref()).unwrap_or_else(|err| {
        eprintln!("chordsense-iod: failed to open I2S/DAC output: {err}");
        std::process::exit(1);
    });

    println!(
        "chordsense-iod: SPI sampling from {spi_device} at {} Hz, captures -> {}, listening on {socket_path}",
        capture::SAMPLE_RATE_HZ,
        captures_dir.display(),
    );

    let daemon = Daemon::new(sampler, playback, captures_dir);
    if let Err(err) = daemon.serve(PathBuf::from(&socket_path)) {
        eprintln!("chordsense-iod: socket server failed: {err}");
        std::process::exit(1);
    }
}

/// Where the control socket lives when `CHORDSENSE_IOD_SOCKET` is unset.
///
/// A non-root user can't create `/run/chordsense`, so prefer the per-user
/// runtime dir (`/run/user/<uid>`) when the session provides one. The Python
/// backend's `iod_client.default_socket_path()` resolves the same way, so both
/// sides agree without any env var when run as the same user. A system-wide
/// `systemd` unit sets `CHORDSENSE_IOD_SOCKET` explicitly alongside its
/// `RuntimeDirectory=`.
fn default_socket_path() -> String {
    match env::var("XDG_RUNTIME_DIR") {
        Ok(dir) if !dir.is_empty() => format!("{dir}/chordsense-iod.sock"),
        _ => "/run/chordsense/iod.sock".to_string(),
    }
}

/// Absolute directory that captured WAVs are written into. Absolute so the
/// `wav_path` handed back over the socket resolves regardless of the backend's
/// working directory. Override with `CHORDSENSE_CAPTURES_DIR`; otherwise
/// `runtime/captures` under the current working directory.
fn resolve_captures_dir() -> PathBuf {
    let dir = env::var("CHORDSENSE_CAPTURES_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| capture::default_captures_dir());
    if dir.is_absolute() {
        dir
    } else {
        env::current_dir().map(|cwd| cwd.join(&dir)).unwrap_or(dir)
    }
}
