//! PCM5102A playback driver

use std::fs::File;
use std::path::{Path, PathBuf};

use rodio::cpal::traits::{DeviceTrait, HostTrait};
use rodio::stream::{DeviceSinkBuilder, MixerDeviceSink};
use rodio::{Decoder, Device, Player, Source};

pub struct Playback {
    sink: MixerDeviceSink,
    player: Player,
    device_name: String,
    current_path: Option<PathBuf>,
    duration_secs: Option<f64>,
    volume: f32,
}

/// an output device plus the ALSA pcm id (e.g. `plughw:CARD=sndrpihifiberry,DEV=0`)
/// and human-readable card description it was found under
pub struct OutputDevice {
    pub device: Device,
    pub pcm_id: String,
    pub description: String,
}

/// every output ALSA exposes. each card shows up several times (hw, plughw,
/// sysdefault, dmix, ...) under the same description; the pcm id tells them apart.
pub fn list_outputs() -> Result<Vec<OutputDevice>, String> {
    let devices = rodio::cpal::default_host()
        .output_devices()
        .map_err(|e| e.to_string())?;
    Ok(devices
        .map(|device| {
            let pcm_id = device.id().map(|id| id.1).unwrap_or_default();
            let description = device
                .description()
                .map(|d| d.name().to_string())
                .unwrap_or_default();
            OutputDevice { device, pcm_id, description }
        })
        .collect())
}

/// finds the output whose pcm id or description contains `needle`
/// (case-insensitive), preferring the card's `plughw:` pcm: it talks to the
/// card directly (no desktop mixer in between) but still converts sample
/// format/rate/channels, so any file plays.
///
/// no fallback: if the DAC was asked for and isn't there, playing through HDMI
/// instead would just mean silent headphones, so report it instead.
pub fn find_output(needle: &str) -> Result<OutputDevice, String> {
    let needle = needle.to_lowercase();
    let outputs = list_outputs()?;
    let available: Vec<String> = outputs.iter().map(|o| o.pcm_id.clone()).collect();
    let mut matches: Vec<OutputDevice> = outputs
        .into_iter()
        .filter(|o| {
            o.pcm_id.to_lowercase().contains(&needle)
                || o.description.to_lowercase().contains(&needle)
        })
        .collect();
    if matches.is_empty() {
        return Err(format!(
            "no audio output matching '{needle}' (is the I2S dtoverlay enabled? check `aplay -l`); available: {}",
            available.join(", ")
        ));
    }
    let preferred = matches
        .iter()
        .position(|o| o.pcm_id.starts_with("plughw:"))
        .unwrap_or(0);
    Ok(matches.swap_remove(preferred))
}

impl Playback {
    /// device_name_contains is a substring of the ALSA output's pcm id or card
    /// name, e.g. "hifiberry" for the PCM5102A under dtoverlay=hifiberry-dac.
    /// None uses the system default output.
    pub fn open(device_name_contains: Option<&str>) -> Result<Self, String> {
        let (device_name, builder) = match device_name_contains {
            Some(needle) => {
                let output = find_output(needle)?;
                let builder = DeviceSinkBuilder::from_device(output.device)
                    .map_err(|e| format!("'{}': {e}", output.pcm_id))?;
                (output.pcm_id, builder)
            }
            None => {
                eprintln!(
                    "chordsense-iod: CHORDSENSE_I2S_DEVICE_MATCH unset; using the default output, not the headphone jack"
                );
                let builder = DeviceSinkBuilder::from_default_device().map_err(|e| e.to_string())?;
                ("default".to_string(), builder)
            }
        };
        let sink = builder
            .open_stream()
            .map_err(|e| format!("failed to open '{device_name}': {e}"))?;

        let player = Player::connect_new(&sink.mixer());
        Ok(Self {
            sink,
            player,
            device_name,
            current_path: None,
            duration_secs: None,
            volume: 0.8,
        })
    }

    pub fn device_name(&self) -> &str {
        &self.device_name
    }

    /// opens an audio file, decodes it, queues the audio onto a fresh player,
    /// starts it paused, applies current volume
    pub fn load(&mut self, path: impl AsRef<Path>) -> Result<(), String> {
        let path = path.as_ref().to_path_buf();
        let player = Player::connect_new(&self.sink.mixer());
        let file = File::open(&path).map_err(|e| e.to_string())?;
        let decoder = Decoder::try_from(file).map_err(|e| e.to_string())?;
        self.duration_secs = decoder.total_duration().map(|d| d.as_secs_f64());
        player.append(decoder);
        player.pause();
        player.set_volume(self.volume);
        self.player = player;
        self.current_path = Some(path);
        Ok(())
    }

    /// rodio drops a source from the queue once it has played out, so after
    /// the end of a track the player is empty; reload it (paused, at 0) so
    /// play/seek work again like they would on a media player.
    fn reload_if_finished(&mut self) -> Result<(), String> {
        if self.player.empty() {
            if let Some(path) = self.current_path.clone() {
                self.load(path)?;
            }
        }
        Ok(())
    }

    pub fn play(&mut self) -> Result<(), String> {
        self.reload_if_finished()?;
        self.player.play();
        Ok(())
    }

    pub fn pause(&self) {
        self.player.pause();
    }

    /// back to the start, paused
    pub fn stop(&mut self) -> Result<(), String> {
        self.player.stop();
        if let Some(path) = self.current_path.clone() {
            self.load(path)?;
        }
        Ok(())
    }

    /// seek to some position in the player
    pub fn seek(&mut self, position_secs: f64) -> Result<(), String> {
        self.reload_if_finished()?;
        self.player
            .try_seek(std::time::Duration::from_secs_f64(position_secs.max(0.0)))
            .map_err(|e| e.to_string())
    }

    pub fn set_volume(&mut self, volume: f32) {
        self.volume = volume;
        self.player.set_volume(volume);
    }

    pub fn position_secs(&self) -> f64 {
        self.player.get_pos().as_secs_f64()
    }

    pub fn is_paused(&self) -> bool {
        self.player.is_paused()
    }

    /// a track was loaded and has played to its end
    pub fn is_finished(&self) -> bool {
        self.current_path.is_some() && self.player.empty()
    }

    pub fn current_path(&self) -> Option<&Path> {
        self.current_path.as_deref()
    }

    pub fn duration_secs(&self) -> Option<f64> {
        self.duration_secs
    }
}
