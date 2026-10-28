//! Native desktop audio. Pi builds retain the iod backend instead.
#![cfg(any(target_os = "windows", target_os = "macos"))]

use crate::services::{client, Event, BACKEND_URL};
use rodio::cpal::{
    self,
    traits::{DeviceTrait, HostTrait},
    DeviceId,
};
use rodio::mixer::Mixer;
use rodio::{Decoder, DeviceSinkBuilder, MixerDeviceSink, Player, Source};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc, Arc,
};
use std::time::{Duration, Instant};

struct AudioCommand {
    tag: String,
    generation: u64,
    action: String,
    body: Value,
    shutdown_ack: Option<mpsc::Sender<bool>>,
}
struct RenderResult {
    command: AudioCommand,
    revision: u64,
    speed: f64,
    result: Result<PathBuf, String>,
}
struct RenderTask {
    cancel: Arc<AtomicBool>,
    handle: std::thread::JoinHandle<()>,
}

pub struct AudioWorker {
    tx: mpsc::Sender<AudioCommand>,
    handle: Option<std::thread::JoinHandle<()>>,
}
impl AudioWorker {
    pub fn new(events: mpsc::Sender<Event>) -> Self {
        let (tx, rx) = mpsc::channel::<AudioCommand>();
        let handle = std::thread::spawn(move || {
            let mut audio = DesktopAudio::default();
            let (render_tx, render_rx) = mpsc::channel::<RenderResult>();
            let mut render: Option<RenderTask> = None;
            let mut pending: Option<(AudioCommand, f64, u64)> = None;
            let mut revision = 0_u64;
            let mut output_check = Instant::now();
            let mut output_error = None;
            loop {
                match rx.recv_timeout(Duration::from_millis(20)) {
                    Ok(command) => {
                        if let Some(ack) = &command.shutdown_ack {
                            drop(audio);
                            let mut clean = true;
                            if let Some(task) = render.take() {
                                task.cancel.store(true, Ordering::SeqCst);
                                clean = render_rx.recv_timeout(Duration::from_secs(2)).is_ok();
                                if clean || task.handle.is_finished() {
                                    let _ = task.handle.join();
                                }
                            }
                            let _ = ack.send(clean);
                            break;
                        }
                        if command.action == "load" || command.action == "speed" {
                            revision += 1;
                            pending = None;
                            if let Some(task) = &render {
                                task.cancel.store(true, Ordering::SeqCst);
                            }
                        }
                        if command.action == "speed" {
                            let speed = audio
                                .require_loaded()
                                .and_then(|_| finite_range(&command.body, "speed", 0.5, 1.0));
                            match speed {
                                Ok(speed)
                                    if (speed - 1.0).abs() > 0.00001
                                        && !audio.rendered.contains_key(&speed_key(speed)) =>
                                {
                                    pending = Some((command, speed, revision));
                                }
                                Ok(_) => {
                                    let result =
                                        audio.command(&command.action, command.body.clone());
                                    send_audio_event(&events, command, result);
                                }
                                Err(error) => send_audio_event(&events, command, Err(error)),
                            }
                        } else {
                            let result = audio.command(&command.action, command.body.clone());
                            send_audio_event(&events, command, result);
                        }
                    }
                    Err(mpsc::RecvTimeoutError::Timeout) => {}
                    Err(mpsc::RecvTimeoutError::Disconnected) => {
                        if let Some(task) = render {
                            task.cancel.store(true, Ordering::SeqCst);
                        }
                        break;
                    }
                }
                // Keep device discovery off the UI/audio callback threads. Check even
                // while paused, and retry transient failures without losing the song.
                if output_check.elapsed() >= Duration::from_millis(500) {
                    output_check = Instant::now();
                    match audio.follow_default_output() {
                        Ok(()) => output_error = None,
                        Err(error) => {
                            if output_error.as_ref() != Some(&error) {
                                eprintln!("Audio output change: {error}; will retry");
                            }
                            output_error = Some(error);
                        }
                    }
                }
                while let Ok(result) = render_rx.try_recv() {
                    if let Some(task) = render.take() {
                        let _ = task.handle.join();
                    }
                    // A load or a newer tempo request invalidates an older render.
                    if result.revision == revision {
                        let committed = result.result.and_then(|path| {
                            audio.rendered.insert(speed_key(result.speed), path);
                            audio.command("speed", json!({"speed":result.speed}))
                        });
                        send_audio_event(&events, result.command, committed);
                    }
                }
                if render.is_none() {
                    if let Some((command, speed, request_revision)) = pending.take() {
                        let original = audio.original.clone().expect("validated loaded track");
                        let files = audio.files.clone().expect("loaded track has working files");
                        let cancel = Arc::new(AtomicBool::new(false));
                        let cancelled = cancel.clone();
                        let result_tx = render_tx.clone();
                        let handle = std::thread::spawn(move || {
                            let result = render_speed(&original, &files, speed, &cancelled);
                            let _ = result_tx.send(RenderResult {
                                command,
                                speed,
                                revision: request_revision,
                                result,
                            });
                        });
                        render = Some(RenderTask { cancel, handle });
                    }
                }
            }
        });
        Self {
            tx,
            handle: Some(handle),
        }
    }
    pub fn command(&self, tag: &str, generation: u64, action: &str, body: Value) {
        let _ = self.tx.send(AudioCommand {
            tag: tag.into(),
            generation,
            action: action.into(),
            body,
            shutdown_ack: None,
        });
    }
    pub fn shutdown(&mut self) -> bool {
        if self.handle.is_none() {
            return true;
        }
        let (ack, done) = mpsc::channel();
        let _ = self.tx.send(AudioCommand {
            tag: String::new(),
            generation: 0,
            action: String::new(),
            body: Value::Null,
            shutdown_ack: Some(ack),
        });
        let result = done.recv_timeout(Duration::from_secs(3));
        if result.is_ok() {
            if let Some(handle) = self.handle.take() {
                let _ = handle.join();
            }
        }
        result == Ok(true)
    }
}
fn send_audio_event(
    events: &mpsc::Sender<Event>,
    command: AudioCommand,
    result: Result<Value, String>,
) {
    let _ = events.send(Event {
        tag: command.tag,
        generation: command.generation,
        result,
    });
}

