use eframe::egui;
use egui::Separator;
use egui_extras::install_image_loaders;
use reqwest::blocking::{multipart, Client};
use rodio::{Decoder, DeviceSinkBuilder, Player, Source};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::path::PathBuf;
use std::time::{Duration, Instant};

const BACKEND_URL: &str = "http://127.0.0.1:5051";

fn main() -> eframe::Result<()> {
    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_fullscreen(true)
            .with_title("ChordSenseOfficial"),
        ..Default::default()
    };

    eframe::run_native(
        "ChordSenseOfficial",
        native_options,
        Box::new(|cc| {
            install_image_loaders(&cc.egui_ctx);

            let mut fonts = egui::FontDefinitions::default();

            fonts.font_data.insert(
                "rakkas".to_owned(),
                std::sync::Arc::new(
                    egui::FontData::from_static(include_bytes!("../assets/font/Rakkas-Regular.ttf"))
                ),
            );

            fonts
                .families
                .entry(egui::FontFamily::Name("rakkas".into()))
                .or_default()
                .push("rakkas".to_owned());

            cc.egui_ctx.set_fonts(fonts);

            Ok(Box::new(MyEguiApp::default()))
        }),
    )
}

#[derive(Debug, Clone, Deserialize, Serialize)]
struct ChordSegment {
    start: f64,
    end: f64,
    chord: String,
    #[serde(default = "default_confidence")]
    confidence: f64,
}

fn default_confidence() -> f64 {
    1.0
}

#[derive(Debug, Clone, Deserialize, Serialize)]
struct AnalyzeResponse {
    success: bool,
    #[serde(default)]
    chords: Vec<ChordSegment>,
    #[serde(default)]
    total_chords: usize,
    #[serde(default)]
    duration: f64,
    #[serde(default)]
    model_used: String,
    #[serde(default)]
    model_name: String,
    #[serde(default)]
    chord_dict: String,
    #[serde(default)]
    processing_time: f64,
    #[serde(default)]
    stdout: Option<String>,
    #[serde(default)]
    stderr: Option<String>,
    #[serde(default)]
    error: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct RecordBackendResponse {
    success: bool,
    #[serde(default)]
    message: Option<String>,
    #[serde(default)]
    error: Option<String>,
}

#[derive(Debug, Clone)]
struct LabData {
    chords: Vec<ChordSegment>,
    duration: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RecordUiState {
    Idle,
    Countdown,
    Recording,
}

struct AudioPlayback {
    sink: rodio::stream::MixerDeviceSink,
    player: Player,
    audio_path: PathBuf,
    duration_secs: Option<f64>,
    last_error: Option<String>,
    volume: f32,
}

impl AudioPlayback {
    fn new(audio_path: PathBuf) -> Self {
        let sink = DeviceSinkBuilder::open_default_sink().expect("open default audio stream");
        let player = Player::connect_new(&sink.mixer());

        let mut this = Self {
            sink,
            player,
            audio_path,
            duration_secs: None,
            last_error: None,
            volume: 0.8,
        };

        if let Err(err) = this.load_current_file() {
            this.last_error = Some(err);
        }

        this
    }

    fn load_current_file(&mut self) -> Result<(), String> {
        let player = Player::connect_new(&self.sink.mixer());

        let file = File::open(&self.audio_path)
            .map_err(|e| format!("Could not open audio file '{}': {e}", self.audio_path.display()))?;

        let decoder = Decoder::try_from(file)
            .map_err(|e| format!("Could not decode audio file '{}': {e}", self.audio_path.display()))?;

        self.duration_secs = decoder.total_duration().map(|d| d.as_secs_f64());
        player.append(decoder);
        player.pause();
        player.set_volume(self.volume);

        self.player = player;
        self.last_error = None;
        Ok(())
    }

    fn play(&self) {
        self.player.play();
    }

    fn pause(&self) {
        self.player.pause();
    }

    fn stop(&mut self) {
        self.player.stop();
        if let Err(err) = self.load_current_file() {
            self.last_error = Some(err);
        }
    }

    fn seek(&mut self, position_secs: f64) {
        let pos = Duration::from_secs_f64(position_secs.max(0.0));
        if let Err(err) = self.player.try_seek(pos) {
            self.last_error = Some(format!("Seek failed: {err}"));
        }
    }

    fn set_volume(&mut self, volume: f32) {
        self.volume = volume;
        self.player.set_volume(volume);
    }

    fn position_secs(&self) -> f64 {
        self.player.get_pos().as_secs_f64()
    }

    fn is_paused(&self) -> bool {
        self.player.is_paused()
    }

    fn is_finished(&self) -> bool {
        self.player.empty()
    }

    fn duration_secs(&self) -> Option<f64> {
        self.duration_secs
    }

    fn path_label(&self) -> String {
        self.audio_path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("audio file")
            .to_string()
    }
}

struct MyEguiApp {
    started: bool,
    mode: usize,

    progress: f64,
    is_playing: bool,

    chord_data: Option<LabData>,
    chord_assets: HashMap<&'static str, &'static str>,
    audio: Option<AudioPlayback>,

    selected_audio_path: Option<PathBuf>,
    selected_dict: String,
    status_message: String,
    backend_logs: String,

    record_state: RecordUiState,
    record_countdown_start: Option<Instant>,
    record_started_at: Option<Instant>,
    record_status_message: String,

    lab_play_started_at: Option<Instant>,
    lab_play_start_progress: f64,

    analyze_after_repaint: bool,
}

impl Default for MyEguiApp {
    fn default() -> Self {
        Self {
            started: false,
            mode: 0,
            progress: 0.0,
            is_playing: false,
            chord_data: None,
            chord_assets: chord_asset_map(),
            audio: None,
            selected_audio_path: None,
            selected_dict: "submission".to_string(),
            status_message: "Choose an audio file, then analyze.".to_string(),
            backend_logs: String::new(),

            record_state: RecordUiState::Idle,
            record_countdown_start: None,
            record_started_at: None,
            record_status_message: "Ready to record.".to_string(),

            lab_play_started_at: None,
            lab_play_start_progress: 0.0,

            analyze_after_repaint: false,
        }
    }
}

impl MyEguiApp {
    fn append_log(&mut self, msg: &str) {
        self.backend_logs.push_str(msg);
        self.backend_logs.push('\n');
    }

