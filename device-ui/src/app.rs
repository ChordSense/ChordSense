use crate::{
    domain::*,
    services::{Event, Services},
};
use eframe::egui::{self, *};
use serde_json::{json, Value};
use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
    sync::mpsc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
include!(concat!(env!("OUT_DIR"), "/assets.rs"));
const CREAM: Color32 = Color32::from_rgb(240, 230, 210);
const INK: Color32 = Color32::from_rgb(17, 17, 17);
const PAPER: Color32 = Color32::from_rgb(245, 235, 218);
fn now_ms() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
        * 1000.0
}
fn time(s: f64) -> String {
    format_time(s)
}
fn display_name(name: &str) -> String {
    let trimmed = name.trim();
    let base = trimmed
        .rsplit_once('.')
        .filter(|(_, ext)| {
            ["mp3", "wav", "m4a", "flac", "ogg"].contains(&ext.to_ascii_lowercase().as_str())
        })
        .map_or(trimmed, |(base, _)| base);
    base.split_whitespace().collect::<Vec<_>>().join(" ")
}
fn file_size(bytes: u64) -> String {
    if bytes < 1024 {
        format!("{bytes} B")
    } else if bytes < 1024 * 1024 {
        format!("{:.1} KB", bytes as f64 / 1024.0)
    } else {
        format!("{:.1} MB", bytes as f64 / 1048576.0)
    }
}
fn text(v: &Value, key: &str) -> String {
    v[key].as_str().unwrap_or_default().to_owned()
}
fn num(v: &Value, key: &str) -> f64 {
    v[key].as_f64().unwrap_or(0.0)
}
fn retire_pending_reply(
    pending: &mut HashMap<String, u64>,
    busy: &mut HashSet<String>,
    tag: &str,
    generation: u64,
) {
    // A completion for an obsolete command must not unlock its replacement.
    if pending.get(tag) == Some(&generation) {
        pending.remove(tag);
        busy.remove(tag);
    }
}
fn current_event(
    tag: &str,
    generation: u64,
    source: u64,
    playback: u64,
    pairing: u64,
    feedback: u64,
) -> bool {
    match tag {
        "loaded" | "analyze" => generation == source,
        "playback" | "poll" => generation == playback,
        "pairing" => generation == pairing,
        "feedback_event" | "feedback_stop" => generation == feedback,
        tag if tag.starts_with("fb_") => generation == feedback,
        _ => true,
    }
}
fn edit_title_selection(
    value: &mut String,
    selection: std::ops::Range<usize>,
    insert: &str,
    backspace: bool,
) -> Option<usize> {
    let mut chars: Vec<char> = value.chars().collect();
    let end = selection.end.min(chars.len());
    let mut start = selection.start.min(end);
    if backspace && start == end {
        start = start.saturating_sub(1);
    }
    let inserted: Vec<char> = if backspace {
        vec![]
    } else {
        insert.chars().collect()
    };
    if chars.len() - (end - start) + inserted.len() > 80 {
        return None;
    }
    let cursor = start + inserted.len();
    chars.splice(start..end, inserted);
    *value = chars.into_iter().collect();
    Some(cursor)
}
fn button(ui: &mut Ui, s: impl Into<String>, enabled: bool) -> Response {
    ui.add_enabled(
        enabled,
        Button::new(RichText::new(s).color(Color32::WHITE).size(16.0))
            .fill(INK)
            .corner_radius(8)
            .min_size(vec2(0.0, 38.0)),
    )
}
#[derive(PartialEq)]
enum Record {
    Idle,
    Countdown(Instant),
    Starting,
    Recording(Instant),
    Processing,
}
pub struct ChordSense {
    services: Services,
    busy: HashSet<String>,
    pending: HashMap<String, u64>,
    textures: HashMap<String, TextureHandle>,
    record_mode: bool,
    record: Record,
    status: String,
    record_status: String,
    source: Value,
    name: String,
    duration: f64,
    position: f64,
    seek_preview: Option<f64>,
    clock: Instant,
    playing: bool,
    loaded: bool,
    speed: f64,
    speed_control: f64,
    volume: f64,
    backlight: Option<crate::backlight::Backlight>,
    brightness: f64,
    source_gen: u64,
    playback_gen: u64,
    last_playback_action: String,
    last_poll: Instant,
    chords: Vec<Chord>,
    timeline: Vec<Chord>,
    capo: i32,
    recommended: Option<i32>,
    focus: bool,
    library: bool,
    recordings: bool,
    files: Vec<Value>,
    takes: Vec<Value>,
    selected: Option<usize>,
    library_status: String,
    rename: bool,
    rename_text: String,
    keyboard: bool,
    shift: bool,
    delete: bool,
    rename_target: Option<Value>,
    rename_kind: String,
    rename_submitted: Option<String>,
    rename_select_all: bool,
    rename_cursor: Option<egui::text::CCursorRange>,
    pairing: bool,
    pair_gen: u64,
    pair: Value,
    pair_deadline: Instant,
    pair_poll: Instant,
    pair_status: String,
    pair_qr: crate::pairing_qr::WebsiteQr,
    feedback: bool,
    fb_gen: u64,
    fb_session: String,
    fb_backend_gen: Option<u64>,
    fb_running: bool,
    fb_cutoff: f64,
    fb_play_pending: bool,
    rater: Option<FeedbackRater>,
    score: Option<FeedbackScore>,
    rating: Option<Rating>,
    results: bool,
    picker_requested: bool,
    picker_open: bool,
    picker_tx: mpsc::Sender<Option<PathBuf>>,
    picker_rx: mpsc::Receiver<Option<PathBuf>>,
}
impl ChordSense {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let ctx = &cc.egui_ctx;
        ctx.send_viewport_cmd(ViewportCommand::CursorVisible(false));
        let mut fonts = FontDefinitions::default();
        fonts.font_data.insert(
            "Rakkas".into(),
            FontData::from_static(include_bytes!("assets/font/Rakkas-Regular.ttf")).into(),
        );
        fonts
            .families
            .insert(FontFamily::Name("Rakkas".into()), vec!["Rakkas".into()]);
        // Match the original Arial fallback using the host font when available.
        #[cfg(target_os = "windows")]
        if let Ok(bytes) = std::fs::read("C:/Windows/Fonts/arial.ttf") {
            fonts
                .font_data
                .insert("Arial".into(), FontData::from_owned(bytes).into());
            fonts
                .families
                .get_mut(&FontFamily::Proportional)
                .unwrap()
                .insert(0, "Arial".into());
        }
        #[cfg(target_os = "windows")]
        if let Ok(bytes) = std::fs::read("C:/Windows/Fonts/arialbd.ttf") {
            fonts
                .font_data
                .insert("ArialBold".into(), FontData::from_owned(bytes).into());
            fonts
                .families
                .insert(FontFamily::Name("Bold".into()), vec!["ArialBold".into()]);
        }
        if !fonts
            .families
            .contains_key(&FontFamily::Name("Bold".into()))
        {
            fonts.families.insert(
                FontFamily::Name("Bold".into()),
                fonts.families[&FontFamily::Proportional].clone(),
            );
        }
        ctx.set_fonts(fonts);
        let mut style = (*ctx.style()).clone();
        style
            .text_styles
            .insert(TextStyle::Body, FontId::proportional(16.0));
        style
            .text_styles
            .insert(TextStyle::Button, FontId::proportional(16.0));
        style.visuals = Visuals::light();
        style.visuals.widgets.noninteractive.bg_stroke = Stroke::new(2.0, INK);
        style.visuals.override_text_color = Some(INK);
        style.visuals.panel_fill = CREAM;
        style.visuals.window_fill = PAPER;
        style.visuals.selection.bg_fill = Color32::from_rgb(205, 191, 168);
        style.spacing.item_spacing = vec2(12.0, 10.0);
        style.spacing.button_padding = vec2(17.0, 10.0);
        style.spacing.slider_width = 150.0;
        ctx.set_style(style);
        let textures = ASSETS
            .iter()
            .filter_map(|(name, bytes)| {
                let img = image::load_from_memory(bytes).ok()?.into_rgba8();
                Some((
                    name.to_string(),
                    ctx.load_texture(
                        *name,
                        ColorImage::from_rgba_unmultiplied(
                            [img.width() as usize, img.height() as usize],
                            img.as_raw(),
                        ),
                        TextureOptions::LINEAR,
                    ),
                ))
            })
            .collect();
        let (picker_tx, picker_rx) = mpsc::channel();
        let backlight = crate::backlight::Backlight::discover();
        let brightness = backlight.as_ref().map_or(0.0, |panel| panel.current as f64);
        let mut app = Self {
            services: Services::new(),
            busy: HashSet::new(),
            pending: HashMap::new(),
            textures,
            record_mode: false,
            record: Record::Idle,
            status: String::new(),
            record_status: "Ready to record.".into(),
            source: Value::Null,
            name: "No song loaded".into(),
            duration: 0.0,
            position: 0.0,
            seek_preview: None,
            clock: Instant::now(),
            playing: false,
            loaded: false,
            speed: 1.0,
            speed_control: 1.0,
            volume: 1.0,
            backlight,
            brightness,
            source_gen: 0,
            playback_gen: 0,
            last_playback_action: String::new(),
            last_poll: Instant::now(),
            chords: vec![],
            timeline: vec![],
            capo: 0,
            recommended: None,
            focus: false,
            library: false,
            recordings: false,
            files: vec![],
            takes: vec![],
            selected: None,
            library_status: String::new(),
            rename: false,
            rename_text: String::new(),
            keyboard: false,
            shift: false,
            delete: false,
            rename_target: None,
            rename_kind: String::new(),
            rename_submitted: None,
            rename_select_all: false,
            rename_cursor: None,
            pairing: false,
            pair_gen: 0,
            pair: Value::Null,
            pair_deadline: Instant::now(),
            pair_poll: Instant::now(),
            pair_status: String::new(),
            pair_qr: crate::pairing_qr::WebsiteQr::default(),
            feedback: false,
            fb_gen: 0,
            fb_session: String::new(),
            fb_backend_gen: None,
            fb_running: false,
            fb_cutoff: 0.0,
            fb_play_pending: false,
            rater: None,
            score: None,
            rating: None,
            results: false,
            picker_requested: false,
            picker_open: false,
            picker_tx,
            picker_rx,
        };
        let args: Vec<String> = std::env::args().collect();
        if args.iter().any(|a| a == "--mute") {
            app.volume = 0.0;
        }
        if let Some(i) = args.iter().position(|a| a == "--audio") {
            if let Some(path) = args.get(i + 1) {
                let chords = args
                    .iter()
                    .position(|a| a == "--lab")
                    .and_then(|j| args.get(j + 1))
                    .and_then(|p| std::fs::read_to_string(p).ok())
                    .map(|s| {
                        s.lines()
                            .filter_map(|l| {
                                let p: Vec<_> = l.split_whitespace().collect();
                                Some(Chord {
                                    start: p.first()?.parse().ok()?,
                                    end: p.get(1)?.parse().ok()?,
                                    chord: p.get(2)?.to_string(),
                                    confidence: 1.0,
                                })
                            })
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default();
                let duration = chords.last().map_or(0.0, |c| c.end);
                app.load(
                    json!({"path":path}),
                    PathBuf::from(path)
                        .file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .into_owned(),
                    chords,
                    duration,
                );
            }
        }
        app
    }
    fn request(&mut self, tag: &str, g: u64, method: &str, path: &str, body: Value) {
        self.pending.insert(tag.into(), g);
        self.busy.insert(tag.into());
        self.services.request(tag, g, method, path, body);
    }
    fn playback(&mut self, action: &str, body: Value) {
        self.playback_gen += 1;
        self.last_playback_action = action.to_owned();
        self.pending.insert("playback".into(), self.playback_gen);
        self.busy.insert("playback".into());
        self.services
            .playback("playback", self.playback_gen, action, body);
    }
    fn current(&self) -> f64 {
        (self.position
            + if self.playing {
                self.clock.elapsed().as_secs_f64() * self.speed
            } else {
                0.0
            })
        .min(self.duration.max(0.0))
    }
    fn stop_feedback(&mut self) {
        let owned = self.fb_running
            || !self.fb_session.is_empty()
            || self.busy.contains("fb_start")
            || self.busy.contains("fb_resume");
        self.fb_gen += 1;
        if owned {
            self.services.stop_feedback(self.fb_gen);
        }
        self.busy.remove("fb_start");
        self.busy.remove("fb_pause");
        self.busy.remove("fb_resume");
        self.fb_running = false;
        self.fb_play_pending = false;
        self.fb_session.clear();
        self.fb_backend_gen = None;
        self.fb_cutoff = now_ms();
        self.rating = None;
        self.rater = None;
    }
    fn start_feedback(&mut self) {
        if !self.feedback
            || (!self.playing && !self.fb_play_pending)
            || self.chords.is_empty()
            || self.fb_running
            || self.busy.contains("fb_start")
            || self.busy.contains("fb_resume")
        {
            return;
        }
        self.fb_gen += 1;
        self.score = Some(FeedbackScore::new(self.chords.clone()));
        self.results = false;
        self.rating = None;
        self.fb_cutoff = now_ms();
        self.request(
            "fb_start",
            self.fb_gen,
            "POST",
            "/feedback/start",
            json!({}),
        );
    }
    fn feedback_ready(&mut self, v: Value) {
        self.fb_session = text(&v, "session_id");
        self.fb_backend_gen = v["generation"].as_u64();
        if self.fb_session.is_empty() {
            self.stop_feedback();
            self.status = "Live feedback did not return a session.".into();
            return;
        }
        self.fb_cutoff = now_ms();
        self.rating = None;
        let mut r = FeedbackRater::new(self.chords.clone());
        r.reset(Some(self.fb_session.clone()));
        self.rater = Some(r);
        self.fb_running = (self.playing || self.fb_play_pending) && self.feedback;
        if self.fb_play_pending && self.fb_running {
            self.begin_playback();
        }
        // A user may pause during the start/resume HTTP request. Release the
        // capture stream once that request completes rather than leaving it live.
        if !self.fb_running && !self.busy.contains("fb_pause") {
            self.request(
                "fb_pause",
                self.fb_gen,
                "POST",
                "/feedback/pause",
                json!({}),
            );
        }
    }
    fn feedback_failed(&mut self, error: String) {
        let play_pending = self.fb_play_pending;
        self.stop_feedback();
        self.status = format!("Live feedback unavailable: {error}");
        // The former UI still allowed listening when the guitar recognizer
        // could not start. Keep playback useful while reporting the failure.
        if play_pending {
            self.begin_playback();
        }
    }
    fn pause(&mut self) {
        self.fb_play_pending = false;
        self.position = self.current();
        self.clock = Instant::now();
        self.playing = false;
        self.playback("pause", json!({}));
        self.rating = None;
        self.fb_cutoff = now_ms();
        if let Some(r) = &mut self.rater {
            r.reset(Some(self.fb_session.clone()));
        }
        if self.fb_running {
            self.request(
                "fb_pause",
                self.fb_gen,
                "POST",
                "/feedback/pause",
                json!({}),
            );
            self.fb_running = false;
        }
    }
    fn play(&mut self) {
        if !self.loaded || self.fb_play_pending {
            return;
        }
        if self.current() >= self.duration && self.duration > 0.0 {
            self.seek(0.0);
            self.score = Some(FeedbackScore::new(self.chords.clone()));
        }
        self.results = false;
        if self.feedback && !self.chords.is_empty() && !self.fb_running {
            self.fb_play_pending = true;
            if self.fb_session.is_empty() {
                self.start_feedback();
            } else if !self.fb_running && !self.busy.contains("fb_resume") {
                self.fb_cutoff = now_ms();
                self.rating = None;
                self.request(
                    "fb_resume",
                    self.fb_gen,
                    "POST",
                    "/feedback/resume",
                    json!({}),
                );
            }
            return;
        }
        self.begin_playback();
    }
    fn begin_playback(&mut self) {
        self.fb_play_pending = false;
        self.clock = Instant::now();
        self.playing = true;
        self.playback("play", json!({}));
        self.focus = !self.chords.is_empty();
        self.results = false;
    }
    fn stop(&mut self) {
        self.seek_preview = None;
        self.playback("stop", json!({}));
        self.position = 0.0;
        self.clock = Instant::now();
        self.playing = false;
        self.focus = false;
        self.stop_feedback();
    }
    fn seek(&mut self, p: f64) {
        self.position = p.clamp(0.0, self.duration.max(0.0));
        self.clock = Instant::now();
        self.fb_cutoff = now_ms();
        self.rating = None;
        if let Some(r) = &mut self.rater {
            r.reset(Some(self.fb_session.clone()));
        }
        self.playback("seek", json!({"position_secs":self.position}));
    }
    fn load(&mut self, source: Value, name: String, chords: Vec<Chord>, duration: f64) {
        self.stop();
        self.seek_preview = None;
        self.source_gen += 1;
        self.source = source;
        self.name = display_name(&name);
        self.loaded = false;
        self.duration = duration;
        self.speed = 1.0;
        self.speed_control = 1.0;
        self.capo = 0;
        self.focus = false;
        self.results = false;
        self.install_chords(chords, duration);
        self.status = "Loading audio…".into();
        self.library = false;
        self.playback_gen += 1;
        self.pending.insert("loaded".into(), self.source_gen);
        self.busy.insert("loaded".into());
        self.services
            .playback("loaded", self.source_gen, "load", self.source.clone());
    }
    fn install_chords(&mut self, chords: Vec<Chord>, duration: f64) {
        self.recommended = if chords.is_empty() {
            None
        } else {
            Some(recommend_capo(&chords).capo)
        };
        self.timeline = build_chord_timeline(&chords, duration);
        self.chords = chords;
    }
    fn analyze(&mut self) {
        if self.source.is_null() {
            return;
        }
        self.status = "Analyzing audio…".into();
        self.pending.insert("analyze".into(), self.source_gen);
        self.busy.insert("analyze".into());
        if let Some(path) = self.source["path"].as_str() {
            self.services.analyze(
                "analyze",
                self.source_gen,
                PathBuf::from(path),
                "submission",
            );
        } else {
            self.services.request(
                "analyze",
                self.source_gen,
                "POST",
                "/analyze_uploaded",
                json!({"filename":self.source["upload"],"chord_dict":"submission"}),
            );
        }
    }
    fn refresh_library(&mut self) {
        self.selected = None;
        self.library_status = "Loading library…".into();
        self.request("files", 0, "GET", "/api/uploads", Value::Null);
        self.request("recordings", 0, "GET", "/api/recordings", Value::Null);
    }
    fn selection(&self) -> Option<Value> {
        let rows = if self.recordings {
            &self.takes
        } else {
            &self.files
        };
        self.selected.and_then(|i| rows.get(i).cloned())
    }
    fn load_selected(&mut self) {
        if let Some(row) = self.selection() {
            let name = row["title"]
                .as_str()
                .filter(|s| !s.is_empty())
                .unwrap_or(row["name"].as_str().unwrap_or("Track"))
                .to_string();
            if self.recordings {
                self.load(
                    json!({"path":row["wav_path"]}),
                    name,
                    serde_json::from_value(row["chords"].clone()).unwrap_or_default(),
                    num(&row, "duration"),
                );
            } else {
                self.load(json!({"upload":row["name"]}), name, vec![], 0.0);
            }
        }
    }
    fn pair_action(&mut self, action: &str) {
        self.pair_gen += 1;
        self.pair_status = "Updating pairing…".into();
        self.request(
            "pairing",
            self.pair_gen,
            "POST",
            &format!("/api/pairing/{action}"),
            json!({}),
        );
    }
    fn close_pair(&mut self) {
        self.pairing = false;
        self.pair_action("cancel");
    }
    fn events(&mut self) {
        while let Ok(selection) = self.picker_rx.try_recv() {
            self.picker_open = false;
            let Some(path) = selection else { continue };
            let name = path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned();
            self.load(json!({"path":path}), name, vec![], 0.0);
        }
        while let Ok(Event {
            tag,
            generation,
            result,
        }) = self.services.rx.try_recv()
        {
            retire_pending_reply(&mut self.pending, &mut self.busy, &tag, generation);
            if !current_event(
                &tag,
                generation,
                self.source_gen,
                self.playback_gen,
                self.pair_gen,
                self.fb_gen,
            ) {
                continue;
            }
            let v = match result {
                Ok(v) => v,
                Err(e) => {
                    match tag.as_str() {
                        "pairing" => self.pair_status = e,
                        "files" | "recordings" | "rename" | "delete" => self.library_status = e,
                        "record_start" | "record_end" => {
                            self.record = Record::Idle;
                            self.record_status = e;
                        }
                        "fb_start" | "fb_resume" | "fb_pause" | "feedback_event" => {
                            self.feedback_failed(e)
                        }
                        _ => {
                            self.status = e;
                            if tag == "playback" {
                                self.speed_control = self.speed;
                                if self.last_playback_action == "play" {
                                    // The local chart clock was started optimistically.
                                    // A failed audio start must also release guitar capture.
                                    self.playing = false;
                                    self.clock = Instant::now();
                                    self.focus = false;
                                    self.stop_feedback();
                                }
                                // Reconcile failed seeks/stops with the real player promptly.
                                self.last_poll = Instant::now() - Duration::from_secs(2);
                            }
                        }
                    }
                    continue;
                }
            };
            match tag.as_str() {
                "loaded" => {
                    self.loaded = true;
                    self.duration = num(&v, "duration").max(self.duration);
                    self.timeline = build_chord_timeline(&self.chords, self.duration);
                    self.position = 0.0;
                    self.status = "Audio loaded.".into();
                    self.playback("volume", json!({"volume":self.volume}));
                }
                "playback" | "poll" => {
                    let was_playing = self.playing;
                    let previous_speed = self.speed;
                    if let Some(p) = v["position"].as_f64().or(v["position_secs"].as_f64()) {
                        self.position = p;
                        self.clock = Instant::now();
                    }
                    if let Some(d) = v["duration"].as_f64() {
                        self.duration = d;
                    }
                    if let Some(s) = v["speed"].as_f64() {
                        self.speed = s;
                        if !self.busy.contains("playback") {
                            self.speed_control = s;
                        }
                    }
                    if let Some(p) = v["playing"].as_bool() {
                        self.playing = p;
                    } else if let Some(p) = v["paused"].as_bool() {
                        self.playing = !p;
                    }
                    if self.speed != previous_speed {
                        self.fb_cutoff = now_ms();
                        self.rating = None;
                        if let Some(rater) = &mut self.rater {
                            rater.reset(Some(self.fb_session.clone()));
                        }
                    }
                    if v["finished"] == true
                        || (self.duration > 0.0 && self.position >= self.duration)
                    {
                        self.complete();
                    } else if was_playing != self.playing {
                        if !self.playing && self.fb_running {
                            self.fb_running = false;
                            self.rating = None;
                            self.fb_cutoff = now_ms();
                            if let Some(rater) = &mut self.rater {
                                rater.reset(Some(self.fb_session.clone()));
                            }
                            self.request(
                                "fb_pause",
                                self.fb_gen,
                                "POST",
                                "/feedback/pause",
                                json!({}),
                            );
                        } else if self.playing
                            && self.feedback
                            && !self.fb_running
                            && !self.fb_play_pending
                        {
                            if self.fb_session.is_empty() {
                                self.start_feedback();
                            } else if !self.busy.contains("fb_resume") {
                                self.request(
                                    "fb_resume",
                                    self.fb_gen,
                                    "POST",
                                    "/feedback/resume",
                                    json!({}),
                                );
                            }
                        }
                    }
                }
                "analyze" => {
                    let chords: Vec<Chord> =
                        serde_json::from_value(v["chords"].clone()).unwrap_or_default();
                    self.status = format!("Analysis complete. {} chords found.", chords.len());
                    self.install_chords(chords, num(&v, "duration").max(self.duration));
                }
                "files" => {
                    self.files = v["files"].as_array().cloned().unwrap_or_default();
                    self.library_status.clear();
                }
                "recordings" => {
                    self.takes = v["recordings"].as_array().cloned().unwrap_or_default();
                    self.library_status.clear();
                }
                "rename" => {
                    if let Some(row) = &self.rename_target {
                        let same_upload = self.rename_kind == "songs"
                            && self.source["upload"]
                                .as_str()
                                .is_some_and(|source| row["name"].as_str() == Some(source));
                        let same_take = self.rename_kind == "recordings"
                            && self.source["path"]
                                .as_str()
                                .is_some_and(|source| row["wav_path"].as_str() == Some(source));
                        if same_upload || same_take {
                            self.name = v["title"]
                                .as_str()
                                .map(str::to_owned)
                                .or(self.rename_submitted.clone())
                                .unwrap_or_else(|| self.name.clone());
                        }
                    }
                    self.rename = false;
                    self.rename_target = None;
                    self.rename_submitted = None;
                    self.refresh_library();
                }
                "delete" => {
                    self.delete = false;
                    self.refresh_library();
                }
                "record_start" => {
                    self.record = Record::Recording(Instant::now());
                    self.record_status = "Recording in progress…".into();
                }
                "record_end" => {
                    self.record = Record::Idle;
                    self.record_mode = false;
                    self.load(
                        json!({"path":v["wav_path"]}),
                        v["recording_name"].as_str().unwrap_or("Recording").into(),
                        serde_json::from_value(v["chords"].clone()).unwrap_or_default(),
                        num(&v, "duration"),
                    );
                }
                "pairing" => {
                    self.pair = v;
                    self.pair_deadline = Instant::now()
                        + Duration::from_secs_f64(num(&self.pair, "expires_in").max(0.0));
                    self.pair_poll = Instant::now();
                    self.pair_status = match self.pair["state"].as_str().unwrap_or("") {
                        "pending" => "Waiting for your browser to pair…",
                        "paired" => "Browser paired. You can upload songs from the website.",
                        "expired" => "This code expired. Generate a new code to pair.",
                        "locked" => "Pairing closed after five incorrect attempts.",
                        "cancelled" => {
                            "Pairing cancelled. Paired browsers keep their existing sessions."
                        }
                        _ => "Generate a new code to pair a browser.",
                    }
                    .into();
                }
                "fb_start" | "fb_resume" => self.feedback_ready(v),
                "feedback_event" => self.feedback_event(v),
                _ => {}
            }
        }
    }
    fn complete(&mut self) {
        if !self.fb_session.is_empty() {
            let position = self.current();
            if let Some(rater) = &mut self.rater {
                let result = rater.advance(position, self.speed);
                if let Some(score) = &mut self.score {
                    score.record(&result);
                }
            }
            self.results = self.score.as_ref().and_then(|s| s.summary()).is_some();
        }
        self.position = self.duration;
        self.playing = false;
        self.focus = false;
        if !self.fb_session.is_empty()
            || self.fb_running
            || self.fb_play_pending
            || self.busy.contains("fb_start")
        {
            self.stop_feedback();
        }
    }
    fn feedback_event(&mut self, v: Value) {
        if text(&v, "session_id") != self.fb_session
            || (v["generation"].is_number() && v["generation"].as_u64() != self.fb_backend_gen)
        {
            return;
        }
        if v["type"] == "status" {
            let status = text(&v, "status");
            if matches!(status.as_str(), "error" | "stopped") {
                let message = v["error"]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .unwrap_or("Live feedback stopped.")
                    .to_string();
                self.feedback_failed(message);
            } else if matches!(
                status.as_str(),
                "warming_up" | "silence" | "stream_gap" | "disconnected" | "paused"
            ) {
                // A transport interruption invalidates an unconfirmed match,
                // while a green already earned belongs to its chart segment.
                self.fb_cutoff = now_ms();
                let position = self.current();
                if let Some(rater) = &mut self.rater {
                    let result = rater.advance(position, self.speed);
                    if status != "paused" && result.rating == Some(Rating::Green) {
                        self.rating = Some(Rating::Green);
                    } else {
                        rater.reset(Some(self.fb_session.clone()));
                        self.rating = None;
                    }
                }
            }
            return;
        }
        if !self.fb_running || !self.playing || v["type"] != "prediction" {
            return;
        }
        let Some(age) = v["sample_age_ms"].as_f64() else {
            return;
        };
        let Some(emitted) = v["emitted_at_unix_ms"].as_f64() else {
            return;
        };
        let Some(sequence) = v["sequence"].as_i64() else {
            return;
        };
        let total_age = age + (now_ms() - emitted).max(0.0);
        if !total_age.is_finite() || emitted - age < self.fb_cutoff {
            return;
        }
        let position = self.current();
        if let Some(r) = &mut self.rater {
            let event = FeedbackEvent {
                session_id: self.fb_session.clone(),
                sequence,
                playback_time_seconds: position,
                playback_speed: self.speed,
                sample_age_ms: total_age,
                chord: text(&v, "chord"),
                input_quality: text(&v, "input_quality"),
                confidence: v["confidence"].as_f64().unwrap_or(f64::NAN),
                model: text(&v, "model"),
                quality_uncertain: v["quality_uncertain"] == true,
            };
            let result = r.observe(&event);
            self.rating = result.rating;
            if let Some(s) = &mut self.score {
                s.record(&result);
            }
        }
    }
    fn tick(&mut self) {
        self.events();
        if let Record::Countdown(start) = self.record {
            if start.elapsed().as_secs_f64() >= 4.0 {
                self.record = Record::Starting;
                self.request("record_start", 0, "POST", "/begin_recording", json!({}));
            }
        }
        if self.loaded
            && !self.busy.contains("poll")
            && !self.busy.contains("playback")
            && self.last_poll.elapsed()
                > Duration::from_millis(if self.playing { 250 } else { 1000 })
        {
            self.last_poll = Instant::now();
            self.pending.insert("poll".into(), self.playback_gen);
            self.busy.insert("poll".into());
            self.services
                .playback("poll", self.playback_gen, "status", Value::Null);
        }
        if self.pairing
            && self.pair["state"] == "pending"
            && self.pair_poll.elapsed() > Duration::from_secs(2)
            && !self.busy.contains("pairing")
        {
            self.pair_poll = Instant::now();
            self.request(
                "pairing",
                self.pair_gen,
                "GET",
                "/api/pairing/status",
                Value::Null,
            );
        }
        if self.fb_running && self.playing {
            let pos = self.current();
            if let Some(r) = &mut self.rater {
                let result = r.advance(pos, self.speed);
                self.rating = result.rating;
                if let Some(s) = &mut self.score {
                    s.record(&result);
                }
            }
        }
    }
    fn dialogs(&mut self, ctx: &Context) {
        // Open child dialogs on the next frame, after the triggering tap is consumed.
        let rename_open = self.rename;
        let delete_open = self.delete;
        if self.library && !rename_open && !delete_open {
            let modal = self.library_ui(ctx);
            if modal.should_close() && !self.rename && !self.delete {
                self.library = false;
            }
            if !self.rename && !self.delete {
                ctx.input(|i| {
                    if i.key_pressed(Key::ArrowDown) {
                        let len = if self.recordings {
                            self.takes.len()
                        } else {
                            self.files.len()
                        };
                        if len > 0 {
                            self.selected = Some(self.selected.map_or(0, |v| (v + 1).min(len - 1)));
                        }
                    }
                    if i.key_pressed(Key::ArrowUp) {
                        self.selected = Some(self.selected.unwrap_or(0).saturating_sub(1));
                    }
                    if i.key_pressed(Key::Enter) {
                        self.load_selected();
                    }
                });
            }
        }
        if rename_open {
            let saving = self.busy.contains("rename");
            let m=egui::Modal::new(Id::new("rename")).backdrop_color(Color32::from_black_alpha(153)).frame(Frame::new().fill(Color32::from_rgb(245,237,224)).stroke(Stroke::new(2.0,INK)).corner_radius(18).inner_margin(24)).show(ctx,|ui|{
                ui.set_width((ctx.screen_rect().width()*0.95).min(1000.0)-52.0);
                ui.spacing_mut().interact_size.y=64.0;
                ui.spacing_mut().button_padding=vec2(20.0,12.0);
                ui.spacing_mut().item_spacing=vec2(12.0,8.0);
                ui.style_mut().text_styles.insert(TextStyle::Button,sans(22.0,false));
                for widgets in [&mut ui.visuals_mut().widgets.inactive] { widgets.weak_bg_fill=Color32::from_rgb(245,237,224);widgets.bg_stroke=Stroke::new(2.0,INK);widgets.corner_radius=10.into(); }
                ui.label(RichText::new("Rename track").font(sans(30.0,true)));ui.add_space(4.0);ui.label(RichText::new("Title").size(20.0));
                ui.add_enabled_ui(!saving,|ui|{
                    let input_id=Id::new("rename_title_input");
                    let mut input=TextEdit::singleline(&mut self.rename_text).id(input_id).font(sans(24.0,false)).margin(vec2(12.0,18.0)).char_limit(80).desired_width(f32::INFINITY).show(ui);
                    if self.rename_select_all {
                        self.rename_select_all=false;input.response.request_focus();
                        let range=egui::text::CCursorRange::two(egui::text::CCursor::new(0),egui::text::CCursor::new(self.rename_text.chars().count()));
                        input.state.cursor.set_char_range(Some(range));input.state.clone().store(ctx,input_id);self.rename_cursor=Some(range);
                    } else if let Some(range)=input.cursor_range {self.rename_cursor=Some(range.as_ccursor_range());}
                    let mut save=input.response.lost_focus()&&ui.input(|i|i.key_pressed(Key::Enter));
                    ui.add_sized([ui.available_width(),24.0],Label::new(RichText::new(&self.library_status).color(Color32::from_rgb(163,40,40))));
                    ui.horizontal(|ui| { ui.with_layout(Layout::right_to_left(Align::Center),|ui|{
                        if ui.add_enabled(!self.rename_text.trim().is_empty(),Button::new(RichText::new("Save").color(PAPER)).fill(INK)).clicked(){save=true;}
                        if ui.button("Cancel").clicked(){self.rename=false;}
                        if ui.button("Keyboard").clicked(){self.keyboard=!self.keyboard;if self.keyboard{input.response.surrender_focus();}else{input.response.request_focus();}}
                    }); });
                    let mut edit:Option<(String,bool)>=None;
                    if self.keyboard {
                        for row in ["1234567890","qwertyuiop","asdfghjkl","zxcvbnm"] {
                            ui.horizontal(|ui|{ui.spacing_mut().item_spacing.x=8.0;let key_width=(ui.available_width()-8.0*(row.len()-1) as f32)/row.len() as f32;for ch in row.chars(){let ch=if self.shift{ch.to_ascii_uppercase()}else{ch};if ui.add_sized([key_width,64.0],Button::new(ch.to_string())).clicked(){edit=Some((ch.to_string(),false));}}});
                        }
                        ui.horizontal_wrapped(|ui|{
                            if ui.button("Shift").clicked(){self.shift=!self.shift;}
                            if ui.button("Space").clicked(){edit=Some((" ".into(),false));}
                            for ch in ["-","'","."] {if ui.button(ch).clicked(){edit=Some((ch.into(),false));}}
                            if ui.button("Del").clicked(){edit=Some((String::new(),true));}
                        });
                    }
                    if let Some((insert,backspace))=edit {
                        let len=self.rename_text.chars().count();
                        let selection=self.rename_cursor.map(|r|r.primary.index.min(r.secondary.index)..r.primary.index.max(r.secondary.index)).unwrap_or(len..len);
                        if let Some(cursor)=edit_title_selection(&mut self.rename_text,selection,&insert,backspace) {
                            let range=egui::text::CCursorRange::one(egui::text::CCursor::new(cursor));self.rename_cursor=Some(range);
                            let mut state=egui::text_edit::TextEditState::load(ctx,input_id).unwrap_or_default();state.cursor.set_char_range(Some(range));state.store(ctx,input_id);
                        }
                    }
                    if save {
                        let title=self.rename_text.trim().to_owned();
                        if title.is_empty(){self.library_status="Enter a title.".into();}
                        else if let Some(row)=self.rename_target.clone(){
                            self.rename_submitted=Some(title.clone());self.library_status="Saving…".into();
                            self.request("rename",0,"POST","/api/library/title",json!({"kind":self.rename_kind,"id":row["id"],"filename":row["name"],"title":title}));
                        }
                    }
                });
            });
            if m.should_close() && !self.busy.contains("rename") {
                self.rename = false;
            }
        }
        if delete_open {
            let m = egui::Modal::new(Id::new("delete"))
                .frame(
                    Frame::new()
                        .fill(Color32::from_rgb(245, 237, 224))
                        .stroke(Stroke::new(2.0, INK))
                        .corner_radius(18)
                        .inner_margin(26),
                )
                .show(ctx, |ui| {
                    ui.set_width(420.0_f32.min(ctx.screen_rect().width() * 0.9) - 56.0);
                    ui.spacing_mut().item_spacing = vec2(12.0, 16.0);
                    ui.spacing_mut().interact_size.y = 64.0;
                    ui.heading("Delete recording?");
                    ui.colored_label(Color32::from_rgb(163, 40, 40), &self.library_status);
                    if let Some(row) = self.selection() {
                        ui.label(format!(
                            "Are you sure you want to delete {}?\nThis cannot be undone.",
                            text(&row, "name")
                        ));
                        ui.horizontal(|ui| {
                            if button(ui, "Cancel", !self.busy.contains("delete")).clicked() {
                                self.delete = false;
                            }
                            if button(ui, "Delete Recording", !self.busy.contains("delete"))
                                .clicked()
                            {
                                self.request(
                                    "delete",
                                    0,
                                    "DELETE",
                                    &format!("/api/recordings/{}", row["id"]),
                                    json!({}),
                                );
                            }
                        });
                    }
                });
            if m.should_close() && !self.busy.contains("delete") {
                self.delete = false;
            }
        }
        if self.pairing {
            let compact = ctx.screen_rect().height() < 700.0;
            let margin = if compact { 20.0 } else { 24.0 };
            let m = egui::Modal::new(Id::new("pairing"))
                .backdrop_color(Color32::from_black_alpha(140))
                .frame(Frame::new().fill(Color32::WHITE).stroke(Stroke::new(2.0, INK))
                    .corner_radius(20).inner_margin(margin))
                .show(ctx, |ui| {
                    ui.set_width((ctx.screen_rect().width() - 40.0).min(700.0) - margin * 2.0);
                    ui.spacing_mut().item_spacing = vec2(12.0, if compact { 8.0 } else { 12.0 });
                    ui.spacing_mut().interact_size.y = 44.0;
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("Pair uploads").font(sans(28.0, true)));
                        ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                            if ui.add_sized([44.0, 44.0], Button::new(RichText::new("×").size(26.0))).clicked() {
                                self.close_pair();
                            }
                        });
                    });
                    ui.label("Scan to open the website, then enter the pairing code. Keep your phone on the same Wi-Fi as ChordSense.");
                    let website = text(&self.pair, "website_url");
                    ui.horizontal_top(|ui| {
                        if !website.is_empty() {
                            self.pair_qr.show(ui, &website, if compact { 148.0 } else { 222.0 });
                        }
                        ui.vertical(|ui| {
                            ui.set_width(ui.available_width());
                            ui.label(RichText::new("Upload website").strong());
                            if website.is_empty() {
                                ui.label("Waiting for website link…");
                            } else {
                                ui.hyperlink_to(&website, &website);
                            }
                            ui.add_space(if compact { 4.0 } else { 16.0 });
                            ui.label("Enter this code on the website:");
                            let code = text(&self.pair, "code");
                            let code = if self.pair["state"] == "pending" && code.len() == 6
                                && code.bytes().all(|b| b.is_ascii_digit()) {
                                format!("{} {}", &code[..3], &code[3..])
                            } else {
                                "—".into()
                            };
                            ui.label(RichText::new(code).monospace().size(if compact { 44.0 } else { 56.0 }));
                        });
                    });
                    ui.label(&self.pair_status);
                    if self.pair["state"] == "pending" {
                        ui.label(format!("Code expires in {}", time(self.pair_deadline.saturating_duration_since(Instant::now()).as_secs_f64())));
                    }
                    ui.horizontal_wrapped(|ui| {
                        if button(ui, "Generate new code", !self.busy.contains("pairing")).clicked() {
                            self.pair_action("start");
                        }
                        if ui.add_enabled(!self.busy.contains("pairing") && self.pair["state"] == "pending", Button::new("Cancel pairing")).clicked() {
                            self.pair_action("cancel");
                        }
                        if ui.add_enabled(!self.busy.contains("pairing"), Button::new("Disconnect all")).clicked() {
                            self.pair_action("revoke");
                        }
                    });
                    ui.label("Closing this panel cancels an unused code. Disconnect all ends every paired browser session.");
                });
            if m.should_close() {
                self.close_pair();
            }
        }
    }
    fn result_ui(&mut self, ui: &mut Ui) {
        let w = ui.ctx().screen_rect().width();
        let h = ui.ctx().screen_rect().height();
        let pad = (w * 0.024).clamp(18.0, 42.0);
        let size = vec2(ui.available_width().min(1000.0), (h * 0.88).min(850.0));
        let rect = Rect::from_center_size(ui.max_rect().center(), size);
        ui.painter().rect_filled(rect, 36, PAPER);
        ui.painter()
            .rect_stroke(rect, 36, Stroke::new(3.0, INK), StrokeKind::Inside);
        let inner = rect.shrink(pad);
        let brand_font = rakkas((w * 0.023).clamp(24.0, 36.0));
        let brand = Rect::from_min_size(
            inner.min,
            vec2(
                text_width(ui, "ChordSense", brand_font.clone()) + 36.0,
                49.0,
            ),
        );
        ui.painter().rect_filled(brand, 22, INK);
        label_at(
            ui,
            brand.center(),
            Align2::CENTER_CENTER,
            "ChordSense",
            brand_font,
            PAPER,
        );
        label_at(
            ui,
            inner.right_top() + vec2(0.0, 24.0),
            Align2::RIGHT_CENTER,
            "PLAY ALONG  —  RESULTS",
            sans((w * 0.011).clamp(11.0, 16.0), true),
            INK,
        );
        let center = inner.center();
        let scale = (inner.height() / 766.0).min(1.0);
        let y = inner.top() + 90.0 * scale;
        let badge = Rect::from_center_size(pos2(center.x, y), vec2(175.0, 36.0));
        ui.painter()
            .rect_stroke(badge, 18, Stroke::new(2.0, INK), StrokeKind::Inside);
        label_at(
            ui,
            badge.center(),
            Align2::CENTER_CENTER,
            "SONG COMPLETE",
            sans(15.0, true),
            INK,
        );
        label_at(
            ui,
            pos2(center.x, y + 64.0 * scale),
            Align2::CENTER_CENTER,
            "Your live score",
            rakkas((w * 0.044).clamp(38.0, 70.0) * scale.max(0.75)),
            INK,
        );
        let number_top = y + 124.0 * scale;
        let number_bottom = number_top + 264.0 * scale;
        let half = inner.width().min(620.0) / 2.0;
        for y in [number_top, number_bottom] {
            ui.painter().line_segment(
                [pos2(center.x - half, y), pos2(center.x + half, y)],
                Stroke::new(2.0, INK),
            );
        }
        let summary = self.score.as_ref().and_then(|s| s.summary());
        label_at(
            ui,
            pos2(center.x, number_top + 114.0 * scale),
            Align2::CENTER_CENTER,
            &summary
                .as_ref()
                .map_or("—".into(), |s| format!("{}%", s.percent)),
            rakkas((w * 0.14).clamp(96.0, 216.0) * scale),
            INK,
        );
        label_at(
            ui,
            pos2(center.x, number_bottom - 28.0 * scale),
            Align2::CENTER_CENTER,
            "CHORD MATCH SCORE",
            sans((w * 0.012).clamp(12.0, 18.0), true),
            INK,
        );
        label_at(
            ui,
            pos2(center.x, number_bottom + 44.0 * scale),
            Align2::CENTER_CENTER,
            &self.name,
            sans((w * 0.02).clamp(18.0, 28.0), true),
            INK,
        );
        if let Some(s) = summary {
            label_at(
                ui,
                pos2(center.x, number_bottom + 76.0 * scale),
                Align2::CENTER_CENTER,
                &format!("{} chord segments scored", s.total),
                sans((w * 0.013).clamp(14.0, 19.0), false),
                blend(INK, PAPER, 0.72),
            );
        }
        let action_y = number_bottom + 110.0 * scale;
        if css_button(
            ui,
            Rect::from_min_size(pos2(center.x - 226.0, action_y), vec2(220.0, 52.0)),
            "replay",
            "Play again",
            sans(20.0, true),
            INK,
            PAPER,
            26,
            2.0,
            true,
            1.0,
        )
        .clicked()
        {
            self.results = false;
            self.seek(0.0);
            self.score = None;
            self.play();
        }
        if css_button(
            ui,
            Rect::from_min_size(pos2(center.x + 6.0, action_y), vec2(220.0, 52.0)),
            "back-to-song",
            "Back to song",
            sans(20.0, true),
            PAPER,
            INK,
            26,
            2.0,
            true,
            1.0,
        )
        .clicked()
        {
            self.results = false;
        }
        label_at(
            ui,
            pos2(center.x, inner.bottom() - 8.0),
            Align2::CENTER_CENTER,
            "Every chord is another chance to find the groove.",
            sans((w * 0.0105).clamp(12.0, 16.0), true),
            INK,
        );
    }
}
impl eframe::App for ChordSense {
    fn update(&mut self, ctx: &Context, frame: &mut eframe::Frame) {
        self.tick();
        let screen = ctx.screen_rect();
        let w = screen.width();
        let h = screen.height();
        // Fullscreen is a window command, even when a control owns keyboard focus.
        if ctx.input(|i| i.key_pressed(Key::F11)) {
            ctx.send_viewport_cmd(ViewportCommand::Fullscreen(
                !ctx.input(|i| i.viewport().fullscreen.unwrap_or(false)),
            ));
        }
        if !ctx.wants_keyboard_input() && !self.library && !self.pairing && !self.results {
            ctx.input(|i| {
                if i.key_pressed(Key::M) && self.record == Record::Idle {
                    self.stop();
                    self.record_mode = !self.record_mode;
                }
                if i.key_pressed(Key::Escape) {
                    self.focus = false;
                }
            });
        }
        CentralPanel::default()
            .frame(Frame::new().fill(CREAM).inner_margin(Margin::symmetric(
                if self.focus {
                    (w * 0.02).clamp(12.0, 32.0) as i8
                } else {
                    (w * 0.03).clamp(18.0, 56.0) as i8
                },
                if self.focus {
                    (h * 0.015).clamp(10.0, 18.0) as i8
                } else if h <= 800.0 {
                    14
                } else {
                    (h * 0.02).clamp(16.0, 32.0) as i8
                },
            )))
            .show(ctx, |ui| {
                if self.results {
                    self.result_ui(ui);
                } else {
                    ui.spacing_mut().item_spacing.y = 0.0;
                    self.header(ui, w, h);
                    if self.record_mode {
                        self.record_ui(ui, w, h);
                    } else {
                        self.play_ui(ui, w, h);
                    }
                }
            });
        if self.fb_running && self.playing && !self.results {
            if let Some(rating) = self.rating {
                let color = match rating {
                    Rating::Green => Some(Color32::from_rgb(48, 155, 80)),
                    Rating::Red => Some(Color32::from_rgb(244, 55, 55)),
                    _ => None,
                };
                if let Some(color) = color {
                    let p = ctx
                        .layer_painter(LayerId::new(Order::Foreground, Id::new("feedback_border")));
                    p.rect_filled(screen, 0, color.linear_multiply(0.16));
                    p.rect_stroke(
                        screen,
                        0,
                        Stroke::new(14.0, color),
                        StrokeKind::Inside,
                    );
                }
            }
        }
        self.dialogs(ctx);
        // Apply after widgets and dialogs so hover/text cursors cannot reveal it.
        ctx.set_cursor_icon(CursorIcon::None);
        ctx.send_viewport_cmd(ViewportCommand::CursorVisible(false));
        if self.picker_requested && !self.picker_open {
            self.picker_requested = false;
            self.picker_open = true;
            let picker = rfd::FileDialog::new()
                .set_parent(frame)
                .set_title("Choose an audio file")
                .add_filter("Audio", &["wav", "mp3", "ogg", "flac", "m4a"]);
            let tx = self.picker_tx.clone();
            let repaint = ctx.clone();
            std::thread::spawn(move || {
                let _ = tx.send(picker.pick_file());
                repaint.request_repaint();
            });
        }
        ctx.request_repaint_after(Duration::from_millis(
            if self.playing || matches!(self.record, Record::Recording(_) | Record::Countdown(_)) {
                16
            } else {
                100
            },
        ));
    }
    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        if !self.services.shutdown() {
            eprintln!("Some device cleanup requests did not finish; check the backend before starting another capture.");
        }
    }
}