struct DesktopOutput {
    stream: MixerDeviceSink,
    id: DeviceId,
    failed: Arc<AtomicBool>,
}

impl DesktopOutput {
    fn default_device() -> Result<(cpal::Device, DeviceId), String> {
        let device = cpal::default_host()
            .default_output_device()
            .ok_or("No default audio output is available")?;
        let id = device
            .id()
            .map_err(|e| format!("Could not identify audio output: {e}"))?;
        Ok((device, id))
    }

    fn open(device: cpal::Device, id: DeviceId) -> Result<Self, String> {
        let failed = Arc::new(AtomicBool::new(false));
        let failure = failed.clone();
        let stream = DeviceSinkBuilder::from_device(device)
            .map_err(|e| format!("Could not configure audio output: {e}"))?
            .with_error_callback(move |_error| {
                failure.store(true, Ordering::SeqCst);
            })
            // Try alternative formats on this device, never an unrelated speaker.
            .open_sink_or_fallback()
            .map_err(|e| format!("Could not open audio output: {e}"))?;
        Ok(Self { stream, id, failed })
    }
}

struct DesktopAudio {
    // Drop the player before releasing the operating system stream and files.
    player: Option<Player>,
    stream: Option<DesktopOutput>,
    files: Option<Arc<tempfile::TempDir>>,
    original: Option<PathBuf>,
    rendered: HashMap<u32, PathBuf>,
    duration: f64,
    speed: f64,
    volume: f32,
    base_position: f64,
    started: bool,
}

impl Default for DesktopAudio {
    fn default() -> Self {
        Self {
            player: None,
            stream: None,
            files: None,
            original: None,
            rendered: HashMap::new(),
            duration: 0.0,
            speed: 1.0,
            volume: 0.8,
            base_position: 0.0,
            started: false,
        }
    }
}