    fn choose_audio_file(&mut self) {
        if let Some(path) = rfd::FileDialog::new()
            .add_filter("Audio", &["wav", "mp3", "ogg"])
            .pick_file()
        {
            self.selected_audio_path = Some(path.clone());
            self.audio = Some(AudioPlayback::new(path.clone()));
            self.progress = 0.0;
            self.is_playing = false;
            self.lab_play_started_at = None;
            self.lab_play_start_progress = 0.0;
            self.chord_data = None;
            self.status_message = format!(
                "Loaded audio: {}",
                path.file_name().and_then(|s| s.to_str()).unwrap_or("audio")
            );
            self.backend_logs.clear();
            self.append_log(&format!("Loaded audio file: {}", path.display()));
        }
    }

    fn analyze_audio(&mut self) {
        let Some(path) = self.selected_audio_path.clone() else {
            self.status_message = "No audio file selected.".to_string();
            return;
        };

        self.status_message = "Analyzing audio with backend...".to_string();
        self.append_log(&format!("Analyzing file: {}", path.display()));
        self.append_log(&format!("Chord dictionary: {}", self.selected_dict));

        let bytes = match std::fs::read(&path) {
            Ok(b) => b,
            Err(e) => {
                self.status_message = format!("Failed to read audio: {e}");
                self.append_log(&self.status_message.clone());
                return;
            }
        };

        let file_name = path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("audio.wav")
            .to_string();

        let client = Client::builder()
	    .connect_timeout(Duration::from_secs(10))
	    .timeout(Duration::from_secs(3600))
	    .build()
	    .unwrap();

        let part = multipart::Part::bytes(bytes).file_name(file_name);
        let form = multipart::Form::new()
            .part("file", part)
            .text("chord_dict", self.selected_dict.clone());

        let response = client
            .post(format!("{BACKEND_URL}/analyze"))
            .multipart(form)
            .send();

        match response {
            Ok(resp) => {
                let status = resp.status();

                let text = match resp.text() {
                    Ok(t) => t,
                    Err(e) => {
                        self.status_message = format!("Failed reading backend response text: {e}");
                        self.append_log(&format!("Failed reading backend response text: {e:?}"));
                        return;
                    }
                };

                self.append_log(&format!("HTTP status: {status}"));
                self.append_log("--- raw backend response ---");
                self.append_log(&text);

                let parsed: Result<AnalyzeResponse, _> = serde_json::from_str(&text);
                match parsed {
                    Ok(payload) => {
                        if payload.success {
                            let duration = if payload.duration > 0.0 {
                                payload.duration
                            } else {
                                payload.chords.last().map(|c| c.end).unwrap_or(0.0)
                            };

                            let chord_count = payload.chords.len();

                            self.chord_data = Some(LabData {
                                chords: payload.chords.clone(),
                                duration,
                            });

                            self.status_message =
                                format!("Analysis complete. {} chords found.", payload.total_chords.max(chord_count));

                            self.append_log(&format!(
                                "Stored chord_data successfully. chord_count={}, duration={:.3}",
                                chord_count, duration
                            ));

                            if let Some(stdout) = payload.stdout {
                                if !stdout.trim().is_empty() {
                                    self.append_log("--- backend stdout ---");
                                    self.append_log(&stdout);
                                }
                            }
                            if let Some(stderr) = payload.stderr {
                                if !stderr.trim().is_empty() {
                                    self.append_log("--- backend stderr ---");
                                    self.append_log(&stderr);
                                }
                            }
                        } else {
                            self.status_message = format!(
                                "Analysis failed: {}",
                                payload.error.unwrap_or_else(|| "unknown error".to_string())
                            );
                            self.append_log(&self.status_message.clone());
                        }
                    }
                    Err(e) => {
                        self.status_message = format!("Failed to parse backend JSON: {e}");
                        self.append_log(&format!("Failed to parse backend JSON: {e:?}"));
                    }
                }
            }
            Err(e) => {
                self.status_message = format!("Backend request failed: {e:?}");
                self.append_log(&format!("Backend request failed: {e:?}"));
            }
        }
    }

    fn max_duration(&self) -> f64 {
        let lab_duration = self.chord_data.as_ref().map(|d| d.duration).unwrap_or(0.0);
        let audio_duration = self
            .audio
            .as_ref()
            .and_then(|a| a.duration_secs())
            .unwrap_or(0.0);
        lab_duration.max(audio_duration)
    }

    fn sync_progress_from_audio(&mut self) {
        if let Some(audio) = &self.audio {
            self.progress = audio.position_secs().min(self.max_duration());

            if audio.is_finished() {
                self.is_playing = false;
            } else {
                self.is_playing = !audio.is_paused();
            }
            return;
        }

        if self.is_playing {
            if let Some(started_at) = self.lab_play_started_at {
                let max_duration = self.max_duration();
                let elapsed = started_at.elapsed().as_secs_f64();
                self.progress = (self.lab_play_start_progress + elapsed).min(max_duration);

                if self.progress >= max_duration {
                    self.progress = max_duration;
                    self.is_playing = false;
                    self.lab_play_started_at = None;
                }
            }
        }
    }

    fn play(&mut self) {
        let max_duration = self.max_duration();
        if max_duration <= 0.0 {
            return;
        }

        if self.progress >= max_duration {
            self.stop();
        }

        if let Some(audio) = &self.audio {
            audio.play();
            self.sync_progress_from_audio();
        } else {
            self.lab_play_start_progress = self.progress;
            self.lab_play_started_at = Some(Instant::now());
            self.is_playing = true;
        }
    }

    fn pause(&mut self) {
        if let Some(audio) = &self.audio {
            audio.pause();
            self.sync_progress_from_audio();
        } else if self.is_playing {
            if let Some(started_at) = self.lab_play_started_at {
                let elapsed = started_at.elapsed().as_secs_f64();
                self.progress = (self.lab_play_start_progress + elapsed).min(self.max_duration());
            }
            self.is_playing = false;
            self.lab_play_started_at = None;
        }
    }

