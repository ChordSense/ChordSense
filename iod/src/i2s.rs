//! PCM5102A playback driver

use std::fs::File;
use std::path::{Path, PathBuf};

use rodio::speakers::{Output, SpeakersBuilder, available_outputs};
use rodio::stream::MixerDeviceSink;
use rodio::{Decoder, Player, Source};

pub struct Playback {
    sink: MixerDeviceSink,
    player: Player,
    current_path: Option<PathBuf>,
    duration_secs: Option<f64>,
    volume: f32,
}

impl Playback {
    /// device_name_contains is a substring of the ALSA device we want to use
    /// this will eventually be the PCM5102A once its set up to show up when running aplay -l
    pub fn open(device_name_contains: Option<&str>) -> Result<Self, String> {
        let mut builder_device = None;
        if let Some(needle) = device_name_contains {
            let needle = needle.to_lowercase();
            let outputs: Vec<Output> = available_outputs().map_err(|e| e.to_string())?;
            builder_device = outputs
                .into_iter()
                .find(|o| o.to_string().to_lowercase().contains(&needle));
            if builder_device.is_none() {
                eprintln!(
                    "chordsense-iod: no output device matching '{needle}' found; falling back to default output"
                );
            }
        }

        let builder = SpeakersBuilder::new();
        let sink = match builder_device {
            Some(device) => builder
                .device(device)
                .map_err(|e| e.to_string())?
                .default_config()
                .map_err(|e| e.to_string())?
                .open_mixer()
                .map_err(|e| e.to_string())?,
            None => builder
                .default_device()
                .map_err(|e| e.to_string())?
                .default_config()
                .map_err(|e| e.to_string())?
                .open_mixer()
                .map_err(|e| e.to_string())?,
        };

        let player = Player::connect_new(&sink.mixer());
        Ok(Self { sink, player, current_path: None, duration_secs: None, volume: 0.8 })
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

    /// control methods to be used at some point
    pub fn play(&self) {
        self.player.play();
    }

    pub fn pause(&self) {
        self.player.pause();
    }

    pub fn stop(&mut self) -> Result<(), String> {
        self.player.stop();
        if let Some(path) = self.current_path.clone() {
            self.load(path)?;
        }
        Ok(())
    }

    /// seek to some position in the player
    pub fn seek(&mut self, position_secs: f64) -> Result<(), String> {
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

    pub fn is_finished(&self) -> bool {
        self.player.empty()
    }

    pub fn duration_secs(&self) -> Option<f64> {
        self.duration_secs
    }
}