impl DesktopAudio {
    fn follow_default_output(&mut self) -> Result<(), String> {
        let Some(output) = &self.stream else {
            return Ok(());
        };
        let (device, id) = DesktopOutput::default_device()?;
        if output.id == id && !output.failed.load(Ordering::SeqCst) {
            return Ok(());
        }
        // Opening can take time. Leave the old output playing until the new one
        // is ready, then sample the position and transfer the player atomically.
        let replacement = DesktopOutput::open(device, id)?;
        self.handoff_to_mixer(replacement.stream.mixer())?;
        self.stream = Some(replacement);
        eprintln!("Audio output switched to the system default");
        Ok(())
    }

    fn handoff_to_mixer(&mut self, mixer: &Mixer) -> Result<(), String> {
        let paused = self.paused();
        if let Some(player) = &self.player {
            player.pause();
        }
        let position = self.position();
        let player = match self.player_for_mixer(mixer, position) {
            Ok(player) => player,
            Err(error) => {
                if !paused {
                    if let Some(player) = &self.player {
                        player.play();
                    }
                }
                return Err(error);
            }
        };
        self.player = Some(player);
        self.base_position = position;
        if !paused {
            self.player.as_ref().unwrap().play();
        }
        Ok(())
    }

    fn command(&mut self, action: &str, body: Value) -> Result<Value, String> {
        match action {
            "load" => self.load(body)?,
            "status" => {}
            "volume" => {
                self.volume = finite_range(&body, "volume", 0.0, 1.0)? as f32;
                if let Some(player) = &self.player {
                    player.set_volume(self.volume);
                }
            }
            "play" => {
                self.require_loaded()?;
                if self.finished() {
                    self.install(0.0, false)?;
                }
                if let Some(player) = &self.player {
                    player.play();
                }
                self.started = true;
            }
            "pause" => {
                if let Some(player) = &self.player {
                    player.pause();
                }
            }
            "stop" => {
                if self.original.is_some() {
                    self.install(0.0, true)?;
                }
                self.started = false;
            }
            "seek" => {
                self.require_loaded()?;
                let position = body["position_secs"]
                    .as_f64()
                    .filter(|x| x.is_finite())
                    .ok_or("Seek position must be finite")?;
                let paused = self.paused();
                self.install(position.clamp(0.0, self.duration), paused)?;
            }
            "speed" => {
                self.require_loaded()?;
                let speed = finite_range(&body, "speed", 0.5, 1.0)?;
                if (speed - self.speed).abs() > 0.00001 {
                    if (speed - 1.0).abs() > 0.00001
                        && !self.rendered.contains_key(&speed_key(speed))
                    {
                        return Err("Playback tempo is still being prepared".into());
                    }
                    // Rendering runs on this worker; playback continues on the
                    // audio callback. Sample position only after rendering ends.
                    let position = self.position();
                    let paused = self.paused();
                    let previous = self.speed;
                    self.speed = speed;
                    if let Err(error) = self.install(position, paused) {
                        self.speed = previous;
                        return Err(error);
                    }
                }
            }
            _ => return Err(format!("Unknown playback action: {action}")),
        }
        Ok(self.status())
    }

    fn require_loaded(&self) -> Result<(), String> {
        if self.original.is_none() {
            Err("Load an audio track first".into())
        } else {
            Ok(())
        }
    }

    fn load(&mut self, body: Value) -> Result<(), String> {
        let files = tempfile::Builder::new()
            .prefix("chordsense-audio-")
            .tempdir()
            .map_err(|e| e.to_string())?;
        let path = if let Some(path) = body["path"].as_str() {
            let path = PathBuf::from(path);
            if !path.is_file() {
                return Err(format!("Audio file not found: {}", path.display()));
            }
            path
        } else if let Some(upload) = body["upload"].as_str() {
            let mut url = reqwest::Url::parse(BACKEND_URL).map_err(|e| e.to_string())?;
            url.path_segments_mut()
                .map_err(|_| "Invalid backend URL")?
                .extend(["api", "uploads", upload]);
            let mut response = client(120)?
                .get(url)
                .send()
                .map_err(|e| format!("Could not load uploaded audio: {e}"))?
                .error_for_status()
                .map_err(|e| format!("Could not load uploaded audio: {e}"))?;
            let extension = Path::new(upload)
                .extension()
                .and_then(|s| s.to_str())
                .unwrap_or("audio");
            let path = files.path().join(format!("source.{extension}"));
            let mut output = File::create(&path).map_err(|e| e.to_string())?;
            std::io::copy(&mut response, &mut output).map_err(|e| e.to_string())?;
            path
        } else {
            return Err("Provide a local path or uploaded filename".into());
        };
        let decoder = decode(&path)?;
        let duration = match decoder.total_duration() {
            Some(duration) => duration.as_secs_f64(),
            None => {
                let rate = decoder.sample_rate().get() as f64;
                let channels = decoder.channels().get() as f64;
                decoder.count() as f64 / (rate * channels)
            }
        };
        if !duration.is_finite() || duration <= 0.0 {
            return Err("Audio file contains no playable samples".into());
        }
        if self.stream.is_none() {
            let (device, id) = DesktopOutput::default_device()?;
            self.stream = Some(DesktopOutput::open(device, id)?);
        }
        self.player = None;
        self.files = Some(Arc::new(files));
        self.original = Some(path);
        self.rendered.clear();
        self.duration = duration;
        self.speed = 1.0;
        self.base_position = 0.0;
        self.started = false;
        self.install(0.0, true)
    }