    fn stop(&mut self) {
        if let Some(audio) = &mut self.audio {
            audio.stop();
            self.sync_progress_from_audio();
        } else {
            self.progress = 0.0;
            self.is_playing = false;
        }

        self.lab_play_started_at = None;
        self.lab_play_start_progress = 0.0;
    }

    fn seek(&mut self, new_position: f64) {
        let target = new_position.clamp(0.0, self.max_duration());

        if let Some(audio) = &mut self.audio {
            audio.seek(target);
            self.sync_progress_from_audio();
        } else {
            self.progress = target;
            self.is_playing = false;
            self.lab_play_started_at = None;
            self.lab_play_start_progress = target;
        }
    }

    fn start_record_countdown(&mut self) {
        if self.record_state != RecordUiState::Idle {
            return;
        }

        self.record_state = RecordUiState::Countdown;
        self.record_countdown_start = Some(Instant::now());
        self.record_started_at = None;
        self.record_status_message = "Ready to record.".to_string();
    }

    fn stop_recording_ui(&mut self) {
            let was_recording = self.record_state == RecordUiState::Recording;

            if was_recording {
                self.end_recording_backend();
            } else {
                self.record_status_message = "Recording cancelled.".to_string();
            }

            self.record_state = RecordUiState::Idle;
            self.record_countdown_start = None;
            self.record_started_at = None;
        }

    fn update_recording_ui(&mut self) {
        if self.record_state != RecordUiState::Countdown {
            return;
        }

        let Some(start) = self.record_countdown_start else {
            self.record_state = RecordUiState::Idle;
            self.record_status_message = "Ready to record.".to_string();
            return;
        };

        let elapsed = start.elapsed().as_secs_f32();

        self.record_status_message = if elapsed < 3.0 {
            "Recording Starts Soon...".to_string()
        } else if elapsed < 4.0 {
            "Start Playing!".to_string()
        } else {
            if self.begin_recording_backend() {
                self.record_state = RecordUiState::Recording;
                self.record_countdown_start = None;
                self.record_started_at = Some(Instant::now());
                self.record_status_message = "Recording in progress...".to_string();
            } else {
                self.record_state = RecordUiState::Idle;
                self.record_countdown_start = None;
                self.record_started_at = None;
            }
            return;
        };
    }

    fn recording_elapsed_secs(&self) -> f64 {
        self.record_started_at
            .map(|start| start.elapsed().as_secs_f64())
            .unwrap_or(0.0)
    }

    fn countdown_display_text(&self) -> Option<&'static str> {
        if self.record_state != RecordUiState::Countdown {
            return None;
        }

        let start = self.record_countdown_start?;
        let elapsed = start.elapsed().as_secs_f32();

        if elapsed < 1.0 {
            Some("4")
        } else if elapsed < 2.0 {
            Some("3")
        } else if elapsed < 3.0 {
            Some("2")
        } else if elapsed < 4.0 {
            Some("1")
        } else {
            None
        }
    }

    fn begin_recording_backend(&mut self) -> bool {
        self.append_log("POST /begin_recording");

        let client = match Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(30))
            .build()
        {
            Ok(c) => c,
            Err(e) => {
                self.record_status_message = format!("Failed to build HTTP client: {e}");
                self.append_log(&self.record_status_message.clone());
                return false;
            }
        };

        let response = client
            .post(format!("{BACKEND_URL}/begin_recording"))
            .send();

        match response {
            Ok(resp) => {
                let status = resp.status();

                let text = match resp.text() {
                    Ok(t) => t,
                    Err(e) => {
                        self.record_status_message =
                            format!("Failed reading /begin_recording response: {e}");
                        self.append_log(&self.record_status_message.clone());
                        return false;
                    }
                };

                self.append_log(&format!("HTTP status: {status}"));
                self.append_log("--- /begin_recording raw response ---");
                self.append_log(&text);

                match serde_json::from_str::<RecordBackendResponse>(&text) {
                    Ok(payload) => {
                        if payload.success {
                            self.record_status_message = payload
                                .message
                                .unwrap_or_else(|| "Recording started.".to_string());
                            true
                        } else {
                            self.record_status_message = payload
                                .error
                                .unwrap_or_else(|| "begin_recording failed".to_string());
                            self.append_log(&self.record_status_message.clone());
                            false
                        }
                    }
                    Err(e) => {
                        self.record_status_message =
                            format!("Failed to parse /begin_recording JSON: {e}");
                        self.append_log(&self.record_status_message.clone());
                        false
                    }
                }
            }
            Err(e) => {
                self.record_status_message = format!("Backend request failed: {e}");
                self.append_log(&self.record_status_message.clone());
                false
            }
        }
    }

    fn end_recording_backend(&mut self) -> bool {
        self.append_log("POST /end_recording");

        let client = match Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(60))
            .build()
        {
            Ok(c) => c,
            Err(e) => {
                self.record_status_message = format!("Failed to build HTTP client: {e}");
                self.append_log(&self.record_status_message.clone());
                return false;
            }
        };

        let response = client
            .post(format!("{BACKEND_URL}/end_recording"))
            .send();

