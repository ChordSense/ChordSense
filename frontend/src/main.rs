use std::fs::File;
use std::io::BufReader;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use eframe::egui;
use egui::{Color32, RichText};
use reqwest::blocking::{multipart, Client};
use rodio::{Decoder, OutputStream, Sink, Source};
use serde::{Deserialize, Serialize};

const BACKEND_URL: &str = "http://127.0.0.1:5051";

#[derive(Debug, Clone, Deserialize, Serialize)]
struct ChordSegment {
    start: f32,
    end: f32,
    chord: String,
    confidence: f32,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
struct AnalyzeResponse {
    success: bool,
    chords: Vec<ChordSegment>,
    total_chords: usize,
    duration: f32,
    model_used: String,
    model_name: String,
    chord_dict: String,
    processing_time: f32,
    stdout: Option<String>,
    stderr: Option<String>,
    error: Option<String>,
}

struct AudioState {
    _stream: OutputStream,
    sink: Sink,
    duration_secs: f32,
}

struct ChordSenseOfficialApp {
    audio_path: Option<PathBuf>,
    chords: Vec<ChordSegment>,
    selected_dict: String,
    current_pos: f32,
    track_duration: f32,
    playing: bool,
    paused: bool,
    play_started_at: Option<Instant>,
    play_start_offset: f32,
    volume: f32,
    status: String,
    logs: String,
    audio_state: Option<AudioState>,
}

impl Default for ChordSenseOfficialApp {
    fn default() -> Self {
        Self {
            audio_path: None,
            chords: vec![],
            selected_dict: "submission".to_string(),
            current_pos: 0.0,
            track_duration: 0.0,
            playing: false,
            paused: false,
            play_started_at: None,
            play_start_offset: 0.0,
            volume: 0.8,
            status: "Choose audio, then analyze.".to_string(),
            logs: String::new(),
            audio_state: None,
        }
    }
}

impl ChordSenseOfficialApp {
    fn fmt_time(seconds: f32) -> String {
        let s = seconds.max(0.0);
        let mins = (s / 60.0).floor() as i32;
        let secs = s - mins as f32 * 60.0;
        format!("{:02}:{:04.1}", mins, secs)
    }

    fn append_log(&mut self, text: &str) {
        self.logs.push_str(text);
        self.logs.push('\n');
    }

    fn load_audio(&mut self) {
        if let Some(path) = rfd::FileDialog::new()
            .add_filter("Audio", &["wav", "mp3", "ogg"])
            .pick_file()
        {
            self.audio_path = Some(path.clone());
            self.current_pos = 0.0;
            self.play_start_offset = 0.0;
            self.play_started_at = None;
            self.playing = false;
            self.paused = false;

            match Self::create_audio_state(&path, self.volume) {
                Ok(audio) => {
                    self.track_duration = audio.duration_secs;
                    self.audio_state = Some(audio);
                    self.status = format!("Loaded audio: {}", path.file_name().unwrap().to_string_lossy());
                }
                Err(e) => {
                    self.status = format!("Audio load failed: {e}");
                }
            }
        }
    }

    fn create_audio_state(path: &PathBuf, volume: f32) -> Result<AudioState, String> {
        let stream = rodio::OutputStreamBuilder::open_default_stream()
            .map_err(|e| format!("output stream error: {e}"))?;
        let mixer = stream.mixer().clone();
        let sink = Sink::connect_new(&mixer);

        let file = File::open(path).map_err(|e| format!("open audio error: {e}"))?;
        let reader = BufReader::new(file);
        let decoder = Decoder::try_from(reader).map_err(|e| format!("decode error: {e}"))?;
        let duration_secs = decoder.total_duration().map(|d| d.as_secs_f32()).unwrap_or(0.0);

        sink.append(decoder);
        sink.pause();
        sink.set_volume(volume);

        Ok(AudioState {
            _stream: stream,
            sink,
            duration_secs,
        })
    }

    fn restart_audio_state(&mut self, start_at: f32) -> Result<(), String> {
        let path = self.audio_path.clone().ok_or("no audio loaded")?;
        let stream = rodio::OutputStreamBuilder::open_default_stream()
            .map_err(|e| format!("output stream error: {e}"))?;
        let mixer = stream.mixer().clone();
        let sink = Sink::connect_new(&mixer);

        let file = File::open(&path).map_err(|e| format!("open audio error: {e}"))?;
        let reader = BufReader::new(file);
        let decoder = Decoder::try_from(reader).map_err(|e| format!("decode error: {e}"))?;
        let duration_secs = decoder.total_duration().map(|d| d.as_secs_f32()).unwrap_or(0.0);

        let skipped = decoder.skip_duration(Duration::from_secs_f32(start_at.max(0.0)));
        sink.append(skipped);
        sink.set_volume(self.volume);

        self.audio_state = Some(AudioState {
            _stream: stream,
            sink,
            duration_secs,
        });
        self.track_duration = duration_secs;
        Ok(())
    }