#[cfg(test)]
mod response_tests {
    use super::{current_event, retire_pending_reply};
    use std::collections::{HashMap, HashSet};

    #[test]
    fn old_playback_completion_does_not_unlock_newer_seek() {
        let mut pending = HashMap::from([("playback".to_owned(), 8), ("poll".to_owned(), 7)]);
        let mut busy = HashSet::from(["playback".to_owned(), "poll".to_owned()]);
        retire_pending_reply(&mut pending, &mut busy, "playback", 7);
        assert!(busy.contains("playback"));
        assert_eq!(pending.get("playback"), Some(&8));
        // The obsolete poll still needs its own busy bit retired, even though
        // its stale player position will not be applied to the new seek.
        retire_pending_reply(&mut pending, &mut busy, "poll", 7);
        assert!(!busy.contains("poll"));
        assert!(!current_event("poll", 7, 1, 8, 0, 0));
        retire_pending_reply(&mut pending, &mut busy, "playback", 8);
        assert!(busy.is_empty());
        assert!(pending.is_empty());
    }
    #[test]
    fn source_loading_is_independent_of_transport_commands() {
        // Stop and volume change the playback generation while a source loads.
        assert!(current_event("loaded", 3, 3, 18, 0, 0));
        assert!(current_event("analyze", 3, 3, 18, 0, 0));
        // Selecting another song makes both its old load and analysis obsolete.
        assert!(!current_event("loaded", 3, 4, 19, 0, 0));
        assert!(!current_event("analyze", 3, 4, 19, 0, 0));
    }
    #[test]
    fn restarting_feedback_rejects_old_stream_controls_and_stop_errors() {
        for tag in [
            "fb_start",
            "fb_pause",
            "fb_resume",
            "feedback_event",
            "feedback_stop",
        ] {
            assert!(!current_event(tag, 4, 1, 1, 2, 6));
            assert!(current_event(tag, 6, 1, 1, 2, 6));
        }
        assert!(!current_event("pairing", 1, 1, 1, 2, 6));
        assert!(current_event("pairing", 2, 1, 1, 2, 6));
    }
}