        match response {
            Ok(resp) => {
                let status = resp.status();

                let text = match resp.text() {
                    Ok(t) => t,
                    Err(e) => {
                        self.record_status_message =
                            format!("Failed reading /end_recording response: {e}");
                        self.append_log(&self.record_status_message.clone());
                        return false;
                    }
                };

                self.append_log(&format!("HTTP status: {status}"));
                self.append_log("--- /end_recording raw response ---");
                self.append_log(&text);

                match serde_json::from_str::<AnalyzeResponse>(&text) {
                    Ok(payload) => {
                        if payload.success {
                            let duration = if payload.duration > 0.0 {
                                payload.duration
                            } else {
                                payload.chords.last().map(|c| c.end).unwrap_or(0.0)
                            };

                            let chord_count = payload.chords.len();

                            self.chord_data = Some(LabData {
                                chords: payload.chords.clone(),
                                duration,
                            });

                            self.audio = None;
                            self.selected_audio_path = None;
                            self.progress = 0.0;
                            self.is_playing = false;

                            self.lab_play_started_at = None;
                            self.lab_play_start_progress = 0.0;

                            self.status_message = format!(
                                "Recorded analysis loaded. {} chords found.",
                                payload.total_chords.max(chord_count)
                            );
                            self.record_status_message = "Recording stopped.".to_string();

                            self.append_log(&format!(
                                "Stored recorded chord_data successfully. chord_count={}, duration={:.3}",
                                chord_count, duration
                            ));

                            if let Some(stdout) = payload.stdout {
                                if !stdout.trim().is_empty() {
                                    self.append_log("--- backend stdout ---");
                                    self.append_log(&stdout);
                                }
                            }

                            if let Some(stderr) = payload.stderr {
                                if !stderr.trim().is_empty() {
                                    self.append_log("--- backend stderr ---");
                                    self.append_log(&stderr);
                                }
                            }

                            self.mode = 0;
                            true
                        } else {
                            self.record_status_message = format!(
                                "Recorded analysis failed: {}",
                                payload.error.unwrap_or_else(|| "unknown error".to_string())
                            );
                            self.append_log(&self.record_status_message.clone());
                            false
                        }
                    }
                    Err(e) => {
                        self.record_status_message =
                            format!("Failed to parse /end_recording JSON: {e}");
                        self.append_log(&self.record_status_message.clone());
                        false
                    }
                }
            }
            Err(e) => {
                self.record_status_message = format!("Backend request failed: {e}");
                self.append_log(&self.record_status_message.clone());
                false
            }
        }
    }
}

impl eframe::App for MyEguiApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        if self.analyze_after_repaint {
            self.analyze_after_repaint = false;
            self.analyze_audio();
        }

        if ctx.input(|i| i.key_pressed(egui::Key::Escape)) {
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            return;
        }
        
        if !self.started {
            let any_pressed = ctx.input(|i| !i.keys_down.is_empty());
            if any_pressed {
                self.started = true;
            }
        }
        // Set background color and force black text.
        let mut visuals = egui::Visuals::light();
        visuals.panel_fill = egui::Color32::from_rgb(240, 230, 210); // beige
        visuals.override_text_color = Some(egui::Color32::BLACK);
        ctx.set_visuals(visuals);

        if self.started && ctx.input(|i| i.key_pressed(egui::Key::M)) {
            self.pause();

            if self.mode == 1 && self.record_state != RecordUiState::Idle {
                self.stop_recording_ui();
            }

            self.mode = (self.mode + 1) % 2;
        }

        self.sync_progress_from_audio();
        self.update_recording_ui();

        if self.started && self.mode == 0 && self.is_playing {
            ctx.request_repaint_after(Duration::from_millis(16));
        }

        if self.started
            && self.mode == 1
            && matches!(self.record_state, RecordUiState::Countdown | RecordUiState::Recording)
        {
            ctx.request_repaint_after(Duration::from_millis(16));
        }

        egui::CentralPanel::default().show(ctx, |ui| {
            if !self.started {
                show_start_screen(ui);
                return;
            }

            match self.mode {
                0 => show_sense_mode(ui, ctx, self),
                1 => show_record_mode(ui, self),
                _ => {}
            }
        });
    }
}

fn active_chord(time: f64, chords: &[ChordSegment]) -> Option<&ChordSegment> {
    chords.iter().find(|seg| time >= seg.start && time < seg.end)
}

fn next_chord(time: f64, chords: &[ChordSegment]) -> Option<&ChordSegment> {
    chords.iter().find(|seg| seg.start > time)
}

fn previous_chord(time: f64, chords: &[ChordSegment]) -> Option<&ChordSegment> {
    chords.iter().rev().find(|seg| seg.end <= time)
}

fn pretty_chord_label(raw: &str) -> String {
    if raw == "N" {
        return "No chord".to_string();
    }

    let mut label = raw.replace(":maj", "");
    label = label.replace(":min", "m");

    if let Some((base, bass)) = label.split_once('/') {
        let bass_note = bass_degree_to_note(raw, bass).unwrap_or_else(|| bass.to_string());
        format!("{}/{}", base, bass_note)
    } else {
        label
    }
}

fn format_time_mm_ss(seconds: f64) -> String {
    let total_seconds = seconds.max(0.0).floor() as u64;
    let minutes = total_seconds / 60;
    let secs = total_seconds % 60;
    format!("{:02}:{:02}", minutes, secs)
}

fn bass_degree_to_note(raw: &str, bass: &str) -> Option<String> {
    let root = chord_root(raw)?;
    let note = match bass {
        "1" => root,
        "b2" => transpose_note(root, 1)?,
        "2" => transpose_note(root, 2)?,
        "b3" => transpose_note(root, 3)?,
        "3" => transpose_note(root, 4)?,
        "4" => transpose_note(root, 5)?,
        "b5" => transpose_note(root, 6)?,
        "5" => transpose_note(root, 7)?,
        "#5" | "b6" => transpose_note(root, 8)?,
        "6" => transpose_note(root, 9)?,
        "b7" => transpose_note(root, 10)?,
        "7" => transpose_note(root, 11)?,
        _ => return None,
    };

    Some(note.to_string())
}

fn chord_root(raw: &str) -> Option<&str> {
    let root_part = raw.split(':').next()?;
    if root_part == "N" {
        None
    } else {
        Some(root_part)
    }
}

fn transpose_note(root: &str, semitones: usize) -> Option<&'static str> {
    let chromatic = [
        "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B",
    ];

    let idx = match root {
        "C" => 0,
        "C#" | "Db" => 1,
        "D" => 2,
        "D#" | "Eb" => 3,
        "E" => 4,
        "F" => 5,
        "F#" | "Gb" => 6,
        "G" => 7,
        "G#" | "Ab" => 8,
        "A" => 9,
        "A#" | "Bb" => 10,
        "B" | "Cb" => 11,
        _ => return None,
    };

    Some(chromatic[(idx + semitones) % 12])
}

