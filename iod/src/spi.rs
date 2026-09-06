//! MCP3201 driver over Linux spidev

use std::io;
use std::path::Path;

use spidev::{SpiModeFlags, Spidev, SpidevOptions, spidevioctl::SpidevTransfer};

/// Per the MCP3201 datasheet: a full read is 16 SCLK cycles clocked out as two
/// bytes.
/// Bit layout (MSB first):
/// byte0 = [X, X, NULL(0), B11, B10, B9, B8, B7],
/// byte1 = [B6, B5, B4, B3, B2, B1, B0, B1-repeated]
///
/// One conversion per SPI transfer (CS cycles per transfer, which the MCP3201
/// requires). Batching many conversions into a single `SPI_IOC_MESSAGE` with
/// per-transfer `cs_change` was tried and is *slower* on the Pi 5 / RP1 SPI
/// controller than one syscall per conversion — the per-CS-change overhead
/// dominates. If the resulting sample-rate headroom is ever too thin, move to
/// the kernel `mcp320x` IIO driver with an hrtimer trigger, not userspace
/// batching.
pub struct Mcp3201 {
    spi: Spidev,
}

impl Mcp3201 {
    /// max_speed_hz must respect mcp3201's rated clock
    pub fn open(path: impl AsRef<Path>, max_speed_hz: u32) -> io::Result<Self> {
        let mut spi = Spidev::open(path)?;
        let options = SpidevOptions::new()
            .bits_per_word(8)
            .max_speed_hz(max_speed_hz)
            .mode(SpiModeFlags::SPI_MODE_0)
            .build();
        spi.configure(&options)?;
        Ok(Self { spi })
    }

    /// one conversion
    pub fn read_raw12(&self) -> io::Result<u16> {
        // 2 bytes clocked in per the datasheet's 16-SCLK frame
        let mut rx = [0u8; 2];
        let mut transfer = SpidevTransfer::read(&mut rx);
        self.spi.transfer(&mut transfer)?;
        Ok(decode(rx[0], rx[1]))
    }
}

/// decode the datasheet's two-byte layout into a 12-bit code
fn decode(byte0: u8, byte1: u8) -> u16 {
    // 1. mask off the first 3 bits of byte0 (X, X, NULL)
    // 2. shift left 7 to make room for the low bits
    // 3. drop byte1's trailing repeated bit, then OR the halves together
    ((byte0 & 0x1F) as u16) << 7 | (byte1 >> 1) as u16
}

/// 2048 == 0V signal. Used by the `spi_*` diagnostic examples.
#[allow(dead_code)]
pub fn raw12_to_f32(raw: u16) -> f32 {
    (raw as f32 - 2048.0) / 2048.0
}

/// upscales a 12-bit code to a 16-bit PCM sample for WAV output
pub fn raw12_to_i16(raw: u16) -> i16 {
    (((raw as i32) - 2048) << 4) as i16
}
