//! Chord charts, capo shapes and temporal feedback. No rendering or I/O.
//! The timing rules deliberately mirror the previous device interface.
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Chord {
    pub start: f64,
    pub end: f64,
    pub chord: String,
    #[serde(default = "default_confidence")]
    pub confidence: f64,
}
fn default_confidence() -> f64 {
    1.0
}

pub fn format_time(seconds: f64) -> String {
    let seconds = if seconds.is_finite() {
        seconds.max(0.0).floor() as u64
    } else {
        0
    };
    format!("{:02}:{:02}", seconds / 60, seconds % 60)
}

fn root_and_quality(label: &str) -> Option<(&str, &str)> {
    let bytes = label.as_bytes();
    if !matches!(bytes.first(), Some(b'A'..=b'G' | b'a'..=b'g')) {
        return None;
    }
    let length = if matches!(bytes.get(1), Some(b'#' | b'b')) {
        2
    } else {
        1
    };
    Some((&label[..length], &label[length..]))
}
fn pitch_class(root: &str) -> Option<i32> {
    match root {
        "C" | "B#" => Some(0),
        "C#" | "Db" => Some(1),
        "D" => Some(2),
        "D#" | "Eb" => Some(3),
        "E" | "Fb" => Some(4),
        "F" | "E#" => Some(5),
        "F#" | "Gb" => Some(6),
        "G" => Some(7),
        "G#" | "Ab" => Some(8),
        "A" => Some(9),
        "A#" | "Bb" => Some(10),
        "B" | "Cb" => Some(11),
        _ => None,
    }
}
fn transpose_note(root: &str, semitones: i32) -> Option<&'static str> {
    // Preserve the capo module's original set of supported note spellings.
    if matches!(root, "B#" | "Cb" | "Fb" | "E#") {
        return None;
    }
    const NOTES: [&str; 12] = [
        "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B",
    ];
    Some(NOTES[(pitch_class(root)? + semitones).rem_euclid(12) as usize])
}
fn transpose_part(label: &str, semitones: i32) -> String {
    let trimmed = label.trim();
    if let Some((root, quality)) = root_and_quality(trimmed) {
        if let Some(note) = transpose_note(root, semitones) {
            return format!("{note}{quality}");
        }
    }
    trimmed.to_owned()
}
pub fn chord_for_capo(label: &str, capo: i32) -> String {
    let label = label.trim();
    if matches!(label, "" | "N" | "NC" | "N.C.") {
        return label.to_owned();
    }
    let mut parts = label.split('/');
    let main = transpose_part(parts.next().unwrap_or_default(), -capo);
    match parts
        .next()
        .and_then(|bass| transpose_note(bass.trim(), -capo))
    {
        Some(bass) => format!("{main}/{bass}"),
        None => main,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Quality {
    Major,
    Minor,
    Dominant7,
    Major7,
    Minor7,
}
fn quality(value: &str) -> Option<Quality> {
    match value {
        "" | "maj" | "major" => Some(Quality::Major),
        "m" | "min" | "minor" => Some(Quality::Minor),
        "7" | "dom7" => Some(Quality::Dominant7),
        "maj7" | "major7" => Some(Quality::Major7),
        "m7" | "min7" | "minor7" => Some(Quality::Minor7),
        _ => None,
    }
}
fn suffix(q: Quality) -> &'static str {
    match q {
        Quality::Major => "",
        Quality::Minor => "m",
        Quality::Dominant7 => "7",
        Quality::Major7 => "maj7",
        Quality::Minor7 => "m7",
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NormalizedChord {
    pub pitch_class: i32,
    pub quality: Quality,
}
pub fn normalize_chord(label: &str) -> Option<NormalizedChord> {
    let base = label.trim().split('/').next()?;
    let (root, text) = root_and_quality(base)?;
    let root = format!("{}{}", root[..1].to_ascii_uppercase(), &root[1..]);
    let text = if let Some(text) = text.strip_prefix(':') {
        text
    } else if matches!(text, "" | "m" | "m7" | "maj7" | "7") {
        text
    } else {
        return None;
    };
    Some(NormalizedChord {
        pitch_class: pitch_class(&root)?,
        quality: quality(&text.to_lowercase())?,
    })
}
pub fn chord_image_path(label: &str) -> Option<String> {
    let base = label.split('/').next()?.trim();
    let (root, text) = root_and_quality(base)?;
    let text = if let Some(text) = text.strip_prefix(':') {
        text.trim()
    } else if matches!(text, "" | "m" | "m7" | "maj7" | "7") {
        text
    } else {
        return None;
    };
    let text = text.to_lowercase();
    if text == "dom7" {
        return None;
    }
    let q = quality(&text)?;
    let file = match root {
        "A" => "a",
        "Ab" | "G#" => "ab",
        "B" => "b",
        "Bb" | "A#" => "bb",
        "C" => "c",
        "C#" | "Db" => "csharp",
        "D" => "d",
        "E" => "e",
        "Eb" | "D#" => "eb",
        "F" => "f",
        "F#" | "Gb" => "fsharp",
        "G" => "g",
        _ => return None,
    };
    Some(format!("assets/chords/{file}{}.png", suffix(q)))
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CapoRecommendation {
    pub capo: i32,
    pub easy_count: usize,
    pub total_chords: usize,
}
pub fn recommend_capo(chords: &[Chord]) -> CapoRecommendation {
    const EASY: [&str; 22] = [
        "C", "D", "E", "G", "A", "Am", "Dm", "Em", "A7", "B7", "C7", "D7", "E7", "G7", "Am7",
        "Dm7", "Em7", "Amaj7", "Cmaj7", "Dmaj7", "Emaj7", "Gmaj7",
    ];
    let mut progression: Vec<&str> = vec![];
    for c in chords {
        if !matches!(c.chord.as_str(), "" | "N" | "NC" | "N.C.")
            && progression.last().copied() != Some(c.chord.as_str())
        {
            progression.push(&c.chord);
        }
    }
    let mut result = CapoRecommendation {
        total_chords: progression.len(),
        ..Default::default()
    };
    for capo in 0..=7 {
        let easy_count = progression
            .iter()
            .filter(|c| {
                let transposed = chord_for_capo(c, capo);
                let base = transposed.split('/').next().unwrap_or_default().trim();
                let Some((root, q)) = root_and_quality(base) else {
                    return false;
                };
                let q = q.trim().to_lowercase();
                let q = q.strip_prefix(':').unwrap_or(&q);
                if q == "dom7" {
                    return false;
                }
                quality(q).is_some_and(|q| EASY.contains(&format!("{root}{}", suffix(q)).as_str()))
            })
            .count();
        if capo == 0 || easy_count > result.easy_count {
            result.capo = capo;
            result.easy_count = easy_count;
        }
    }
    result
}

pub fn build_chord_timeline(chords: &[Chord], duration: f64) -> Vec<Chord> {
    let mut ordered: Vec<_> = chords
        .iter()
        .filter(|c| c.start.is_finite() && c.end.is_finite() && c.end > c.start)
        .cloned()
        .collect();
    ordered.sort_by(|a, b| a.start.total_cmp(&b.start));
    let mut timeline: Vec<Chord> = vec![];
    let mut cursor = 0.0;
    for mut chord in ordered {
        let mut start = chord.start.max(0.0);
        let end = chord.end.max(0.0);
        if end <= cursor {
            continue;
        }
        let gap = start - cursor;
        if gap <= 0.08 && timeline.is_empty() {
            start = 0.0;
        } else if gap > 0.08 {
            timeline.push(Chord {
                start: cursor,
                end: start,
                chord: "N".into(),
                confidence: 1.0,
            });
            cursor = start;
        } else if gap > 0.0 {
            if let Some(last) = timeline.last_mut() {
                last.end = start;
            }
            cursor = start;
        }
        chord.start = start.max(cursor);
        chord.end = end;
        timeline.push(chord);
        cursor = end;
    }
    if duration.is_finite() && duration - cursor > 0.08 {
        timeline.push(Chord {
            start: cursor,
            end: duration,
            chord: "N".into(),
            confidence: 1.0,
        });
    }
    timeline
}
#[derive(Debug)]
pub struct Progression<'a> {
    pub index: Option<usize>,
    pub current: Option<&'a Chord>,
    pub next: Option<&'a Chord>,
    pub then: Option<&'a Chord>,
    pub progress: f64,
    pub remaining: f64,
}
pub fn chord_progression_at(timeline: &[Chord], position: f64) -> Progression<'_> {
    let time = if position.is_finite() {
        position.max(0.0)
    } else {
        0.0
    };
    let index = timeline
        .iter()
        .position(|c| time >= c.start && time < c.end);
    if let Some(i) = index {
        let current = &timeline[i];
        Progression {
            index,
            current: Some(current),
            next: timeline.get(i + 1),
            then: timeline.get(i + 2),
            progress: ((time - current.start) / (current.end - current.start)).clamp(0.0, 1.0),
            remaining: (current.end - time).max(0.0),
        }
    } else {
        let next = timeline.iter().position(|c| c.start > time);
        Progression {
            index: None,
            current: None,
            next: next.and_then(|i| timeline.get(i)),
            then: next.and_then(|i| timeline.get(i + 1)),
            progress: 0.0,
            remaining: 0.0,
        }
    }
}
pub fn expected_segment_at(segments: &[Chord], seconds: f64) -> Option<usize> {
    if !seconds.is_finite() {
        return None;
    }
    let mut low = 0;
    let mut high = segments.len();
    while low < high {
        let mid = (low + high) / 2;
        let segment = &segments[mid];
        if seconds < segment.start {
            high = mid;
        } else if seconds >= segment.end {
            low = mid + 1;
        } else {
            return Some(mid);
        }
    }
    None
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Rating {
    Green,
    Yellow,
    Red,
}
pub fn classify_chord_rating(expected: &str, played: &str) -> Option<Rating> {
    let expected = normalize_chord(expected)?;
    let played = normalize_chord(played)?;
    if !matches!(played.quality, Quality::Major | Quality::Minor) {
        return None;
    }
    Some(if expected.pitch_class != played.pitch_class {
        Rating::Red
    } else if expected.quality == played.quality {
        Rating::Green
    } else {
        Rating::Yellow
    })
}
pub fn align_sample_time(playback: f64, age_ms: f64, max_age_ms: f64, speed: f64) -> Option<f64> {
    if !playback.is_finite()
        || !age_ms.is_finite()
        || age_ms < 0.0
        || age_ms > max_age_ms
        || !speed.is_finite()
        || speed <= 0.0
    {
        return None;
    }
    Some((playback - age_ms * speed / 1000.0).max(0.0))
}
#[derive(Clone, Debug, Default, PartialEq)]
pub struct RatingResult {
    pub rating: Option<Rating>,
    pub segment_index: Option<usize>,
    pub expected_chord: Option<String>,
    pub sample_playback_time: Option<f64>,
}
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ScoreSummary {
    pub points: f64,
    pub total: usize,
    pub percent: u32,
}
#[derive(Debug)]
pub struct FeedbackScore {
    segments: Vec<Chord>,
    segment_ratings: BTreeMap<usize, Option<Rating>>,
}
impl FeedbackScore {
    pub fn new(segments: Vec<Chord>) -> Self {
        Self {
            segments,
            segment_ratings: BTreeMap::new(),
        }
    }
    pub fn reset(&mut self) {
        self.segment_ratings.clear();
    }
    pub fn record(&mut self, result: &RatingResult) {
        let Some(index) = result.segment_index else {
            return;
        };
        if !self
            .segments
            .get(index)
            .is_some_and(|c| normalize_chord(&c.chord).is_some())
        {
            return;
        }
        if self.segment_ratings.get(&index) == Some(&Some(Rating::Green)) {
            return;
        }
        if result.rating.is_some() || !self.segment_ratings.contains_key(&index) {
            self.segment_ratings.insert(index, result.rating);
        }
    }
    pub fn summary(&self) -> Option<ScoreSummary> {
        let total = self.segment_ratings.len();
        if total == 0 {
            return None;
        }
        let points: f64 = self
            .segment_ratings
            .values()
            .map(|r| match r {
                Some(Rating::Green) => 1.0,
                Some(Rating::Red) => 0.0,
                _ => 0.5,
            })
            .sum();
        Some(ScoreSummary {
            points,
            total,
            percent: (points / total as f64 * 100.0).round() as u32,
        })
    }
}
#[derive(Clone, Debug)]
pub struct FeedbackEvent {
    pub session_id: String,
    pub sequence: i64,
    pub playback_time_seconds: f64,
    pub playback_speed: f64,
    pub sample_age_ms: f64,
    pub chord: String,
    pub input_quality: String,
    pub confidence: f64,
    pub model: String,
    pub quality_uncertain: bool,
}
impl Default for FeedbackEvent {
    fn default() -> Self {
        Self {
            session_id: String::new(),
            sequence: 0,
            playback_time_seconds: 0.0,
            playback_speed: 1.0,
            sample_age_ms: 0.0,
            chord: String::new(),
            input_quality: "ok".into(),
            confidence: 0.0,
            model: String::new(),
            quality_uncertain: false,
        }
    }
}
#[derive(Clone, Debug)]
pub struct RaterOptions {
    pub confidence: f64,
    pub red_confidence: f64,
    pub chord_grace_ms: f64,
    pub template_chord_grace_ms: f64,
    pub positive_dwell_ms: f64,
    pub red_dwell_ms: f64,
    pub experimental_dwell_ms: f64,
    pub min_display_ms: f64,
    pub dropout_hold_ms: f64,
    pub max_age_ms: f64,
}
impl Default for RaterOptions {
    fn default() -> Self {
        Self {
            confidence: 0.60,
            red_confidence: 0.70,
            chord_grace_ms: 250.0,
            template_chord_grace_ms: 120.0,
            positive_dwell_ms: 200.0,
            red_dwell_ms: 350.0,
            experimental_dwell_ms: 280.0,
            min_display_ms: 300.0,
            dropout_hold_ms: 240.0,
            max_age_ms: 750.0,
        }
    }
}
#[derive(Debug)]
pub struct FeedbackRater {
    segments: Vec<Chord>,
    pub options: RaterOptions,
    session_id: Option<String>,
    last_sequence: i64,
    playback_speed: f64,
    last_playback_time: Option<f64>,
    elapsed_wall_ms: f64,
    segment_index: Option<usize>,
    rating: Option<Rating>,
    candidate: Option<Rating>,
    candidate_since: Option<f64>,
    candidate_paused_at: Option<f64>,
    last_rating_change_at: Option<f64>,
    last_reliable_at: Option<f64>,
    last_rating_support_at: Option<f64>,
}
impl FeedbackRater {
    pub fn new(segments: Vec<Chord>) -> Self {
        Self {
            segments,
            options: RaterOptions::default(),
            session_id: None,
            last_sequence: -1,
            playback_speed: 1.0,
            last_playback_time: None,
            elapsed_wall_ms: 0.0,
            segment_index: None,
            rating: None,
            candidate: None,
            candidate_since: None,
            candidate_paused_at: None,
            last_rating_change_at: None,
            last_reliable_at: None,
            last_rating_support_at: None,
        }
    }
    pub fn reset(&mut self, session_id: Option<String>) {
        self.session_id = session_id;
        self.last_sequence = -1;
        self.playback_speed = 1.0;
        self.last_playback_time = None;
        self.elapsed_wall_ms = 0.0;
        self.segment_index = None;
        self.clear_segment();
    }
    fn clear_candidate(&mut self) {
        self.candidate = None;
        self.candidate_since = None;
        self.candidate_paused_at = None;
    }
    fn clear_segment(&mut self) {
        self.rating = None;
        self.clear_candidate();
        self.last_rating_change_at = None;
        self.last_reliable_at = None;
        self.last_rating_support_at = None;
    }
    fn result(&self, sample_time: Option<f64>) -> RatingResult {
        RatingResult {
            rating: self.rating,
            segment_index: self.segment_index,
            expected_chord: self.segment_index.map(|i| self.segments[i].chord.clone()),
            sample_playback_time: sample_time,
        }
    }
    pub fn advance(&mut self, time: f64, speed: f64) -> RatingResult {
        let speed = if speed.is_finite() && speed > 0.0 {
            speed
        } else {
            1.0
        };
        if time.is_finite() {
            if let Some(previous) = self.last_playback_time {
                self.elapsed_wall_ms += (time - previous).max(0.0) * 1000.0 / speed;
            }
            self.last_playback_time = Some(time);
        }
        self.playback_speed = speed;
        let current = expected_segment_at(&self.segments, time)
            .filter(|i| normalize_chord(&self.segments[*i].chord).is_some());
        if current.is_none() {
            self.segment_index = None;
            self.rating = None;
            self.clear_candidate();
            self.last_rating_support_at = None;
        } else if current != self.segment_index {
            self.segment_index = current;
            self.clear_segment();
        } else {
            if self
                .last_reliable_at
                .is_some_and(|t| self.elapsed_wall_ms - t > self.options.dropout_hold_ms)
            {
                self.clear_candidate();
            }
            if self.rating != Some(Rating::Green)
                && self
                    .last_rating_support_at
                    .is_some_and(|t| self.elapsed_wall_ms - t > self.options.dropout_hold_ms)
            {
                self.rating = None;
                self.last_rating_change_at = None;
                self.last_rating_support_at = None;
            }
        }
        self.result(Some(time))
    }
    pub fn observe(&mut self, event: &FeedbackEvent) -> RatingResult {
        if self.session_id.as_deref() != Some(event.session_id.as_str())
            || event.sequence <= self.last_sequence
        {
            return self.result(None);
        }
        if self.last_sequence >= 0 && event.sequence > self.last_sequence + 1 {
            self.clear_candidate();
        }
        self.last_sequence = event.sequence;
        let speed = event.playback_speed;
        let visible = self.advance(event.playback_time_seconds, speed);
        if self.rating == Some(Rating::Green) {
            return visible;
        }
        let Some(sample_time) = align_sample_time(
            event.playback_time_seconds,
            event.sample_age_ms,
            self.options.max_age_ms,
            speed,
        ) else {
            return visible;
        };
        let current = expected_segment_at(&self.segments, sample_time);
        if current != self.segment_index {
            return visible;
        }
        let Some(index) = current.filter(|i| normalize_chord(&self.segments[*i].chord).is_some())
        else {
            self.segment_index = None;
            self.rating = None;
            self.clear_candidate();
            self.last_rating_support_at = None;
            return self.result(Some(sample_time));
        };
        let sample_wall_ms = self.elapsed_wall_ms - event.sample_age_ms;
        let experimental = event.model == "chordsense-chroma-template-experimental";
        let grace = if experimental {
            self.options.template_chord_grace_ms
        } else {
            self.options.chord_grace_ms
        };
        if (sample_time - self.segments[index].start) * 1000.0 < grace * speed {
            return self.result(Some(sample_time));
        }
        let mut proposed = classify_chord_rating(&self.segments[index].chord, &event.chord);
        if experimental && event.quality_uncertain && proposed == Some(Rating::Green) {
            proposed = Some(Rating::Yellow);
        }
        let needed = if proposed == Some(Rating::Red) {
            self.options.red_confidence
        } else {
            self.options.confidence
        };
        if event.input_quality != "ok" || !event.confidence.is_finite() || event.confidence < needed
        {
            proposed = None;
        }
        if proposed.is_none() {
            if event.input_quality != "ok"
                && self.candidate.is_some()
                && self
                    .last_reliable_at
                    .is_some_and(|t| self.elapsed_wall_ms - t <= self.options.dropout_hold_ms)
            {
                if self.candidate_paused_at.is_none() {
                    self.candidate_paused_at = self.last_reliable_at;
                }
            } else {
                self.clear_candidate();
            }
            if self
                .last_rating_support_at
                .is_none_or(|t| self.elapsed_wall_ms - t > self.options.dropout_hold_ms)
            {
                self.rating = None;
                self.last_rating_change_at = None;
                self.last_rating_support_at = None;
            }
            return self.result(Some(sample_time));
        }
        if let Some(paused_at) = self.candidate_paused_at {
            if proposed == self.candidate {
                if let Some(since) = self.candidate_since.as_mut() {
                    *since += (sample_wall_ms - paused_at).max(0.0);
                }
            } else {
                self.candidate = None;
                self.candidate_since = None;
            }
            self.candidate_paused_at = None;
        }
        self.last_reliable_at = Some(self.elapsed_wall_ms);
        if proposed == self.rating {
            self.last_rating_support_at = Some(self.elapsed_wall_ms);
            self.clear_candidate();
            return self.result(Some(sample_time));
        }
        if proposed != self.candidate {
            self.candidate = proposed;
            self.candidate_since = Some(sample_wall_ms);
            self.candidate_paused_at = None;
            return self.result(Some(sample_time));
        }
        let dwell = if proposed == Some(Rating::Red) {
            self.options.red_dwell_ms
        } else if experimental {
            self.options.experimental_dwell_ms
        } else {
            self.options.positive_dwell_ms
        };
        let held = self
            .last_rating_change_at
            .is_none_or(|t| sample_wall_ms - t >= self.options.min_display_ms);
        if self
            .candidate_since
            .is_some_and(|t| sample_wall_ms - t >= dwell)
            && held
        {
            self.rating = proposed;
            self.last_rating_change_at = Some(sample_wall_ms);
            self.last_rating_support_at = Some(self.elapsed_wall_ms);
            self.clear_candidate();
        }
        self.result(Some(sample_time))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn c(start: f64, end: f64, name: &str) -> Chord {
        Chord {
            start,
            end,
            chord: name.into(),
            confidence: 1.0,
        }
    }
    fn rater(chords: Vec<Chord>) -> FeedbackRater {
        let mut r = FeedbackRater::new(chords);
        r.reset(Some("s".into()));
        r
    }
    fn event(sequence: i64, time: f64, chord: &str) -> FeedbackEvent {
        FeedbackEvent {
            session_id: "s".into(),
            sequence,
            playback_time_seconds: time,
            chord: chord.into(),
            confidence: 0.95,
            ..Default::default()
        }
    }
    fn observe(
        r: &mut FeedbackRater,
        seq: &mut i64,
        t: f64,
        chord: &str,
        speed: f64,
        template: bool,
        quality: &str,
    ) -> Option<Rating> {
        *seq += 1;
        let mut e = event(*seq, t, chord);
        e.playback_speed = speed;
        e.input_quality = quality.into();
        if template {
            e.model = "chordsense-chroma-template-experimental".into();
        }
        r.observe(&e).rating
    }
    #[test]
    fn exact_boundaries_preview_two_and_progress() {
        let timeline = build_chord_timeline(
            &[c(0., 4., "C:maj"), c(4., 7., "G:maj"), c(7., 9., "A:min")],
            9.,
        );
        let p = chord_progression_at(&timeline, 3.);
        assert_eq!(p.current.unwrap().chord, "C:maj");
        assert_eq!(p.next.unwrap().chord, "G:maj");
        assert_eq!(p.then.unwrap().chord, "A:min");
        assert_eq!(p.progress, 0.75);
        assert_eq!(p.remaining, 1.);
        let p = chord_progression_at(&timeline, 4.);
        assert_eq!(p.current.unwrap().chord, "G:maj");
        assert_eq!(p.progress, 0.);
    }
    #[test]
    fn gaps_rests_seams_and_invalid_segments() {
        let timeline =
            build_chord_timeline(&[c(0.04, 2., "C"), c(3., 5., "G"), c(2., 2., "bad")], 6.);
        assert_eq!(
            timeline
                .iter()
                .map(|c| c.chord.as_str())
                .collect::<Vec<_>>(),
            vec!["C", "N", "G", "N"]
        );
        assert_eq!(timeline[0].start, 0.);
        assert_eq!(
            chord_progression_at(&timeline, 2.5).current.unwrap().chord,
            "N"
        );
        assert_eq!(chord_progression_at(&timeline, f64::NAN).progress, 0.);
        assert!(chord_progression_at(&timeline, 6.).current.is_none());
        let seam = build_chord_timeline(&[c(0., 1., "C"), c(1.02, 2., "G")], 2.);
        assert_eq!(seam[0].end, 1.02);
    }
    #[test]
    fn transpose_slash_chords_and_recommend_lowest_tie() {
        assert_eq!(chord_for_capo("B", 4), "G");
        assert_eq!(chord_for_capo("G#:min", 4), "E:min");
        assert_eq!(chord_for_capo("C/G", 2), "Bb/F");
        assert_eq!(chord_for_capo("C:maj/3", 2), "Bb:maj");
        assert_eq!(chord_for_capo("N", 4), "N");
        let r = recommend_capo(&[
            c(0., 1., "G"),
            c(1., 2., "G"),
            c(2., 3., "C"),
            c(3., 4., "D"),
        ]);
        assert_eq!(
            r,
            CapoRecommendation {
                capo: 0,
                easy_count: 3,
                total_chords: 3
            }
        );
    }
    #[test]
    fn exact_diagrams_only() {
        assert_eq!(
            chord_image_path("Db:min7/Ab").as_deref(),
            Some("assets/chords/csharpm7.png")
        );
        assert_eq!(
            chord_image_path("G#maj7").as_deref(),
            Some("assets/chords/abmaj7.png")
        );
        assert!(chord_image_path("C:sus4").is_none());
        assert!(chord_image_path("N").is_none());
    }
    #[test]
    fn normalization_and_ratings() {
        assert_eq!(
            normalize_chord("Bb:maj/3"),
            Some(NormalizedChord {
                pitch_class: 10,
                quality: Quality::Major
            })
        );
        assert_eq!(normalize_chord("A#:maj"), normalize_chord("Bb"));
        assert_eq!(normalize_chord("D:min7"), normalize_chord("Dm7"));
        for invalid in ["C:sus4", "N", "Noise", "Cmajor", "Cm9"] {
            assert!(normalize_chord(invalid).is_none());
        }
        assert_eq!(classify_chord_rating("Bb:maj", "A#"), Some(Rating::Green));
        assert_eq!(classify_chord_rating("D:7", "D"), Some(Rating::Yellow));
        assert_eq!(classify_chord_rating("C:maj", "Cm"), Some(Rating::Yellow));
        assert_eq!(classify_chord_rating("D:min", "G"), Some(Rating::Red));
        assert!(classify_chord_rating("C", "C7").is_none());
    }
    #[test]
    fn score_visited_segments_once_and_latch_green() {
        let mut score = FeedbackScore::new(vec![
            c(0., 1., "C"),
            c(1., 2., "D"),
            c(2., 3., "Em"),
            c(3., 4., "N"),
        ]);
        assert_eq!(score.summary(), None);
        for (i, rating) in [
            (0, None),
            (0, Some(Rating::Green)),
            (0, Some(Rating::Red)),
            (1, Some(Rating::Red)),
            (2, Some(Rating::Yellow)),
            (3, Some(Rating::Green)),
        ] {
            score.record(&RatingResult {
                segment_index: Some(i),
                rating,
                ..Default::default()
            });
        }
        assert_eq!(
            score.summary(),
            Some(ScoreSummary {
                points: 1.5,
                total: 3,
                percent: 50
            })
        );
        score.reset();
        score.record(&RatingResult {
            segment_index: Some(1),
            ..Default::default()
        });
        assert_eq!(
            score.summary(),
            Some(ScoreSummary {
                points: 0.5,
                total: 1,
                percent: 50
            })
        );
    }
    #[test]
    fn sample_alignment_rejects_stale_invalid_and_tracks_speed() {
        assert_eq!(align_sample_time(5., 200., 750., 1.), Some(4.8));
        assert_eq!(align_sample_time(5., 200., 750., 0.5), Some(4.9));
        assert_eq!(align_sample_time(5., 900., 750., 1.), None);
        assert_eq!(align_sample_time(5., -1., 750., 1.), None);
        assert_eq!(align_sample_time(5., 200., 750., 0.), None);
        let mut r = rater(vec![c(0., 0.9, "C"), c(0.9, 2., "D")]);
        r.options.chord_grace_ms = 0.;
        let mut e = event(1, 1.1, "D");
        e.playback_speed = 0.5;
        e.sample_age_ms = 300.;
        assert!((r.observe(&e).sample_playback_time.unwrap() - 0.95).abs() < 1e-9);
        e.sequence = 2;
        e.playback_time_seconds = 1.21;
        assert_eq!(r.observe(&e).rating, Some(Rating::Green));
    }
    #[test]
    fn slowed_dwell_dropout_and_speed_change_use_wall_time() {
        let mut r = rater(vec![c(0., 3., "C")]);
        let mut seq = 0;
        for t in [0.12, 0.13, 0.18, 0.23, 0.28] {
            assert_eq!(observe(&mut r, &mut seq, t, "G", 0.5, false, "ok"), None);
        }
        assert_eq!(
            observe(&mut r, &mut seq, 0.33, "G", 0.5, false, "ok"),
            Some(Rating::Red)
        );
        assert_eq!(r.advance(0.44, 0.5).rating, Some(Rating::Red));
        assert_eq!(r.advance(0.47, 0.5).rating, None);
        r.reset(Some("s".into()));
        seq = 0;
        for (t, speed) in [(0.30, 1.), (0.40, 1.), (0.425, 0.5)] {
            assert_eq!(observe(&mut r, &mut seq, t, "C", speed, false, "ok"), None);
        }
        assert_eq!(
            observe(&mut r, &mut seq, 0.45, "C", 0.5, false, "ok"),
            Some(Rating::Green)
        );
    }
    #[test]
    fn green_latches_until_boundary_and_ignores_old_session() {
        let mut r = rater(vec![c(0., 3., "C"), c(3., 6., "D:min")]);
        let mut seq = 0;
        for t in [0.10, 0.30] {
            assert_eq!(observe(&mut r, &mut seq, t, "C", 1., false, "ok"), None);
        }
        assert_eq!(
            observe(&mut r, &mut seq, 0.52, "C", 1., false, "ok"),
            Some(Rating::Green)
        );
        for t in [0.6, 1., 1.4] {
            assert_eq!(
                observe(&mut r, &mut seq, t, "G", 1., false, "ok"),
                Some(Rating::Green)
            );
        }
        assert_eq!(r.advance(2.99, 1.).rating, Some(Rating::Green));
        assert_eq!(r.advance(3.05, 1.).rating, None);
        assert_eq!(observe(&mut r, &mut seq, 3.3, "Dm", 1., false, "ok"), None);
        assert_eq!(
            observe(&mut r, &mut seq, 3.51, "Dm", 1., false, "ok"),
            Some(Rating::Green)
        );
        assert_eq!(
            observe(&mut r, &mut seq, 3.85, "", 1., false, "silence"),
            Some(Rating::Green)
        );
        let mut old = event(100, 4., "G");
        old.session_id = "old".into();
        assert_eq!(r.observe(&old).rating, Some(Rating::Green));
    }
    #[test]
    fn repeated_segments_reearn_green_and_seek_reset_clears() {
        let mut r = rater(vec![c(0., 2., "C"), c(2., 4., "C")]);
        assert_eq!(r.observe(&event(1, 0.3, "C")).rating, None);
        assert_eq!(r.observe(&event(2, 0.52, "C")).rating, Some(Rating::Green));
        assert_eq!(r.advance(2.01, 1.).rating, None);
        let mut stale = event(3, 2.2, "C");
        stale.sample_age_ms = 250.;
        assert_eq!(r.observe(&stale).rating, None);
        assert_eq!(r.observe(&event(4, 2.3, "C")).rating, None);
        assert_eq!(r.observe(&event(5, 2.52, "C")).rating, Some(Rating::Green));
        r.reset(Some("s".into()));
        assert_eq!(r.advance(2.7, 1.).rating, None);
        assert_eq!(r.observe(&event(1, 2.71, "C")).rating, None);
        assert_eq!(r.observe(&event(2, 2.93, "C")).rating, Some(Rating::Green));
    }
    #[test]
    fn template_has_shorter_grace_and_longer_dwell() {
        let mut r = rater(vec![c(0., 3., "C")]);
        let mut seq = 0;
        for t in [0.11, 0.13, 0.30, 0.40] {
            assert_eq!(observe(&mut r, &mut seq, t, "C", 1., true, "ok"), None);
        }
        assert_eq!(
            observe(&mut r, &mut seq, 0.42, "C", 1., true, "ok"),
            Some(Rating::Green)
        );
    }
    #[test]
    fn template_short_noise_pauses_dwell_long_noise_resets() {
        let mut r = rater(vec![c(0., 3., "C")]);
        let mut seq = 0;
        for (t, q) in [
            (0.13, "ok"),
            (0.22, "ok"),
            (0.25, "silence"),
            (0.29, "clipping"),
            (0.31, "ok"),
            (0.48, "ok"),
        ] {
            assert_eq!(observe(&mut r, &mut seq, t, "C", 1., true, q), None);
        }
        assert_eq!(
            observe(&mut r, &mut seq, 0.50, "C", 1., true, "ok"),
            Some(Rating::Green)
        );
        r.reset(Some("s".into()));
        seq = 0;
        for (t, q) in [
            (0.13, "ok"),
            (0.22, "ok"),
            (0.25, "silence"),
            (0.5, "silence"),
            (0.52, "ok"),
            (0.70, "ok"),
        ] {
            assert_eq!(observe(&mut r, &mut seq, t, "C", 1., true, q), None);
        }
        assert_eq!(
            observe(&mut r, &mut seq, 0.81, "C", 1., true, "ok"),
            Some(Rating::Green)
        );
    }
    #[test]
    fn uncertainty_template_only_and_wrong_chord_conservative() {
        for (model, expected) in [
            ("chordsense-chroma-template-experimental", Rating::Yellow),
            ("chordsense-causal-stft-cnn", Rating::Green),
        ] {
            let mut r = rater(vec![c(0., 3., "C")]);
            let mut e = event(1, 0.3, "C");
            e.model = model.into();
            e.quality_uncertain = true;
            assert_eq!(r.observe(&e).rating, None);
            e.sequence = 2;
            e.playback_time_seconds = 0.52;
            let middle = r.observe(&e).rating;
            if expected == Rating::Yellow {
                assert_eq!(middle, None);
                e.sequence = 3;
                e.playback_time_seconds = 0.59;
                assert_eq!(r.observe(&e).rating, Some(expected));
            } else {
                assert_eq!(middle, Some(expected));
            }
        }
        let mut r = rater(vec![c(0., 4., "C")]);
        let mut seq = 0;
        for t in [0.3, 0.5] {
            assert_eq!(observe(&mut r, &mut seq, t, "G", 1., false, "ok"), None);
        }
        assert_eq!(
            observe(&mut r, &mut seq, 0.66, "G", 1., false, "ok"),
            Some(Rating::Red)
        );
        assert_eq!(
            observe(&mut r, &mut seq, 0.70, "C", 1., false, "ok"),
            Some(Rating::Red)
        );
        assert_eq!(
            observe(&mut r, &mut seq, 0.92, "C", 1., false, "ok"),
            Some(Rating::Green)
        );
    }
    #[test]
    fn contradictory_evidence_and_sequence_gaps_restart_candidate() {
        let mut r = rater(vec![c(0., 3., "C")]);
        let mut seq = 0;
        for (t, chord) in [
            (0.3, "C"),
            (0.48, "C"),
            (0.5, "G"),
            (0.52, "C"),
            (0.7, "C"),
            (0.78, "C"),
        ] {
            assert_eq!(observe(&mut r, &mut seq, t, chord, 1., true, "ok"), None);
        }
        assert_eq!(
            observe(&mut r, &mut seq, 0.81, "C", 1., true, "ok"),
            Some(Rating::Green)
        );
        r.reset(Some("s".into()));
        assert_eq!(r.observe(&event(1, 0.3, "C")).rating, None);
        assert_eq!(r.observe(&event(3, 0.52, "C")).rating, None);
        assert_eq!(r.observe(&event(4, 0.74, "C")).rating, Some(Rating::Green));
    }
    #[test]
    fn red_requires_stronger_confidence_than_positive_feedback() {
        let mut r = rater(vec![c(0., 4., "C")]);
        for (i, time) in [0.3, 0.5, 0.7, 0.9].into_iter().enumerate() {
            let mut e = event(i as i64 + 1, time, "G");
            e.confidence = 0.69;
            assert_eq!(r.observe(&e).rating, None);
        }
        for (i, time) in [1.1, 1.3, 1.46].into_iter().enumerate() {
            let mut e = event(i as i64 + 5, time, "G");
            e.confidence = 0.70;
            let actual = r.observe(&e).rating;
            assert_eq!(actual, if i == 2 { Some(Rating::Red) } else { None });
        }
        r.reset(Some("s".into()));
        let mut e = event(1, 0.3, "C");
        e.confidence = 0.59;
        assert_eq!(r.observe(&e).rating, None);
        e.sequence = 2;
        e.playback_time_seconds = 0.5;
        e.confidence = 0.60;
        assert_eq!(r.observe(&e).rating, None);
        e.sequence = 3;
        e.playback_time_seconds = 0.71;
        assert_eq!(r.observe(&e).rating, Some(Rating::Green));
    }
    #[test]
    fn replayed_events_cannot_advance_evidence_or_move_chart() {
        let mut r = rater(vec![c(0., 2., "C"), c(2., 4., "D")]);
        assert_eq!(r.observe(&event(1, 0.3, "C")).rating, None);
        let replay = r.observe(&event(1, 2.5, "D"));
        assert_eq!(replay.segment_index, Some(0));
        assert_eq!(replay.rating, None);
        assert_eq!(r.observe(&event(2, 0.52, "C")).rating, Some(Rating::Green));
    }
    #[test]
    fn stale_predictions_cannot_confirm_a_candidate() {
        let mut r = rater(vec![c(0., 4., "C")]);
        assert_eq!(r.observe(&event(1, 0.3, "C")).rating, None);
        let mut stale = event(2, 0.52, "C");
        stale.sample_age_ms = 751.;
        assert_eq!(r.observe(&stale).rating, None);
        stale.sequence = 3;
        stale.playback_time_seconds = 0.8;
        assert_eq!(r.observe(&stale).rating, None);
        assert_eq!(r.observe(&event(4, 0.9, "C")).rating, None);
        assert_eq!(r.observe(&event(5, 1.11, "C")).rating, Some(Rating::Green));
    }
}