    fn install(&mut self, position: f64, paused: bool) -> Result<(), String> {
        let stream = self.stream.as_ref().ok_or("Audio output is unavailable")?;
        let player = self.player_for_mixer(stream.stream.mixer(), position)?;
        self.player = Some(player);
        self.base_position = position;
        if !paused {
            self.player.as_ref().unwrap().play();
            self.started = true;
        }
        Ok(())
    }

    fn player_for_mixer(&self, mixer: &Mixer, position: f64) -> Result<Player, String> {
        let source = if (self.speed - 1.0).abs() < 0.00001 {
            self.original.as_ref().ok_or("Load an audio track first")?
        } else {
            self.rendered
                .get(&speed_key(self.speed))
                .ok_or("Playback speed is not prepared")?
        };
        let player = Player::connect_new(mixer);
        player.pause();
        player.set_volume(self.volume);
        // An already completed song must remain completed after a device switch.
        if position >= self.duration {
            return Ok(player);
        }
        let mut decoder = decode(source)?;
        if position > 0.0 {
            decoder
                .try_seek(Duration::from_secs_f64(position / self.speed))
                .map_err(|e| format!("Could not seek audio: {e}"))?;
        }
        player.append(decoder);
        Ok(player)
    }

    fn paused(&self) -> bool {
        self.player.as_ref().is_none_or(Player::is_paused)
    }
    fn finished(&self) -> bool {
        self.started && self.player.as_ref().is_some_and(Player::empty)
    }
    fn position(&self) -> f64 {
        if self.finished() {
            return self.duration;
        }
        (self.base_position
            + self
                .player
                .as_ref()
                .map(|p| p.get_pos().as_secs_f64() * self.speed)
                .unwrap_or(0.0))
        .clamp(0.0, self.duration)
    }
    fn status(&self) -> Value {
        json!({"success":true,"playing":!self.paused() && !self.finished(),
            "paused":self.paused(),"finished":self.finished(),"position":self.position(),
            "duration":self.duration,"speed":self.speed,"volume":self.volume})
    }
}

fn decode(path: &Path) -> Result<Decoder<std::io::BufReader<File>>, String> {
    Decoder::try_from(File::open(path).map_err(|e| format!("Could not open audio: {e}"))?)
        .map_err(|e| format!("Could not decode audio: {e}"))
}

fn speed_key(speed: f64) -> u32 {
    (speed * 1_000_000.0).round() as u32
}

