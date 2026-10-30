//! Native tuner state. The backend owns pitch estimation; this owns display timing.
use serde_json::{json, Value};

pub const STANDARD_TUNING: [i32; 6] = [40, 45, 50, 55, 59, 64];
const A4_HZ: i32 = 440;
pub fn note(midi: i32) -> String {
    let name = [
        "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
    ][midi.rem_euclid(12) as usize];
    format!("{name}{}", midi.div_euclid(12) - 1)
}
#[derive(Clone, Copy)]
pub struct Reading {
    pub midi: i32,
    pub cents: f64,
    pub hz: f64,
}
pub struct Tuner {
    pub string: Option<usize>,
    pub session: String,
    pub quality: String,
    pub error: String,
    reading: Option<Reading>,
    heard_at: f64,
    in_tune_since: Option<f64>,
    pub locked: bool,
}
impl Default for Tuner {
    fn default() -> Self {
        Self {
            string: None,
            session: String::new(),
            quality: "warming_up".into(),
            error: String::new(),
            reading: None,
            heard_at: f64::NEG_INFINITY,
            in_tune_since: None,
            locked: false,
        }
    }
}
impl Tuner {
    pub fn target(&self) -> Option<i32> {
        self.string.map(|s| STANDARD_TUNING[s])
    }
    pub fn settings(&self) -> Value {
        json!({"a4_hz": A4_HZ, "target_midi": self.target()})
    }
    pub fn clear_reading(&mut self) {
        self.reading = None;
        self.in_tune_since = None;
        self.locked = false;
    }
    pub fn clear(&mut self) {
        self.clear_reading();
        self.session.clear();
        self.error.clear();
        self.quality = "warming_up".into();
    }
    pub fn fail(&mut self, error: String) {
        self.clear_reading();
        self.quality = "error".into();
        self.error = error;
    }
    pub fn display(&self, now: f64) -> Option<Reading> {
        if now - self.heard_at > 600.0 {
            None
        } else {
            self.reading
        }
    }
    pub fn observe(&mut self, v: &Value, now: f64) {
        if v["type"] == "status" {
            self.quality = v["status"].as_str().unwrap_or("listening").into();
            if self.quality != "listening" {
                self.in_tune_since = None;
                self.locked = false;
            }
            if matches!(
                self.quality.as_str(),
                "error" | "stopped" | "disconnected" | "clipping" | "stream_gap" | "warming_up"
            ) {
                self.clear_reading();
            }
            self.error = v["error"].as_str().unwrap_or_default().into();
            return;
        }
        if v["type"] != "pitch" {
            return;
        }
        // Ignore queued frames from an earlier string/reference configuration.
        if v["a4_hz"].as_f64() != Some(A4_HZ as f64)
            || v["target_midi"] != json!(self.target())
            || v["emitted_at_unix_ms"]
                .as_f64()
                .is_some_and(|at| now - at > 600.0)
            || v["sample_age_ms"].as_f64().is_some_and(|age| age > 600.0)
        {
            return;
        }
        let (Some(midi), Some(offset), Some(hz)) = (
            v["midi_note"].as_i64(),
            v["cents"].as_f64(),
            v["f0_hz"].as_f64(),
        ) else {
            return;
        };
        if !offset.is_finite() || !hz.is_finite() || hz <= 0.0 {
            return;
        }
        let reference = self.target().unwrap_or(midi as i32);
        let cents = (midi as f64 - reference as f64) * 100.0 + offset;
        if now - self.heard_at > 600.0 || self.reading.is_none_or(|r| r.midi != reference) {
            self.in_tune_since = None;
            self.locked = false;
        }
        self.reading = Some(Reading {
            midi: reference,
            cents,
            hz,
        });
        self.heard_at = now;
        self.quality = "listening".into();
        if cents.abs() <= 3.0 {
            let since = *self.in_tune_since.get_or_insert(now);
            if now - since >= 500.0 {
                self.locked = true;
            }
        } else {
            self.in_tune_since = None;
            if cents.abs() > 5.0 {
                self.locked = false;
            }
        }
    }
    pub fn hint(&self, now: f64) -> &str {
        match self.quality.as_str() {
            "error" | "disconnected" | "stopped" => "Guitar input unavailable. Tap Retry.",
            "clipping" => "Input too loud. Turn down the guitar volume.",
            "warming_up" => "Starting tuner…",
            "stream_gap" => "Reconnecting to guitar input…",
            _ => match self.display(now) {
                None => "Play a single string and let it ring.",
                Some(_) if self.locked => "In tune",
                Some(r) if r.cents.abs() <= 3.0 => "Hold steady…",
                Some(r) if r.cents < 0.0 => "Flat — tune up",
                Some(_) => "Sharp — tune down",
            },
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn pitch(midi: i32, cents: f64) -> Value {
        json!({"type":"pitch", "midi_note":midi, "cents":cents, "f0_hz":110.0,
            "a4_hz":440.0, "target_midi":null})
    }
    #[test]
    fn lock_requires_dwell_and_releases_after_silence_or_note_change() {
        let mut t = Tuner::default();
        t.observe(&pitch(45, 1.0), 0.0);
        t.observe(&pitch(45, 1.0), 500.0);
        assert!(t.locked);
        t.observe(&pitch(45, 4.0), 510.0);
        assert!(t.locked);
        t.observe(&pitch(45, 6.0), 520.0);
        assert!(!t.locked);
        t.observe(&pitch(45, 1.0), 530.0);
        t.observe(&pitch(45, 1.0), 1030.0);
        assert!(t.locked);
        assert!(t.display(1631.0).is_none());
        t.observe(&pitch(45, 1.0), 1700.0);
        assert!(!t.locked);
        t.observe(&pitch(45, 1.0), 2200.0);
        t.observe(&pitch(50, 1.0), 2210.0);
        assert!(!t.locked);
    }
    #[test]
    fn selected_string_measures_offset_from_target_and_rejects_old_settings() {
        let mut t = Tuner::default();
        t.string = Some(0);
        t.observe(&pitch(41, -10.0), 0.0);
        assert!(t.display(0.0).is_none());
        let mut v = pitch(41, -10.0);
        v["target_midi"] = json!(40);
        t.observe(&v, 0.0);
        let r = t.display(0.0).unwrap();
        assert_eq!(r.midi, 40);
        assert_eq!(r.cents, 90.0);
        t.observe(&json!({"type":"status","status":"clipping"}), 10.0);
        assert!(t.display(10.0).is_none());
    }
    #[test]
    fn unclear_frames_hold_only_briefly() {
        let mut t = Tuner::default();
        t.observe(&pitch(45, -7.0), 0.0);
        t.observe(&json!({"type":"pitch","note":null}), 100.0);
        assert!(t.display(600.0).is_some());
        assert!(t.display(601.0).is_none());
        assert_eq!(note(40), "E2");
        assert_eq!(note(64), "E4");
    }
}