#[cfg(test)]
mod rename_tests {
    use super::edit_title_selection;
    #[test]
    fn touchscreen_replaces_selected_text_and_preserves_unicode() {
        let mut title = "A😀曲Z".to_owned();
        assert_eq!(edit_title_selection(&mut title, 1..3, "é", false), Some(2));
        assert_eq!(title, "AéZ");
        assert_eq!(edit_title_selection(&mut title, 2..2, "", true), Some(1));
        assert_eq!(title, "AZ");
    }
    #[test]
    fn touchscreen_backspace_removes_selection_or_one_complete_character() {
        let mut title = "x😀z".to_owned();
        assert_eq!(edit_title_selection(&mut title, 2..2, "", true), Some(1));
        assert_eq!(title, "xz");
        assert_eq!(edit_title_selection(&mut title, 0..2, "", true), Some(0));
        assert_eq!(title, "");
        assert_eq!(edit_title_selection(&mut title, 0..0, "", true), Some(0));
    }
    #[test]
    fn title_limit_allows_replacing_at_capacity_but_rejects_appending() {
        let mut title = "a".repeat(80);
        assert_eq!(edit_title_selection(&mut title, 80..80, "b", false), None);
        assert_eq!(title.chars().count(), 80);
        assert_eq!(edit_title_selection(&mut title, 0..1, "😀", false), Some(1));
        assert_eq!(title.chars().count(), 80);
        assert!(title.starts_with('😀'));
    }
}