fn chord_asset_key(raw: &str) -> Option<&'static str> {
    let simplified = simplify_for_asset(raw);

    match simplified.as_str() {
        "A" => Some("a"),
        "Ab" | "G#" => Some("ab"),
        "Abm" | "G#m" => Some("abm"),
        "Am" => Some("am"),
        "B" => Some("b"),
        "Bb" | "A#" => Some("bb"),
        "Bbm" | "A#m" => Some("bbm"),
        "Bm" => Some("bm"),
        "C" => Some("c"),
        "C#" | "Db" => Some("c#"),
        "C#m" | "Dbm" => Some("c#m"),
        "Cm" => Some("cm"),
        "D" => Some("d"),
        "Dm" => Some("dm"),
        "E" => Some("e"),
        "Eb" | "D#" => Some("eb"),
        "Ebm" | "D#m" => Some("ebm"),
        "Em" => Some("em"),
        "F" => Some("f"),
        "F#m" | "Gbm" => Some("f#m"),
        "Fm" => Some("fm"),
        "G" => Some("g"),
        "Gm" => Some("gm"),
        _ => None,
    }
}

fn simplify_for_asset(raw: &str) -> String {
    if raw == "N" {
        return "N".to_string();
    }

    let base = raw.split('/').next().unwrap_or(raw);
    let root = base.split(':').next().unwrap_or(base);
    let quality = base.split(':').nth(1).unwrap_or("maj").to_lowercase();

    let is_minor_family = quality.starts_with("min");

    if is_minor_family {
        format!("{}m", root)
    } else {
        root.to_string()
    }
}

fn chord_asset_map() -> HashMap<&'static str, &'static str> {
    HashMap::from([
        ("a", "assets/chords/a.png"),
        ("ab", "assets/chords/ab.png"),
        ("abm", "assets/chords/abm.png"),
        ("am", "assets/chords/am.png"),
        ("b", "assets/chords/b.png"),
        ("bb", "assets/chords/bb.png"),
        ("bbm", "assets/chords/bbm.png"),
        ("bm", "assets/chords/bm.png"),
        ("c", "assets/chords/c.png"),
        ("c#", "assets/chords/c#.png"),
        ("c#m", "assets/chords/c#m.png"),
        ("cm", "assets/chords/cm.png"),
        ("d", "assets/chords/d.png"),
        ("dm", "assets/chords/dm.png"),
        ("e", "assets/chords/e.png"),
        ("eb", "assets/chords/eb.png"),
        ("ebm", "assets/chords/ebm.png"),
        ("em", "assets/chords/em.png"),
        ("f", "assets/chords/f.png"),
        ("f#m", "assets/chords/f#m.png"),
        ("fm", "assets/chords/fm.png"),
        ("g", "assets/chords/g.png"),
        ("gm", "assets/chords/gm.png"),
    ])
}

fn show_start_screen(ui: &mut egui::Ui) {
    ui.with_layout(egui::Layout::top_down(egui::Align::Center), |ui| {
        ui.add_space((ui.available_height() * 0.22).clamp(20.0, 140.0));

        let card_width = ui.available_width().min(900.0);

        ui.allocate_ui_with_layout(
            egui::vec2(card_width, 0.0),
            egui::Layout::top_down(egui::Align::Center),
            |ui| {
                egui::Frame::new()
                    .fill(egui::Color32::BLACK)
                    .stroke(egui::Stroke::new(2.0, egui::Color32::WHITE))
                    .corner_radius(egui::CornerRadius::same(20))
                    .inner_margin(egui::Margin::same(10))
                    .show(ui, |ui| {
                        ui.horizontal_centered(|ui| {
                            ui.add_space(180.0);
                            ui.label(
                                egui::RichText::new("♫")
                                    .size(60.0)
                                    .color(egui::Color32::from_rgb(240, 230, 210)),
                            );
                            ui.add_space(15.0);
                            ui.label(
                                egui::RichText::new("ChordSense")
                                    .size(72.0)
                                    .color(egui::Color32::from_rgb(250, 240, 230))
                                    .family(egui::FontFamily::Name("rakkas".into())),
                            );
                            ui.add_space(180.0);
                        });

                        ui.add_space(24.0);

                        ui.label(
                            egui::RichText::new("Press any button to start")
                                .size(18.0)
                                .color(egui::Color32::from_rgb(240, 230, 210)),
                        );
                    });
            },
        );
    });
}