    fn analyze(&mut self) {
        let Some(path) = self.audio_path.clone() else {
            self.status = "Load an audio file first.".to_string();
            return;
        };

        self.status = "Analyzing audio...".to_string();
        self.append_log(&format!("Analyzing: {}", path.display()));
        self.append_log(&format!("Dictionary: {}", self.selected_dict));

        let client = Client::new();
        let file_name = path.file_name().unwrap().to_string_lossy().to_string();

        let bytes = match std::fs::read(&path) {
            Ok(b) => b,
            Err(e) => {
                self.status = format!("Read failed: {e}");
                return;
            }
        };

        let part = multipart::Part::bytes(bytes).file_name(file_name);
        let form = multipart::Form::new()
            .part("file", part)
            .text("chord_dict", self.selected_dict.clone());

        let response = client.post(format!("{BACKEND_URL}/analyze")).multipart(form).send();
        match response {
            Ok(resp) => {
                let parsed: Result<AnalyzeResponse, _> = resp.json();
                match parsed {
                    Ok(payload) => {
                        if payload.success {
                            self.chords = payload.chords;
                            if payload.duration > 0.0 {
                                self.track_duration = payload.duration;
                            }
                            self.status = format!("Analysis complete. {} chords found.", payload.total_chords);
                            if let Some(out) = payload.stdout.as_ref() {
                                if !out.trim().is_empty() {
                                    self.append_log("--- backend stdout ---");
                                    self.append_log(out);
                                }
                            }
                            if let Some(err) = payload.stderr.as_ref() {
                                if !err.trim().is_empty() {
                                    self.append_log("--- backend stderr ---");
                                    self.append_log(err);
                                }
                            }
                        } else {
                            self.status = format!("Analysis failed: {}", payload.error.unwrap_or("unknown error".into()));
                        }
                    }
                    Err(e) => {
                        self.status = format!("JSON parse failed: {e}");
                    }
                }
            }
            Err(e) => {
                self.status = format!("Backend request failed: {e}");
            }
        }
    }

    fn toggle_play_pause(&mut self) {
        let Some(audio) = self.audio_state.as_mut() else {
            self.status = "Load audio first.".to_string();
            return;
        };

        if !self.playing && !self.paused {
            audio.sink.play();
            self.playing = true;
            self.paused = false;
            self.play_start_offset = self.current_pos;
            self.play_started_at = Some(Instant::now());
            self.status = "Playing.".to_string();
            return;
        }

        if self.playing {
            audio.sink.pause();
            self.current_pos = self.get_current_position();
            self.playing = false;
            self.paused = true;
            self.play_started_at = None;
            self.status = "Paused.".to_string();
        } else if self.paused {
            audio.sink.play();
            self.playing = true;
            self.paused = false;
            self.play_start_offset = self.current_pos;
            self.play_started_at = Some(Instant::now());
            self.status = "Playing.".to_string();
        }
    }

    fn restart(&mut self) {
        self.current_pos = 0.0;
        self.play_start_offset = 0.0;
        self.play_started_at = None;
        self.playing = false;
        self.paused = false;
        let _ = self.restart_audio_state(0.0);
        if let Some(audio) = self.audio_state.as_mut() {
            audio.sink.pause();
        }
        self.status = "Returned to start.".to_string();
    }

    fn seek(&mut self, target: f32) {
        self.current_pos = target.clamp(0.0, self.track_duration.max(0.0));
        match self.restart_audio_state(self.current_pos) {
            Ok(()) => {
                if self.playing {
                    if let Some(audio) = self.audio_state.as_mut() {
                        audio.sink.play();
                    }
                    self.play_start_offset = self.current_pos;
                    self.play_started_at = Some(Instant::now());
                } else {
                    if let Some(audio) = self.audio_state.as_mut() {
                        audio.sink.pause();
                    }
                    self.play_start_offset = self.current_pos;
                    self.play_started_at = None;
                }
            }
            Err(e) => self.status = format!("Seek failed: {e}"),
        }
    }

    fn get_current_position(&self) -> f32 {
        if self.playing {
            if let Some(started) = self.play_started_at {
                let pos = self.play_start_offset + started.elapsed().as_secs_f32();
                return pos.min(self.track_duration.max(0.0));
            }
        }
        self.current_pos
    }

