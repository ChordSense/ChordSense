//! MCP3201 driver over Linux spidev

use std::io;
use std::path::Path;

use spidev::{SpiModeFlags, Spidev, SpidevOptions, spidevioctl::SpidevTransfer};

/// Per the MCP3201 datasheet: a full read is 16 SCLK cycles clocked out as two
/// bytes. 
/// Bit layout (MSB first): 
/// byte0 = [X, X, NULL(0), B11, B10, B9, B8, B7],
/// byte1 = [B6, B5, B4, B3, B2, B1, B0, B1-repeated]
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
        /// allocate 2 bytes for recieving
        let mut rx = [0u8; 2];
        let mut transfer = SpidevTransfer::read(&mut rx);
        self.spi.transfer(&mut transfer)?;
        
        /// 1. mask off first 3 bits of first byte
        /// 2. shift left 7 bits to make room for other data
        /// 3. get rid of final junk bit and or the values
        let raw = ((rx[0] & 0x1F) as u16) << 7 | (rx[1] >> 1) as u16;
        Ok(raw)
    }
}

/// 2048 == 0V signal
pub fn raw12_to_f32(raw: u16) -> f32 {
    (raw as f32 - 2048.0) / 2048.0
}

/// upscales a 12-bit code to a 16-bit PCM sample for WAV output
pub fn raw12_to_i16(raw: u16) -> i16 {
    (((raw as i32) - 2048) << 4) as i16
}