fn finite_range(body: &Value, field: &str, min: f64, max: f64) -> Result<f64, String> {
    body[field]
        .as_f64()
        .filter(|v| v.is_finite() && *v >= min && *v <= max)
        .ok_or_else(|| format!("{field} must be from {min} to {max}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    // Drive real Rodio players through in-memory mixers: these exercise the
    // handoff without opening a speaker or modifying the user's sound settings.
    fn output_fixture() -> (
        tempfile::NamedTempFile,
        DesktopAudio,
        rodio::mixer::MixerSource,
    ) {
        use std::io::Write;
        let mut wav = tempfile::Builder::new().suffix(".wav").tempfile().unwrap();
        let data_bytes = 16_000_u32;
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&16_u32.to_le_bytes());
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&8_000_u32.to_le_bytes());
        bytes.extend_from_slice(&16_000_u32.to_le_bytes());
        bytes.extend_from_slice(&2_u16.to_le_bytes());
        bytes.extend_from_slice(&16_u16.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&data_bytes.to_le_bytes());
        for i in 0..8_000 {
            bytes.extend_from_slice(&((i % 100) as i16 * 100).to_le_bytes());
        }
        wav.write_all(&bytes).unwrap();
        wav.flush().unwrap();
        let (mixer, source) = rodio::mixer::mixer(1.try_into().unwrap(), 8_000.try_into().unwrap());
        let mut audio = DesktopAudio {
            original: Some(wav.path().to_owned()),
            duration: 1.0,
            volume: 0.37,
            ..Default::default()
        };
        audio.player = Some(audio.player_for_mixer(&mixer, 0.0).unwrap());
        (wav, audio, source)
    }

    #[test]
    fn output_handoff_preserves_playback_position_volume_and_rendered_speed() {
        let (wav, mut audio, mut old_source) = output_fixture();
        audio.speed = 0.5;
        audio.duration = 0.5;
        audio.rendered.insert(speed_key(0.5), wav.path().to_owned());
        audio.command("play", json!({})).unwrap();
        assert!(old_source.by_ref().take(2_400).any(|s| s != 0.0));
        old_source.by_ref().take(2_400).for_each(drop);
        let before = audio.position();
        assert!(before > 0.1 && before < 0.4);
        let (mixer, mut next_source) =
            rodio::mixer::mixer(1.try_into().unwrap(), 8_000.try_into().unwrap());
        audio.handoff_to_mixer(&mixer).unwrap();
        assert!((audio.position() - before).abs() < 0.0001);
        assert_eq!(audio.speed, 0.5);
        assert_eq!(audio.player.as_ref().unwrap().volume(), 0.37);
        assert_eq!(audio.status()["playing"], true);
        assert!(next_source.by_ref().take(400).any(|s| s != 0.0));
        // The former output drains and stops receiving the song.
        assert!(old_source.by_ref().take(800).count() < 800);
    }

    #[test]
    fn output_handoff_does_not_resume_a_paused_song() {
        let (_wav, mut audio, mut source) = output_fixture();
        audio.command("play", json!({})).unwrap();
        source.by_ref().take(2_400).for_each(drop);
        audio.command("pause", json!({})).unwrap();
        let before = audio.position();
        let (mixer, mut next_source) =
            rodio::mixer::mixer(1.try_into().unwrap(), 8_000.try_into().unwrap());
        audio.handoff_to_mixer(&mixer).unwrap();
        assert!(audio.paused());
        assert!(next_source.by_ref().take(400).all(|s| s == 0.0));
        assert!((audio.position() - before).abs() < 0.0001);
    }

    #[test]
    fn failed_output_handoff_keeps_the_original_player_running() {
        let (_wav, mut audio, mut source) = output_fixture();
        audio.command("play", json!({})).unwrap();
        source.by_ref().take(800).for_each(drop);
        let before = audio.position();
        audio.original = Some(PathBuf::from("missing-output-handoff-test.wav"));
        let (mixer, _next_source) =
            rodio::mixer::mixer(1.try_into().unwrap(), 8_000.try_into().unwrap());
        assert!(audio.handoff_to_mixer(&mixer).is_err());
        assert!(!audio.paused());
        assert!((audio.position() - before).abs() < 0.0001);
        assert!(source.by_ref().take(800).any(|s| s != 0.0));
    }

    #[test]
    fn output_handoff_does_not_restart_a_finished_song() {
        let (_wav, mut audio, mut source) = output_fixture();
        audio.command("play", json!({})).unwrap();
        source.by_ref().take(16_000).for_each(drop);
        assert!(audio.finished());
        let (mixer, mut next_source) =
            rodio::mixer::mixer(1.try_into().unwrap(), 8_000.try_into().unwrap());
        audio.handoff_to_mixer(&mixer).unwrap();
        assert!(audio.finished());
        assert_eq!(audio.position(), 1.0);
        assert_eq!(audio.status()["playing"], false);
        assert!(next_source.by_ref().take(800).all(|s| s == 0.0));
    }

    #[test]
    fn audio_controls_reject_invalid_ranges() {
        assert!(finite_range(&json!({"speed":0.49}), "speed", 0.5, 1.0).is_err());
        assert!(finite_range(&json!({"volume":1.01}), "volume", 0.0, 1.0).is_err());
        assert_eq!(
            finite_range(&json!({"speed":0.5}), "speed", 0.5, 1.0),
            Ok(0.5)
        );
    }
    #[test]
    fn unloaded_audio_is_idle_and_controlled_errors() {
        let mut audio = DesktopAudio::default();
        assert_eq!(audio.status()["playing"], false);
        assert!(audio.command("play", json!({})).is_err());
        assert!(audio.command("stop", json!({})).is_ok());
        assert!(audio.command("speed", json!({"speed":0.7})).is_err());
    }
    #[test]
    fn idle_audio_worker_shutdown_is_acknowledged_without_opening_speakers() {
        let (events, _rx) = mpsc::channel();
        let mut worker = AudioWorker::new(events);
        assert!(worker.shutdown());
        assert!(worker.handle.is_none());
        assert!(worker.shutdown());
    }
    #[test]
    fn superseded_render_never_launches_ffmpeg() {
        let dir = tempfile::tempdir().unwrap();
        let cancelled = AtomicBool::new(true);
        let error =
            render_speed(Path::new("does-not-exist.wav"), &dir, 0.75, &cancelled).unwrap_err();
        assert!(error.contains("superseded"));
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 0);
    }
    #[test]
    fn native_decoder_reads_duration_and_seeks_without_a_speaker() {
        use std::io::Write;
        let mut wav = tempfile::Builder::new().suffix(".wav").tempfile().unwrap();
        let sample_rate: u32 = 8_000;
        let data_bytes: u32 = sample_rate * 2;
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&16_u32.to_le_bytes());
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&sample_rate.to_le_bytes());
        bytes.extend_from_slice(&(sample_rate * 2).to_le_bytes());
        bytes.extend_from_slice(&2_u16.to_le_bytes());
        bytes.extend_from_slice(&16_u16.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&data_bytes.to_le_bytes());
        bytes.resize(bytes.len() + data_bytes as usize, 0);
        wav.write_all(&bytes).unwrap();
        wav.flush().unwrap();
        let mut source = decode(wav.path()).unwrap();
        assert_eq!(source.total_duration(), Some(Duration::from_secs(1)));
        source.try_seek(Duration::from_millis(500)).unwrap();
        let remaining = source.count();
        assert!(
            (3_900..=4_100).contains(&remaining),
            "remaining samples: {remaining}"
        );
    }
}