include!("appearance.rs");

#[cfg(test)]
mod screen_tests {
    use super::*;
    use eframe::App;

    #[test]
    fn pi_views_fit_without_clipping_or_page_scrolling() {
        let ctx = Context::default();
        let mut app = ChordSense::new(&eframe::CreationContext::_new_kittest(ctx.clone()));
        let mut frame = eframe::Frame::_new_kittest();
        let screen = Rect::from_min_size(Pos2::ZERO, vec2(1920.0, 1200.0));
        for view in [
            "empty", "chords", "focus", "record", "countdown", "recording", "processing", "results",
        ] {
            app.focus = view == "focus";
            app.record_mode = matches!(view, "record" | "countdown" | "recording" | "processing");
            app.results = view == "results";
            app.record = match view {
                "countdown" => Record::Countdown(Instant::now()),
                "recording" => Record::Recording(Instant::now()),
                "processing" => Record::Processing,
                _ => Record::Idle,
            };
            app.timeline = if view == "empty" {
                vec![]
            } else {
                ["C", "G", "Am"]
                    .iter()
                    .enumerate()
                    .map(|(i, chord)| Chord {
                        start: i as f64 * 10.0,
                        end: (i + 1) as f64 * 10.0,
                        chord: (*chord).into(),
                        confidence: 1.0,
                    })
                    .collect()
            };
            for _ in 0..2 {
                let output = ctx.run(
                    RawInput { screen_rect: Some(screen), ..Default::default() },
                    |ctx| {
                        app.update(ctx, &mut frame);
                        assert!(
                            screen.expand(1.0).contains_rect(ctx.used_rect()),
                            "{view}: layout exceeds screen: {:?}", ctx.used_rect(),
                        );
                    },
                );
                for shape in output.shapes {
                    let bounds = shape.shape.visual_bounding_rect();
                    if bounds.is_positive() {
                        assert!(
                            screen.expand(1.0).contains_rect(bounds),
                            "{view}: content exceeds screen: {bounds:?}",
                        );
                    }
                }
            }
        }
    }