fn show_sense_mode(ui: &mut egui::Ui, ctx: &egui::Context, app: &mut MyEguiApp) {
    let max_duration = app.max_duration();

    ui.with_layout(egui::Layout::top_down(egui::Align::Center), |ui| {
        ui.add_space(25.0);
        ui.horizontal(|ui| {
            

            ui.add_space(20.0);
            ui.heading(
                
                egui::RichText::new("Mode: Play Along")
                    .size(48.0)
                    .family(egui::FontFamily::Name("rakkas".into()))
            );

            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                egui::Frame::new()
                    .fill(egui::Color32::BLACK)
                    .stroke(egui::Stroke::new(2.0, egui::Color32::BLACK))
                    .corner_radius(egui::CornerRadius::same(30))
                    .inner_margin(egui::Margin::same(10))
                    .show(ui, |ui| {
                        ui.label(
                            egui::RichText::new("ChordSense")
                                .size(48.0)
                                .color(egui::Color32::WHITE)
                                .family(egui::FontFamily::Name("rakkas".into())),
                        );
                    });
            });
        });
        
        ui.add_space(25.0);
        ui.separator();


        ui.horizontal(|ui| {
            ui.add_space(20.0);
            if ui.add( egui::Button::new(egui::RichText::new("Load Audio").color(egui::Color32::WHITE)).fill(egui::Color32::BLACK)
                )
                .clicked()
            {
                app.choose_audio_file();
            }

            if ui.add(
                    egui::Button::new(
                        egui::RichText::new("Analyze").color(egui::Color32::WHITE)
                    )
                    .fill(egui::Color32::BLACK)
                )
                .clicked()
            {
                app.status_message = "Loading Analysis...".to_string();
                app.analyze_after_repaint = true;
                ctx.request_repaint();
            }


            if let Some(audio) = &mut app.audio {
                let mut vol = audio.volume;
                if ui
                    .add(egui::Slider::new(&mut vol, 0.0..=1.0).text("Volume"))
                    .changed()
                {
                    audio.set_volume(vol);
                }
            }
        });

        ui.add_space(12.0);
        let is_error = app.status_message.contains("Failed")
            || app.status_message.contains("failed")
            || app.status_message.contains("No audio file selected")
            || app.status_message.contains("error");

        if is_error {
            ui.label(
                egui::RichText::new(format!("Status: {}", app.status_message))
                    .size(22.0)
                    .color(egui::Color32::RED),
            );
        }
        // ui.label(format!(
        //     "Debug: chord_data loaded = {}, chord count = {}",
        //     app.chord_data.is_some(),
        //     app.chord_data.as_ref().map(|d| d.chords.len()).unwrap_or(0)
        // ));

        ui.add_space(8.0);

        ui.horizontal(|ui| {
            ui.horizontal(|ui| {
                ui.add_space(20.0);
                let vinyl = egui::Image::new(
                    egui::include_image!("../assets/icons/vinyl.png")
                )
                .fit_to_exact_size(egui::vec2(40.0, 40.0));
                ui.add(vinyl);
                ui.add_space(8.0);
                egui::Frame::new()
                    .fill(egui::Color32::BLACK)
                    .stroke(egui::Stroke::new(1.0, egui::Color32::WHITE))
                    .corner_radius(egui::CornerRadius::same(14))
                    .inner_margin(egui::Margin::symmetric(14, 8))
                    .show(ui, |ui| {
                        

                        

                        let song_name = if let Some(audio) = app.audio.as_ref() {
                            audio.path_label()
                        } else if app.chord_data.is_some() {
                            "Your_Recording".to_string()
                        } else {
                            "No song loaded".to_string()
                        };

                        ui.label(
                            egui::RichText::new(format!("Now Playing: {}", song_name))
                                .size(22.0)
                                .color(egui::Color32::WHITE)
                                .strong(),
                        );
                    
                    });
            });
        });

        ui.add_space(10.0);

        let back = egui::Image::new(egui::include_image!("../assets/icons/back.png"))
            .fit_to_exact_size(egui::vec2(50.0, 50.0));
        let pause = egui::Image::new(egui::include_image!("../assets/icons/pause.png"))
            .fit_to_exact_size(egui::vec2(50.0, 50.0));
        let play_button = egui::Image::new(egui::include_image!("../assets/icons/play-button.png"))
            .fit_to_exact_size(egui::vec2(50.0, 50.0));

        ui.horizontal(|ui| {
            ui.add_space(20.0);
            // let metronome = egui::Image::new(egui::include_image!("../../assets/icons/metronome.png"))
            //     .fit_to_exact_size(egui::vec2(50.0, 50.0));
            // ui.add(metronome);
            // ui.add_space(9.0);

            let back_response = ui.add(back.sense(egui::Sense::click()));
            if back_response.clicked() {
                app.stop();
            }

            ui.add_space(12.0);

            if app.is_playing {
                let pause_response = ui.add(pause.sense(egui::Sense::click()));
                if pause_response.clicked() {
                    app.pause();
                }
            } else {
                let play_response = ui.add(play_button.sense(egui::Sense::click()));
                if play_response.clicked() {
                    app.play();
                    ctx.request_repaint();
                }
            }

            ui.add_space(12.0);

            ui.vertical(|ui| {
                let mut slider_value = app.progress;

                let slider_response = ui.add_sized(
                    [400.0, 30.0],
                    egui::Slider::new(&mut slider_value, 0.0..=max_duration)
                        .show_value(false)
                        .min_decimals(0)
                        .max_decimals(3),
                );

                if slider_response.changed() {
                    let was_playing = app.is_playing;
                    app.seek(slider_value);
                    if was_playing {
                        app.play();
                    }
                }

                ui.add_space(2.0);
                ui.label(
                    egui::RichText::new(format!(
                        "{} / {}",
                        format_time_mm_ss(app.progress),
                        format_time_mm_ss(max_duration)
                    ))
                    .size(20.0),
                );
            });
        });

        ui.separator();

        let now_pos = app.progress;

        let (
            active_label,
            previous_label,
            next_label,
            previous_image_path,
            active_image_path,
            next_image_path,
            active_model_output,
            active_simplified_output,
        ) = if let Some(data) = &app.chord_data {
            let active = active_chord(now_pos, &data.chords);
            let previous = previous_chord(now_pos, &data.chords);
            let next = next_chord(now_pos, &data.chords);

            let active_label = active
                .map(|c| pretty_chord_label(&c.chord))
                .unwrap_or_else(|| "No chord".to_string());

            let previous_label = previous
                .map(|c| pretty_chord_label(&c.chord))
                .unwrap_or_else(|| "".to_string());

            let next_label = next
                .map(|c| pretty_chord_label(&c.chord))
                .unwrap_or_else(|| "".to_string());

            let previous_image_path = previous
                .and_then(|c| chord_asset_key(&c.chord))
                .and_then(|key| app.chord_assets.get(key).copied())
                .map(|s| s.to_string());

            let active_image_path = active
                .and_then(|c| chord_asset_key(&c.chord))
                .and_then(|key| app.chord_assets.get(key).copied())
                .map(|s| s.to_string());

            let next_image_path = next
                .and_then(|c| chord_asset_key(&c.chord))
                .and_then(|key| app.chord_assets.get(key).copied())
                .map(|s| s.to_string());

            let active_model_output = active.map(|c| c.chord.clone());
            let active_simplified_output = active.map(|c| simplify_for_asset(&c.chord));

            (
                active_label,
                previous_label,
                next_label,
                previous_image_path,
                active_image_path,
                next_image_path,
                active_model_output,
                active_simplified_output,
            )
        } else {
            (
                "Please Load a Song of Choice to Play Along to!".to_string(),
                "".to_string(),
                "".to_string(),
                None,
                None,
                None,
                None,
                None,
            )
        };

        ui.add_space(20.0);

        if app.chord_data.is_none() {
            ui.add_space(40.0);
            ui.vertical_centered(|ui| {
                let center_message = if app.status_message.contains("Loading")
                    || app.status_message.contains("Analyzing")
                {
                    "Loading analysis..."
                } else {
                    "Please Load a Song\nof Choice to Play\nAlong to!"
                };

                ui.label(
                    egui::RichText::new(center_message)
                        .size(34.0),
                );
            });
        } else {
            let side_col_w = 360.0;
            let center_col_w = 360.0;
            let col_gap = 36.0;
            let row_h = 340.0;

            let total_w = side_col_w + center_col_w + side_col_w + col_gap * 2.0;

            ui.allocate_ui_with_layout(
                egui::vec2(ui.available_width(), row_h),
                egui::Layout::left_to_right(egui::Align::Min),
                |ui| {
                    let left_pad = ((ui.available_width() - total_w) * 0.5).max(0.0);
                    ui.add_space(left_pad - 50.0);

                    ui.allocate_ui_with_layout(
                        egui::vec2(side_col_w, row_h),
                        egui::Layout::top_down(egui::Align::Center),
                        |ui| {
                            if let Some(path) = &previous_image_path {
                                let prev_image = egui::Image::new(format!("file://{}", path))
                                    .fit_to_exact_size(egui::vec2(250.0, 280.0))
                                    .tint(egui::Color32::from_white_alpha(110));
                                ui.add(prev_image);
                            } else if !previous_label.is_empty() {
                                ui.allocate_ui_with_layout(
                                    egui::vec2(250.0, 280.0),
                                    egui::Layout::top_down(egui::Align::Center),
                                    |ui| {
                                        ui.add_space(120.0);
                                        ui.label(
                                            egui::RichText::new("N/A")
                                                .size(28.0)
                                                .color(egui::Color32::BLACK)
                                                .strong(),
                                        );
                                    },
                                );
                            }
                        },
                    );

                    ui.add_space(col_gap);

                    ui.allocate_ui_with_layout(
                        egui::vec2(center_col_w, row_h),
                        egui::Layout::top_down(egui::Align::Center),
                        |ui| {
                            if let Some(path) = &active_image_path {
                                let chord_image = egui::Image::new(format!("file://{}", path))
                                    .fit_to_exact_size(egui::vec2(350.0, 390.0));
                                ui.add(chord_image);
                            } else if active_model_output.is_some() {
                                ui.allocate_ui_with_layout(
                                    egui::vec2(350.0, 390.0),
                                    egui::Layout::top_down(egui::Align::Center),
                                    |ui| {
                                        ui.add_space(170.0);
                                        ui.label(
                                            egui::RichText::new("N/A")
                                                .size(34.0)
                                                .strong(),
                                        );
                                    },
                                );
                            }
                        },
                    );

                    ui.add_space(col_gap);

                    ui.allocate_ui_with_layout(
                        egui::vec2(side_col_w, row_h),
                        egui::Layout::top_down(egui::Align::Center),
                        |ui| {
                            if let Some(path) = &next_image_path {
                                let next_image = egui::Image::new(format!("file://{}", path))
                                    .fit_to_exact_size(egui::vec2(250.0, 280.0))
                                    .tint(egui::Color32::from_white_alpha(110));
                                ui.add(next_image);
                            }
                        },
                    );
                },
            );
        }

        ui.add_space(12.0);

        //// Debug for AUDIO
        // if let Some(audio) = &app.audio {
        //     ui.separator();
        //     ui.label(egui::RichText::new("Audio Debug").size(24.0));
        //     ui.label(
        //         egui::RichText::new(format!("Audio file: {}", audio.path_label())).size(18.0),
        //     );
        //     if let Some(duration) = audio.duration_secs() {
        //         ui.label(
        //             egui::RichText::new(format!("Decoded audio duration: {:.6}s", duration)).size(18.0),
        //         );
        //     } else {
        //         ui.label(egui::RichText::new("Decoded audio duration: unavailable").size(18.0));
        //     }
        //     ui.label(
        //         egui::RichText::new(format!("Playback position from audio engine: {:.6}s", app.progress))
        //             .size(18.0),
        //     );
        //     ui.label(
        //         egui::RichText::new(format!("Audio paused: {}", audio.is_paused())).size(18.0),
        //     );
        //     ui.label(
        //         egui::RichText::new(format!("Audio queue empty: {}", audio.is_finished())).size(18.0),
        //     );
        //     if let Some(err) = &audio.last_error {
        //         ui.label(
        //             egui::RichText::new(format!("Audio status: {}", err))
        //                 .size(18.0)
        //                 .color(egui::Color32::RED),
        //         );
        //     } else {
        //         ui.label(
        //             egui::RichText::new("Audio status: loaded")
        //                 .size(18.0)
        //                 .color(egui::Color32::DARK_GREEN),
        //         );
        //     }
        // }

        // if let Some(data) = &app.chord_data {
            // ui.label(egui::RichText::new("Detected chord timeline").size(26.0));
            // ui.label(
            //     egui::RichText::new(format!(
            //         "Playback duration is based on max(audio duration, backend chord end time): {:.3}s",
            //         max_duration
            //     ))
            //     .size(18.0)
            //     .color(egui::Color32::DARK_GRAY),
            // );
            // ui.label(
            //     egui::RichText::new(format!("Chord end time: {:.3}s", data.duration))
            //         .size(18.0)
            //         .color(egui::Color32::DARK_GRAY),
            // );

        //     // Chord Timeline
        //     egui::ScrollArea::vertical().max_height(180.0).show(ui, |ui| {
        //         for seg in &data.chords {
        //             let is_active = app.progress >= seg.start && app.progress < seg.end;
        //             let text = format!(
        //                 "{:.3} - {:.3}    {}",
        //                 seg.start,
        //                 seg.end,
        //                 pretty_chord_label(&seg.chord)
        //             );

        //             if is_active {
        //                 ui.label(
        //                     egui::RichText::new(text)
        //                         .size(22.0)
        //                         .strong()
        //                         .color(egui::Color32::from_rgb(0, 90, 200)),
        //                 );
        //             } else {
        //                 ui.label(egui::RichText::new(text).size(20.0));
        //             }
        //         }
        //     });
        // } else {
        //     ui.label(egui::RichText::new("No analyzed chord timeline yet.").size(24.0));
        // }

        
        // ui.label(egui::RichText::new("Backend Log").size(24.0));
        // egui::ScrollArea::vertical().max_height(180.0).show(ui, |ui| {
        //     if app.backend_logs.trim().is_empty() {
        //         ui.label("No backend logs yet.");
        //     } else {
        //         ui.code(&app.backend_logs);
        //     }
        // });

        ui.add_space(8.0);
        ui.label(egui::RichText::new("Press M to switch modes").size(20.0));
    });
}