fn render_speed(
    original: &Path,
    files: &tempfile::TempDir,
    speed: f64,
    cancel: &AtomicBool,
) -> Result<PathBuf, String> {
    if cancel.load(Ordering::SeqCst) {
        return Err("Playback tempo request was superseded".into());
    }
    let output = files.path().join(format!("speed-{}.wav", speed_key(speed)));
    let mut command = Command::new("ffmpeg");
    command
        .args([
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-threads",
            "1",
            "-i",
        ])
        .arg(original)
        .args([
            "-vn",
            "-filter_threads",
            "1",
            "-af",
            &format!("atempo={speed:.6}"),
            "-c:a",
            "pcm_s16le",
        ])
        .arg(&output)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn().map_err(|e| {
        format!("Pitch-preserving speed changes need FFmpeg on PATH. Could not start FFmpeg: {e}")
    })?;
    let started = Instant::now();
    loop {
        if cancel.load(Ordering::SeqCst) {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Playback tempo request was superseded".into());
        }
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if started.elapsed() < Duration::from_secs(240) => {
                std::thread::sleep(Duration::from_millis(40))
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(
                    "Preparing playback speed timed out; the original track is still available"
                        .into(),
                );
            }
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("Could not prepare playback speed: {error}"));
            }
        }
    }
    let result = child.wait_with_output().map_err(|e| e.to_string())?;
    if !result.status.success() {
        let detail = String::from_utf8_lossy(&result.stderr);
        return Err(format!(
            "Could not prepare playback speed: {}",
            detail.chars().take(600).collect::<String>()
        ));
    }
    Ok(output)
}