    fn dialog_frame(app: &mut ChordSense, ctx: &Context, events: Vec<egui::Event>) -> egui::FullOutput {
        ctx.run(RawInput {
            screen_rect: Some(Rect::from_min_size(Pos2::ZERO, vec2(1920.0, 1200.0))),
            events,
            ..Default::default()
        }, |ctx| app.update(ctx, &mut eframe::Frame::_new_kittest()))
    }

    fn tap_label(app: &mut ChordSense, ctx: &Context, label: &str) {
        let mut output = dialog_frame(app, ctx, vec![]);
        for _ in 0..3 { output = dialog_frame(app, ctx, vec![]); }
        let pos = output.shapes.iter().find_map(|shape| match &shape.shape {
            Shape::Text(text) if text.galley.text() == label => Some(text.visual_bounding_rect().center()),
            _ => None,
        }).unwrap_or_else(|| panic!("Missing button: {label}"));
        for pressed in [true, false] {
            dialog_frame(app, ctx, vec![egui::Event::PointerMoved(pos), egui::Event::PointerButton {
                pos, button: PointerButton::Primary, pressed, modifiers: Modifiers::NONE,
            }]);
        }
    }

    #[test]
    fn recording_dialogs_open_and_touch_keyboard_edits_title() {
        let ctx = Context::default();
        let mut app = ChordSense::new(&eframe::CreationContext::_new_kittest(ctx.clone()));
        // Exercise UI requests without touching recordings on a running device.
        assert!(app.services.shutdown());
        app.library = true;
        app.recordings = true;
        app.takes = vec![json!({"id": 123, "name": "Test recording", "title": "Test recording"})];
        tap_label(&mut app, &ctx, "Delete");
        assert!(app.delete, "Opening tap must not immediately dismiss delete dialog");
        tap_label(&mut app, &ctx, "Cancel");
        assert!(!app.delete);
        app.selected = Some(0);
        tap_label(&mut app, &ctx, "Rename");
        assert!(app.rename);
        tap_label(&mut app, &ctx, "Keyboard");
        assert!(app.keyboard);
        let output = dialog_frame(&mut app, &ctx, vec![]);
        for shape in output.shapes {
            let bounds = shape.shape.visual_bounding_rect();
            if bounds.is_positive() {
                assert!(Rect::from_min_size(Pos2::ZERO, vec2(1920.0, 1200.0)).expand(1.0).contains_rect(bounds), "Keyboard outside display: {bounds:?}");
            }
        }
        tap_label(&mut app, &ctx, "q");
        assert_eq!(app.rename_text, "q");
        tap_label(&mut app, &ctx, "Space");
        tap_label(&mut app, &ctx, "w");
        assert_eq!(app.rename_text, "q w");
        tap_label(&mut app, &ctx, "Del");
        assert_eq!(app.rename_text, "q ");
        tap_label(&mut app, &ctx, "w");
        tap_label(&mut app, &ctx, "Save");
        assert!(app.busy.contains("rename"));
        assert_eq!(app.rename_submitted.as_deref(), Some("q w"));
    }