    fn current_and_next_chord(&self, pos: f32) -> (String, String) {
        if self.chords.is_empty() {
            return ("---".into(), "---".into());
        }

        for (i, seg) in self.chords.iter().enumerate() {
            if pos >= seg.start && pos < seg.end {
                let next = if i + 1 < self.chords.len() {
                    self.chords[i + 1].chord.clone()
                } else {
                    "---".into()
                };
                return (seg.chord.clone(), next);
            }
        }

        if pos < self.chords[0].start {
            return ("---".into(), self.chords[0].chord.clone());
        }

        (self.chords.last().unwrap().chord.clone(), "---".into())
    }
}

impl eframe::App for ChordSenseOfficialApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        ctx.set_visuals(egui::Visuals::dark());

        if self.playing {
            self.current_pos = self.get_current_position();
            ctx.request_repaint_after(Duration::from_millis(100));
        }

        let (current_chord, next_chord) = self.current_and_next_chord(self.current_pos);

        egui::TopBottomPanel::top("top").show(ctx, |ui| {
            ui.add_space(8.0);
            ui.heading(RichText::new("ChordSenseOfficial").size(30.0));
            ui.label(RichText::new("ChordSense UI + local Python backend chord analysis").color(Color32::LIGHT_GRAY));
            ui.add_space(6.0);
        });

        egui::SidePanel::right("timeline").resizable(true).min_width(320.0).show(ctx, |ui| {
            ui.heading("Chord Timeline");
            ui.separator();
            egui::ScrollArea::vertical().show(ui, |ui| {
                for seg in &self.chords {
                    let active = self.current_pos >= seg.start && self.current_pos < seg.end;
                    let text = format!(
                        "{} → {}    {}",
                        Self::fmt_time(seg.start),
                        Self::fmt_time(seg.end),
                        seg.chord
                    );
                    if active {
                        ui.colored_label(Color32::LIGHT_BLUE, text);
                    } else {
                        ui.label(text);
                    }
                }
            });
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            ui.horizontal_wrapped(|ui| {
                if ui.button("Load Audio").clicked() {
                    self.load_audio();
                }
                if ui.button("Analyze").clicked() {
                    self.analyze();
                }
                if ui.button("Restart").clicked() {
                    self.restart();
                }
                if ui.button(if self.playing { "Pause" } else if self.paused { "Resume" } else { "Play" }).clicked() {
                    self.toggle_play_pause();
                }

                ui.label("Dict:");
                egui::ComboBox::from_id_salt("dict_box")
                    .selected_text(&self.selected_dict)
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut self.selected_dict, "submission".into(), "submission");
                        ui.selectable_value(&mut self.selected_dict, "ismir2017".into(), "ismir2017");
                        ui.selectable_value(&mut self.selected_dict, "full".into(), "full");
                    });

                ui.label("Volume");
                let mut vol = self.volume;
                if ui.add(egui::Slider::new(&mut vol, 0.0..=1.0)).changed() {
                    self.volume = vol;
                    if let Some(audio) = self.audio_state.as_mut() {
                        audio.sink.set_volume(self.volume);
                    }
                }
            });

            ui.add_space(8.0);

            let mut slider_pos = self.current_pos;
            let slider_resp = ui.add(
                egui::Slider::new(&mut slider_pos, 0.0..=self.track_duration.max(0.0))
                    .show_value(false)
                    .text("position")
            );
            if slider_resp.drag_stopped() {
                self.seek(slider_pos);
            }

            ui.horizontal(|ui| {
                ui.label(RichText::new(Self::fmt_time(self.current_pos)).monospace().size(20.0));
                ui.label("/");
                ui.label(RichText::new(Self::fmt_time(self.track_duration)).monospace().size(20.0));
            });

            ui.add_space(10.0);

            ui.group(|ui| {
                ui.heading("Now Playing");
                ui.add_space(6.0);
                ui.label(RichText::new("Current Chord").size(18.0).color(Color32::GRAY));
                ui.label(RichText::new(current_chord).size(40.0).color(Color32::from_rgb(125, 211, 252)));
                ui.add_space(8.0);
                ui.label(RichText::new("Next Chord").size(16.0).color(Color32::GRAY));
                ui.label(RichText::new(next_chord).size(26.0).color(Color32::from_rgb(249, 168, 212)));
            });

            ui.add_space(10.0);
            ui.label(RichText::new(format!("Status: {}", self.status)).color(Color32::LIGHT_GREEN));

            if let Some(path) = &self.audio_path {
                ui.label(format!("Audio: {}", path.display()));
            }

            ui.add_space(10.0);
            ui.heading("Analysis Log");
            egui::ScrollArea::vertical().max_height(220.0).show(ui, |ui| {
                ui.code(&self.logs);
            });
        });
    }
}

fn main() -> eframe::Result<()> {
    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("ChordSenseOfficial")
            .with_inner_size([1280.0, 800.0])
            .with_resizable(true),
        ..Default::default()
    };

    eframe::run_native(
        "ChordSenseOfficial",
        native_options,
        Box::new(|_cc| Ok(Box::new(ChordSenseOfficialApp::default()))),
    )
}
