//! Lists the audio outputs iod can see, and optionally plays a test tone
//! through one, to check the PCM5102A DAC is reachable before running the
//! daemon.
//!
//!     cd iod
//!     cargo run --release --example audio_outputs                # list outputs
//!     cargo run --release --example audio_outputs -- hifiberry   # 440 Hz for 3 s on the match
//!
//! The substring you pass is what goes in CHORDSENSE_I2S_DEVICE_MATCH. Stop
//! chordsense-iod first: it holds the DAC open while running.

#[path = "../src/i2s.rs"]
#[allow(dead_code)] // only device selection is used here
mod i2s;

use std::env;
use std::time::Duration;

use rodio::source::{SineWave, Source};
use rodio::stream::DeviceSinkBuilder;
use rodio::Player;

fn main() -> Result<(), String> {
    println!("audio outputs (ALSA pcm id  |  card):");
    for output in i2s::list_outputs()? {
        println!("  {:<45} | {}", output.pcm_id, output.description);
    }

    let Some(needle) = env::args().nth(1) else {
        return Ok(());
    };

    let output = i2s::find_output(&needle)?;
    println!("\nplaying 440 Hz for 3 s on: {}", output.pcm_id);
    let sink = DeviceSinkBuilder::from_device(output.device)
        .and_then(|b| b.open_stream())
        .map_err(|e| e.to_string())?;
    let config = sink.config();
    println!("  {} Hz, {} ch, {:?}", config.sample_rate(), config.channel_count(), config.sample_format());
    let player = Player::connect_new(sink.mixer());
    player.set_volume(0.3);
    player.append(SineWave::new(440.0).take_duration(Duration::from_secs(3)));
    player.sleep_until_end();
    Ok(())
}