fn show_record_mode(ui: &mut egui::Ui, app: &mut MyEguiApp) {
    ui.with_layout(egui::Layout::top_down(egui::Align::Center), |ui| {
        ui.add_space(25.0);
        ui.horizontal(|ui| {
            ui.add_space(20.0);
            ui.heading(
                egui::RichText::new("Mode: Record")
                    .size(48.0)
                    .family(egui::FontFamily::Name("rakkas".into())),
            );

            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                egui::Frame::new()
                    .fill(egui::Color32::BLACK)
                    .stroke(egui::Stroke::new(2.0, egui::Color32::BLACK))
                    .corner_radius(egui::CornerRadius::same(30))
                    .inner_margin(egui::Margin::same(10))
                    .show(ui, |ui| {
                        ui.label(
                            egui::RichText::new("ChordSense")
                                .size(48.0)
                                .color(egui::Color32::WHITE)
                                .family(egui::FontFamily::Name("rakkas".into())),
                        );
                    });
            });
        });

        ui.add_space(25.0);
        ui.separator();

        

        ui.add_space(30.0);

        let (title_text, detail_text) = match app.record_state {
            RecordUiState::Idle => (
                "Ready to Record".to_string(),
                "Press Start Recording to begin.".to_string(),
            ),
            RecordUiState::Countdown => (
                "Get Ready".to_string(),
                "".to_string(),
            ),
            RecordUiState::Recording => (
                "Recording...".to_string(),
                format!(
                    "Elapsed Time - {}",
                    format_time_mm_ss(app.recording_elapsed_secs())
                ),
            ),
        };

        egui::Frame::new()
            .fill(egui::Color32::from_rgb(54, 53, 49))
            .stroke(egui::Stroke::new(2.0, egui::Color32::from_rgb(54, 53, 49)))
            .corner_radius(egui::CornerRadius::same(5))
            .inner_margin(egui::Margin::symmetric(28, 20))
            .show(ui, |ui| {
                ui.set_min_width(720.0);
                ui.vertical_centered(|ui| {
                    ui.label(
                        egui::RichText::new(title_text)
                            .size(34.0)
                            .color(egui::Color32::from_rgb(240, 230, 210))
                            .strong(),
                    );
                    ui.add_space(6.0);
                    ui.label(
                        egui::RichText::new(detail_text)
                            .size(20.0)
                            .color(egui::Color32::from_rgb(240, 230, 210)),
                    );
                });
            });

        ui.add_space(30.0);

        match app.record_state {
            RecordUiState::Idle => {
                let rec_image =
                    egui::Image::new(egui::include_image!("../assets/icons/record_circle.png"))
                        .fit_to_exact_size(egui::vec2(70.0, 70.0));
                ui.add(rec_image);
            }
            RecordUiState::Countdown => {
                let countdown_text = app.countdown_display_text().unwrap_or("3");
                ui.label(
                    egui::RichText::new(countdown_text)
                        .size(120.0)
                        .strong(),
                );
            }
            RecordUiState::Recording => {
                let blink_on = ((ui.ctx().input(|i| i.time) * 1.5).floor() as i64) % 2 == 0;

                if blink_on {
                    let rec_image =
                        egui::Image::new(egui::include_image!("../assets/icons/record_circle.png"))
                            .fit_to_exact_size(egui::vec2(70.0, 70.0));

                    ui.add(rec_image);
                } else {
                    ui.allocate_space(egui::vec2(70.0, 70.0));
                }
            }
        }

        ui.add_space(20.0);

        // Record Buttons
        ui.allocate_ui_with_layout(
            egui::vec2(ui.available_width(), 60.0),
            egui::Layout::left_to_right(egui::Align::Center),
            |ui| {
                let button_w = 240.0;
                let gap = 18.0;
                let total_w = button_w * 2.0 + gap;
                let left_pad = ((ui.available_width() - total_w) * 0.5).max(0.0);

                ui.add_space(left_pad);

                let start_response = ui.add_enabled(
                    app.record_state == RecordUiState::Idle,
                    egui::Button::new(
                        egui::RichText::new("Start Recording")
                            .size(24.0)
                            .color(egui::Color32::WHITE),
                    )
                    .fill(egui::Color32::BLACK)
                    .min_size(egui::vec2(button_w, 56.0)),
                );

                if start_response.clicked() {
                    app.start_record_countdown();
                }

                ui.add_space(gap);

                let stop_response = ui.add_enabled(
                    app.record_state != RecordUiState::Idle,
                    egui::Button::new(
                        egui::RichText::new("Stop Recording")
                            .size(24.0)
                            .color(egui::Color32::WHITE),
                    )
                    .fill(egui::Color32::BLACK)
                    .min_size(egui::vec2(button_w, 56.0)),
                );

                if stop_response.clicked() {
                    app.stop_recording_ui();
                }
            },
        );

        ui.add_space(10.0);

        let record_is_error = app.record_status_message.contains("Failed")
            || app.record_status_message.contains("failed")
            || app.record_status_message.contains("error");

        ui.label(
            egui::RichText::new(&app.record_status_message)
                .size(20.0)
                .color(if record_is_error {
                    egui::Color32::RED
                } else {
                    egui::Color32::BLACK
                }),
        );


        // ui.label(egui::RichText::new("Press M to switch modes").size(25.0));
    });
}
