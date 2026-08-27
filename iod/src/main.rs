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

const DEFAULT_SOCKET_PATH: &str = "/run/chordsense/iod.sock";
const DEFAULT_SPI_DEVICE: &str = "/dev/spidev0.0";
/// MCP3201 max clock: confirm against the schematic's actual VDD to this
/// chip before deploying (the datasheet caps this lower at 3.3V than 5V).
const DEFAULT_SPI_MAX_SPEED_HZ: u32 = 1_000_000;

fn main() {
    let socket_path =
        env::var("CHORDSENSE_IOD_SOCKET").unwrap_or_else(|_| DEFAULT_SOCKET_PATH.to_string());
    let spi_device =
        env::var("CHORDSENSE_SPI_DEVICE").unwrap_or_else(|_| DEFAULT_SPI_DEVICE.to_string());
    let i2s_device_match = env::var("CHORDSENSE_I2S_DEVICE_MATCH").ok();

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
        "chordsense-iod: SPI sampling from {spi_device} at {} Hz, listening on {socket_path}",
        capture::SAMPLE_RATE_HZ
    );

    let daemon = Daemon::new(sampler, playback);
    if let Err(err) = daemon.serve(PathBuf::from(&socket_path)) {
        eprintln!("chordsense-iod: socket server failed: {err}");
        std::process::exit(1);
    }
}