    #[test]
    fn recording_delete_confirmation_submits_request() {
        let ctx = Context::default();
        let mut app = ChordSense::new(&eframe::CreationContext::_new_kittest(ctx.clone()));
        let requests = app.services.capture_requests();
        app.library = true;
        app.recordings = true;
        app.takes = (0..26).map(|i| json!({"id": 123 + i, "name": format!("Test recording {i}")})).collect();
        // Touchscreens can retain the location of the preceding tap as a mouse
        // position. The new touch must not scroll the target out from under us.
        dialog_frame(&mut app, &ctx, vec![egui::Event::PointerMoved(pos2(1000.0, 950.0))]);
        tap_label(&mut app, &ctx, "Delete");
        assert!(app.delete);
        assert_eq!(app.selected, Some(0));
        tap_label(&mut app, &ctx, "Delete Recording");
        assert!(app.busy.contains("delete"));
        let (method, path, body) = requests.try_recv().expect("delete HTTP request");
        assert_eq!(method, "DELETE");
        assert_eq!(path, "/api/recordings/123");
        assert!(body.is_object(), "The API rejects JSON null: {body}");
        assert!(requests.try_recv().is_err(), "One confirmation must send one delete");
    }

    #[test]
    fn pairing_qr_link_and_code_fit_large_and_compact_displays() {
        for size in [vec2(1920.0, 1200.0), vec2(800.0, 480.0)] {
            let ctx = Context::default();
            let mut app = ChordSense::new(&eframe::CreationContext::_new_kittest(ctx.clone()));
            assert!(app.services.shutdown());
            app.pairing = true;
            app.pair = json!({
                "website_url": "https://chordsense-phi.vercel.app",
                "state": "pending", "code": "123456",
            });
            app.pair_status = "Waiting for your browser to pair…".into();
            app.pair_deadline = Instant::now() + Duration::from_secs(120);
            let screen = Rect::from_min_size(Pos2::ZERO, size);
            let mut qr_uploaded = false;
            for _ in 0..4 {
                let output = ctx.run(RawInput { screen_rect: Some(screen), ..Default::default() }, |ctx| app.dialogs(ctx));
                qr_uploaded |= output.textures_delta.set.iter().any(|(_, delta)| {
                    let size = delta.image.size();
                    size[0] == size[1] && size[0] >= 29 && size[0] < 200
                });
            }
            assert!(qr_uploaded, "The pairing dialog must upload its QR image");
            let output = ctx.run(RawInput { screen_rect: Some(screen), ..Default::default() }, |ctx| app.dialogs(ctx));
            for label in ["https://chordsense-phi.vercel.app", "123 456", "Generate new code", "Disconnect all"] {
                assert!(output.shapes.iter().any(|s| matches!(&s.shape, Shape::Text(t) if t.galley.text() == label)), "Missing {label}");
            }
            for shape in output.shapes {
                let bounds = shape.shape.visual_bounding_rect();
                if bounds.is_positive() {
                    assert!(screen.expand(1.0).contains_rect(bounds), "Pairing dialog outside {size:?}: {bounds:?}");
                }
            }
        }
    }

    fn pointer_button(pos: Pos2, pressed: bool) -> egui::Event {
        egui::Event::PointerButton {
            pos, button: PointerButton::Primary, pressed, modifiers: Modifiers::NONE,
        }
    }

    #[test]
    fn library_scrolls_by_swiping_without_scroll_buttons() {
        let ctx = Context::default();
        let mut app = ChordSense::new(&eframe::CreationContext::_new_kittest(ctx.clone()));
        assert!(app.services.shutdown());
        app.library = true;
        app.recordings = true;
        app.takes = (0..40).map(|i| json!({"id": i, "name": format!("Take {i}")})).collect();
        let mut output = dialog_frame(&mut app, &ctx, vec![]);
        for _ in 0..3 { output = dialog_frame(&mut app, &ctx, vec![]); }
        let row_center = |output: &egui::FullOutput| output.shapes.iter().find_map(|s| match &s.shape {
            Shape::Text(t) if t.galley.text() == "Take 4" => Some(t.visual_bounding_rect().center()),
            _ => None,
        }).expect("visible recording row");
        assert!(!output.shapes.iter().any(|s| matches!(&s.shape,
            Shape::Text(t) if t.galley.text().contains("Scroll up") || t.galley.text().contains("Scroll down")
        )));
        let start = row_center(&output);
        dialog_frame(&mut app, &ctx, vec![egui::Event::PointerMoved(start), pointer_button(start, true)]);
        for distance in [30.0, 90.0, 150.0] {
            dialog_frame(&mut app, &ctx, vec![egui::Event::PointerMoved(start - vec2(0.0, distance))]);
        }
        output = dialog_frame(&mut app, &ctx, vec![]);
        assert!(row_center(&output).y < start.y - 50.0, "Swipe must move the recording list");
        dialog_frame(&mut app, &ctx, vec![pointer_button(start - vec2(0.0, 150.0), false)]);
        assert!(!app.delete);
    }

    #[test]
    fn touching_recordings_does_not_jump_to_the_previous_pointer_position() {
        let ctx = Context::default();
        let mut app = ChordSense::new(&eframe::CreationContext::_new_kittest(ctx.clone()));
        assert!(app.services.shutdown());
        app.library = true;
        app.recordings = true;
        app.takes = (0..40).map(|i| json!({"id": i, "name": format!("Take {i}")})).collect();
        let row_center = |output: &egui::FullOutput| output.shapes.iter().find_map(|s| match &s.shape {
            Shape::Text(t) if t.galley.text() == "Take 4" => Some(t.visual_bounding_rect().center()),
            _ => None,
        }).expect("recording row");
        for _ in 0..4 { dialog_frame(&mut app, &ctx, vec![]); }
        let output = dialog_frame(&mut app, &ctx, vec![egui::Event::PointerMoved(pos2(1000.0, 950.0))]);
        let start = row_center(&output);
        dialog_frame(&mut app, &ctx, vec![egui::Event::PointerMoved(start), pointer_button(start, true)]);
        let output = dialog_frame(&mut app, &ctx, vec![]);
        assert!((row_center(&output).y - start.y).abs() < 0.5, "Touch-down alone must not move the list");
        dialog_frame(&mut app, &ctx, vec![egui::Event::PointerMoved(start - vec2(0.0, 40.0))]);
        let output = dialog_frame(&mut app, &ctx, vec![]);
        assert!((row_center(&output).y - (start.y - 40.0)).abs() < 1.0, "The list must follow just this swipe's movement");
        let end = start - vec2(0.0, 40.0);
        dialog_frame(&mut app, &ctx, vec![pointer_button(end, false), egui::Event::PointerGone]);
        // Start a second swipe elsewhere, as a real finger does after lifting.
        let output = dialog_frame(&mut app, &ctx, vec![]);
        let before = row_center(&output);
        let next = before + vec2(0.0, 90.0);
        dialog_frame(&mut app, &ctx, vec![egui::Event::Touch {
            device_id: egui::TouchDeviceId(1), id: egui::TouchId(2),
            phase: egui::TouchPhase::Start, pos: next, force: None,
        }, egui::Event::PointerMoved(next), pointer_button(next, true)]);
        let output = dialog_frame(&mut app, &ctx, vec![]);
        assert!((row_center(&output).y - before.y).abs() < 0.5);
        let moved = next - vec2(0.0, 50.0);
        dialog_frame(&mut app, &ctx, vec![egui::Event::Touch {
            device_id: egui::TouchDeviceId(1), id: egui::TouchId(2),
            phase: egui::TouchPhase::Move, pos: moved, force: None,
        }, egui::Event::PointerMoved(moved)]);
        let output = dialog_frame(&mut app, &ctx, vec![]);
        assert!((row_center(&output).y - (before.y - 50.0)).abs() < 1.0);
        dialog_frame(&mut app, &ctx, vec![egui::Event::Touch {
            device_id: egui::TouchDeviceId(1), id: egui::TouchId(2),
            phase: egui::TouchPhase::End, pos: moved, force: None,
        }, pointer_button(moved, false), egui::Event::PointerGone]);
        assert!(app.selected.is_none(), "Swiping must not select a recording");
        assert!(!app.delete, "Swiping must not open deletion");

        let output = dialog_frame(&mut app, &ctx, vec![]);
        let list_clip = output.shapes.iter().find_map(|s| match &s.shape {
            Shape::Text(t) if t.galley.text() == "Delete" => Some(s.clip_rect),
            _ => None,
        }).unwrap();
        for shape in &output.shapes {
            if matches!(&shape.shape, Shape::Text(t) if t.galley.text().starts_with("Take "))
                && shape.clip_rect.is_positive() {
                assert!(list_clip.contains_rect(shape.clip_rect), "Recording title escapes the scrolling list");
            }
        }
    }

    #[test]
    fn live_feedback_border_touches_all_screen_edges() {
        let ctx = Context::default();
        let mut app = ChordSense::new(&eframe::CreationContext::_new_kittest(ctx.clone()));
        assert!(app.services.shutdown());
        app.fb_running = true;
        app.playing = true;
        let screen = Rect::from_min_size(Pos2::ZERO, vec2(1920.0, 1200.0));
        for (rating, color) in [
            (Rating::Green, Color32::from_rgb(48, 155, 80)),
            (Rating::Red, Color32::from_rgb(244, 55, 55)),
        ] {
            app.rating = Some(rating);
            let output = dialog_frame(&mut app, &ctx, vec![]);
            assert!(output.shapes.iter().any(|s| matches!(&s.shape,
                Shape::Rect(r) if r.stroke.color == color
                    && r.stroke.width == 14.0 && r.rect == screen
                    && r.stroke_kind == StrokeKind::Inside
            )));
        }
    }

    #[test]
    fn song_seek_keeps_drag_position_and_commits_release_or_tap() {
        for playing in [false, true] {
            for focus in [false, true] {
                let ctx = Context::default();
                let mut app = ChordSense::new(&eframe::CreationContext::_new_kittest(ctx.clone()));
                assert!(app.services.shutdown());
                app.loaded = true;
                app.duration = 100.0;
                app.position = 80.0;
                app.playing = playing;
                app.focus = focus;
                let mut output = dialog_frame(&mut app, &ctx, vec![]);
                for _ in 0..3 { output = dialog_frame(&mut app, &ctx, vec![]); }
                let track = output.shapes.iter().find_map(|s| match &s.shape {
                    Shape::Rect(r) if r.fill == Color32::from_gray(239)
                        && r.rect.width() > 250.0 => Some(r.rect),
                    _ => None,
                }).expect("seek track");
                let thumb = output.shapes.iter().find_map(|s| match &s.shape {
                    Shape::Circle(c) if c.fill == Color32::from_rgb(0, 117, 255)
                        && track.contains(c.center) => Some(c.center),
                    _ => None,
                }).expect("seek thumb");
                let point = |fraction| pos2(track.left() + 6.0 + (track.width() - 12.0) * fraction, thumb.y);
                dialog_frame(&mut app, &ctx, vec![egui::Event::PointerMoved(thumb), pointer_button(thumb, true)]);
                dialog_frame(&mut app, &ctx, vec![egui::Event::PointerMoved(point(0.3))]);
                assert!((app.seek_preview.expect(&format!("drag: playing={playing}, focus={focus}, thumb={thumb:?}, track={track:?}")) - 30.0).abs() < 0.01);
                assert_eq!(app.playback_gen, 0, "Dragging should preview before submitting a seek");
                // A playback update must not overwrite the position under the finger.
                app.position = 85.0;
                dialog_frame(&mut app, &ctx, vec![]);
                assert!((app.seek_preview.unwrap() - 30.0).abs() < 0.01);
                dialog_frame(&mut app, &ctx, vec![egui::Event::PointerMoved(point(0.2)), pointer_button(point(0.2), false)]);
                assert!(app.seek_preview.is_none());
                assert!((app.position - 20.0).abs() < 0.01, "Seek must use the final release position, got {}", app.position);
                assert_eq!(app.last_playback_action, "seek");
                assert_eq!(app.playback_gen, 1);
                assert_eq!(app.playing, playing);
                for pressed in [true, false] {
                    dialog_frame(&mut app, &ctx, vec![egui::Event::PointerMoved(point(0.7)), pointer_button(point(0.7), pressed)]);
                }
                assert!((app.position - 70.0).abs() < 0.01);
                assert_eq!(app.playback_gen, 2);
            }
        }
    }

    #[test]
    fn cursor_stays_hidden_over_buttons_and_dialogs() {
        let ctx = Context::default();
        let mut app = ChordSense::new(&eframe::CreationContext::_new_kittest(ctx.clone()));
        let mut frame = eframe::Frame::_new_kittest();
        for library in [false, true] {
            app.library = library;
            for pointer in [pos2(150.0, 270.0), pos2(1820.0, 80.0), pos2(960.0, 600.0)] {
                for _ in 0..2 {
                    let output = ctx.run(
                        RawInput {
                            screen_rect: Some(Rect::from_min_size(Pos2::ZERO, vec2(1920.0, 1200.0))),
                            events: vec![egui::Event::PointerMoved(pointer)],
                            ..Default::default()
                        },
                        |ctx| app.update(ctx, &mut frame),
                    );
                    assert_eq!(output.platform_output.cursor_icon, CursorIcon::None);
                    assert!(output.viewport_output[&ViewportId::ROOT]
                        .commands
                        .iter()
                        .any(|cmd| matches!(cmd, ViewportCommand::CursorVisible(false))));
                }
            }
        }
    }
}
