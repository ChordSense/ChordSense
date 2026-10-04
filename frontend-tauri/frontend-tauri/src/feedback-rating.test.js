import assert from "node:assert/strict";
import test from "node:test";

import {
    FeedbackRater,
    FeedbackScore,
    alignSampleTime,
    classifyChordRating,
    normalizeChord
} from "./feedback-rating.js";

test("scores each visited chord segment once with half credit for inconclusive", () => {
    const score = new FeedbackScore([
        { start: 0, end: 1, chord: "C" },
        { start: 1, end: 2, chord: "D" },
        { start: 2, end: 3, chord: "Em" },
        { start: 3, end: 4, chord: "N" }
    ]);

    score.record({ segmentIndex: 0, rating: null });
    score.record({ segmentIndex: 0, rating: "green" });
    score.record({ segmentIndex: 0, rating: "red" });
    score.record({ segmentIndex: 1, rating: "red" });
    score.record({ segmentIndex: 2, rating: "yellow" });
    score.record({ segmentIndex: 3, rating: "green" });

    assert.deepEqual(score.summary(), {
        points: 1.5,
        total: 3,
        percent: 50
    });
});

test("does not score chord segments that were never visited", () => {
    const score = new FeedbackScore([
        { start: 0, end: 1, chord: "C" },
        { start: 1, end: 2, chord: "G" }
    ]);

    assert.equal(score.summary(), null);
    score.record({ segmentIndex: 1, rating: null });
    assert.deepEqual(score.summary(), {
        points: 0.5,
        total: 1,
        percent: 50
    });
});

test("normalizes chart and model labels without guessing unsupported chords", () => {
    assert.deepEqual(normalizeChord("Bb:maj/3"), {
        pitchClass: 10, quality: "major"
    });
    assert.deepEqual(normalizeChord("A#:maj"), normalizeChord("Bb"));
    assert.deepEqual(normalizeChord("D:min7"), normalizeChord("Dm7"));
    assert.equal(normalizeChord("C:sus4"), null);
    assert.equal(normalizeChord("N"), null);
});

test("uses green, yellow, red, and abstention deliberately", () => {
    assert.equal(classifyChordRating("Bb:maj", "A#"), "green");
    assert.equal(classifyChordRating("D:7", "D"), "yellow");
    assert.equal(classifyChordRating("C:maj", "Cm"), "yellow");
    assert.equal(classifyChordRating("D:min", "G"), "red");
    assert.equal(classifyChordRating("N", "G"), null);
    assert.equal(classifyChordRating("C:sus4", "C"), null);
});

test("rejects stale or invalid alignment", () => {
    assert.equal(alignSampleTime(5, 200), 4.8);
    assert.equal(alignSampleTime(5, 200, 750, 0.5), 4.9);
    assert.equal(alignSampleTime(5, 900), null);
    assert.equal(alignSampleTime(5, -1), null);
    assert.equal(alignSampleTime(5, 200, 750, 0), null);
});

test("slowed playback aligns delayed predictions to the right chord", () => {
    const rater = new FeedbackRater([
        { start: 0, end: 0.9, chord: "C" },
        { start: 0.9, end: 2, chord: "D" }
    ], { chordGraceMs: 0 });
    rater.reset("slow-alignment");
    const observe = (sequence, time) => rater.observe({
        sessionId: "slow-alignment", sequence,
        playbackTimeSeconds: time, playbackSpeed: 0.5,
        sampleAgeMs: 300, chord: "D", inputQuality: "ok", confidence: 0.95
    });
    assert.ok(Math.abs(observe(1, 1.1).samplePlaybackTime - 0.95) < 1e-9);
    assert.equal(observe(2, 1.21).rating, "green");
});

test("slowed playback keeps feedback dwell and dropout in real milliseconds", () => {
    const rater = new FeedbackRater([{ start: 0, end: 3, chord: "C" }]);
    rater.reset("slow-response");
    let sequence = 0;
    const observe = (time, chord) => rater.observe({
        sessionId: "slow-response", sequence: ++sequence,
        playbackTimeSeconds: time, playbackSpeed: 0.5,
        sampleAgeMs: 0, chord, inputQuality: "ok", confidence: 0.95
    }).rating;

    // The 250 ms boundary grace occupies 0.125 s of the slowed chart.
    assert.equal(observe(0.12, "G"), null);
    for (const time of [0.13, 0.18, 0.23, 0.28]) {
        assert.equal(observe(time, "G"), null);
    }
    assert.equal(observe(0.33, "G"), "red");

    // A correct shape can earn green after its real-time dwell and display hold.
    for (const time of [0.35, 0.40, 0.45]) {
        assert.equal(observe(time, "C"), "red");
    }
    assert.equal(observe(0.48, "C"), "green");

    rater.reset("slow-response");
    sequence = 0;
    for (const time of [0.13, 0.18, 0.23, 0.28, 0.33]) observe(time, "G");
    assert.equal(rater.advance(0.44, 0.5).rating, "red");
    assert.equal(rater.advance(0.47, 0.5).rating, null);
});

test("changing speed mid-chord preserves elapsed feedback dwell", () => {
    const rater = new FeedbackRater([{ start: 0, end: 2, chord: "C" }]);
    rater.reset("speed-change");
    let sequence = 0;
    const observe = (time, speed) => rater.observe({
        sessionId: "speed-change", sequence: ++sequence,
        playbackTimeSeconds: time, playbackSpeed: speed,
        sampleAgeMs: 0, chord: "C", inputQuality: "ok", confidence: 0.95
    }).rating;
    assert.equal(observe(0.30, 1), null);
    assert.equal(observe(0.40, 1), null);
    assert.equal(observe(0.425, 0.5), null);
    assert.equal(observe(0.45, 0.5), "green");
});

test("requires stable evidence and latches green until the chord boundary", () => {
    const rater = new FeedbackRater([
        { start: 0, end: 3, chord: "C" },
        { start: 3, end: 6, chord: "D:min" }
    ]);
    rater.reset("session-1");
    let sequence = 0;
    const observe = (time, chord, inputQuality = "ok") => rater.observe({
        sessionId: "session-1", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        chord, inputQuality, confidence: 0.95
    }).rating;
    assert.equal(observe(0.10, "C"), null);
    assert.equal(observe(0.30, "C"), null);
    assert.equal(observe(0.52, "C"), "green");
    assert.equal(observe(0.60, "G"), "green");
    assert.equal(observe(0.65, "C"), "green");
    assert.equal(observe(1.00, "G"), "green");
    assert.equal(observe(1.20, "G"), "green");
    assert.equal(observe(1.40, "G"), "green");
    assert.equal(observe(3.05, "G"), null);
    assert.equal(observe(3.30, "Dm"), null);
    assert.equal(observe(3.50, "Dm"), "green");
    assert.equal(observe(3.60, null, "silence"), "green");
    assert.equal(observe(3.85, null, "silence"), "green");
    assert.equal(rater.observe({
        sessionId: "old-session", sequence: 100,
        playbackTimeSeconds: 4, sampleAgeMs: 0,
        chord: "G", inputQuality: "ok", confidence: 0.99
    }).rating, "green");
});

test("preserves green without predictions and ignores previous-chord samples", () => {
    const rater = new FeedbackRater([
        { start: 0, end: 2, chord: "C" },
        { start: 2, end: 4, chord: "D" }
    ]);
    rater.reset("s");
    const observe = (sequence, playbackTimeSeconds, sampleAgeMs = 0) => rater.observe({
        sessionId: "s", sequence, playbackTimeSeconds, sampleAgeMs,
        chord: "C", inputQuality: "ok", confidence: 0.95
    }).rating;
    assert.equal(observe(1, 0.30), null);
    assert.equal(observe(2, 0.52), "green");
    assert.equal(rater.advance(0.80).rating, "green");
    assert.equal(observe(3, 2.10, 300), null);
    assert.equal(rater.advance(2.50).rating, null);
});

test("experimental template confirms a matching chord after 280 ms of stable evidence", () => {
    const rater = new FeedbackRater([{ start: 0, end: 3, chord: "C" }]);
    rater.reset("template-session");
    let sequence = 0;
    const observe = time => rater.observe({
        sessionId: "template-session", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        model: "chordsense-chroma-template-experimental",
        chord: "C", confidence: 0.95, inputQuality: "ok",
        qualityUncertain: false
    }).rating;
    assert.equal(observe(0.30), null);
    assert.equal(observe(0.52), null);
    assert.equal(observe(0.57), null);
    assert.equal(observe(0.59), "green");
});

test("template grace admits a stable shape early while still filtering the chord boundary", () => {
    const rater = new FeedbackRater([{ start: 0, end: 2, chord: "C" }]);
    rater.reset("template-session");
    let sequence = 0;
    const observe = time => rater.observe({
        sessionId: "template-session", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        model: "chordsense-chroma-template-experimental",
        chord: "C", confidence: 0.95, inputQuality: "ok"
    }).rating;
    assert.equal(observe(0.11), null);
    assert.equal(observe(0.13), null);
    assert.equal(observe(0.30), null);
    assert.equal(observe(0.40), null);
    assert.equal(observe(0.42), "green");
});

test("short non-ok gaps pause template dwell without discarding a supported shape", () => {
    const rater = new FeedbackRater([{ start: 0, end: 3, chord: "C" }]);
    rater.reset("template-session");
    let sequence = 0;
    const observe = (time, quality = "ok") => rater.observe({
        sessionId: "template-session", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        model: "chordsense-chroma-template-experimental",
        chord: quality === "ok" ? "C" : null,
        confidence: 0.95, inputQuality: quality
    }).rating;
    assert.equal(observe(0.13), null);
    assert.equal(observe(0.22), null);
    assert.equal(observe(0.25, "silence"), null);
    assert.equal(observe(0.29, "clipping"), null);
    assert.equal(observe(0.31), null);
    assert.equal(observe(0.48), null);
    assert.equal(observe(0.50), "green");
});

test("a non-ok gap longer than the hold starts a new template candidate", () => {
    const rater = new FeedbackRater([{ start: 0, end: 3, chord: "C" }]);
    rater.reset("template-session");
    let sequence = 0;
    const observe = (time, quality = "ok") => rater.observe({
        sessionId: "template-session", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        model: "chordsense-chroma-template-experimental",
        chord: quality === "ok" ? "C" : null,
        confidence: 0.95, inputQuality: quality
    }).rating;
    assert.equal(observe(0.13), null);
    assert.equal(observe(0.22), null);
    assert.equal(observe(0.25, "silence"), null);
    assert.equal(observe(0.50, "silence"), null);
    assert.equal(observe(0.52), null);
    assert.equal(observe(0.70), null);
    assert.equal(observe(0.81), "green");
});

test("template quality uncertainty downgrades an exact match to yellow", () => {
    const rater = new FeedbackRater([{ start: 0, end: 3, chord: "C" }]);
    rater.reset("template-session");
    let sequence = 0;
    const observe = (time, chord) => rater.observe({
        sessionId: "template-session", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        model: "chordsense-chroma-template-experimental",
        chord, confidence: 0.95, inputQuality: "ok",
        qualityUncertain: true
    }).rating;
    assert.equal(observe(0.30, "C"), null);
    assert.equal(observe(0.52, "C"), null);
    assert.equal(observe(0.57, "C"), null);
    assert.equal(observe(0.59, "C"), "yellow");

    rater.reset("template-session");
    sequence = 0;
    assert.equal(observe(0.30, "G"), null);
    assert.equal(observe(0.52, "G"), null);
    assert.equal(observe(0.59, "G"), null);
    assert.equal(observe(0.66, "G"), "red");
});

test("brief contradictory template evidence resets the faster candidate", () => {
    const rater = new FeedbackRater([{ start: 0, end: 3, chord: "C" }]);
    rater.reset("template-session");
    let sequence = 0;
    const observe = (time, chord) => rater.observe({
        sessionId: "template-session", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        model: "chordsense-chroma-template-experimental",
        chord, confidence: 0.95, inputQuality: "ok",
        qualityUncertain: false
    }).rating;
    assert.equal(observe(0.30, "C"), null);
    assert.equal(observe(0.48, "C"), null);
    assert.equal(observe(0.50, "G"), null);
    assert.equal(observe(0.52, "C"), null);
    assert.equal(observe(0.70, "C"), null);
    assert.equal(observe(0.78, "C"), null);
    assert.equal(observe(0.81, "C"), "green");
});

test("CNN keeps its existing dwell and ignores template-only uncertainty", () => {
    const rater = new FeedbackRater([{ start: 0, end: 3, chord: "C" }]);
    rater.reset("cnn-session");
    let sequence = 0;
    const observe = time => rater.observe({
        sessionId: "cnn-session", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        model: "chordsense-causal-stft-cnn",
        chord: "C", confidence: 0.95, inputQuality: "ok",
        qualityUncertain: true
    }).rating;
    assert.equal(observe(0.30), null);
    assert.equal(observe(0.52), "green");
});

test("late wrong template predictions cannot dislodge green within a segment", () => {
    const rater = new FeedbackRater([{ start: 0, end: 3, chord: "C" }]);
    rater.reset("template-session");
    let sequence = 0;
    const observe = (time, chord) => rater.observe({
        sessionId: "template-session", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        model: "chordsense-chroma-template-experimental",
        chord, confidence: 0.95, inputQuality: "ok",
        qualityUncertain: false
    }).rating;
    for (const time of [0.30, 0.40, 0.50]) assert.equal(observe(time, "C"), null);
    assert.equal(observe(0.60, "C"), "green");
    assert.equal(observe(0.90, "G"), "green");
    assert.equal(observe(1.05, "G"), "green");
    assert.equal(observe(1.10, "G"), "green");
    assert.equal(observe(1.30, "G"), "green");
    assert.equal(observe(1.42, "G"), "green");
    assert.equal(rater.advance(2.99).rating, "green");
    assert.equal(rater.advance(3).rating, null);
});

test("intermittent contradictory CNN evidence cannot dislodge green", () => {
    const rater = new FeedbackRater([{ start: 0, end: 4, chord: "E" }]);
    rater.reset("cnn-session");
    let sequence = 0;
    const observe = (time, chord, confidence) => rater.observe({
        sessionId: "cnn-session", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        model: "chordsense-causal-stft-cnn",
        chord, confidence, inputQuality: "ok"
    }).rating;
    assert.equal(observe(0.30, "E", 0.95), null);
    assert.equal(observe(0.52, "E", 0.95), "green");
    for (let index = 0; index < 40; index += 1) {
        const rating = observe(0.57 + index * 0.05, "F#m",
            index % 5 === 4 ? 0.65 : 0.75);
        assert.equal(rating, "green");
    }
});

test("long silence and missing predictions preserve green until the segment ends", () => {
    const rater = new FeedbackRater([{ start: 0, end: 5, chord: "C" }]);
    rater.reset("s");
    const observe = (sequence, time, chord, inputQuality = "ok") => rater.observe({
        sessionId: "s", sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        chord, inputQuality, confidence: 0.95
    }).rating;
    assert.equal(observe(1, 0.30, "C"), null);
    assert.equal(observe(2, 0.52, "C"), "green");
    assert.equal(observe(3, 1.20, null, "silence"), "green");
    assert.equal(rater.advance(4.95).rating, "green");
    assert.equal(rater.advance(5).rating, null);
});

test("a wrong chord can show red before a correct chord latches green", () => {
    const rater = new FeedbackRater([{ start: 0, end: 4, chord: "C" }]);
    rater.reset("s");
    let sequence = 0;
    const observe = (time, chord) => rater.observe({
        sessionId: "s", sequence: ++sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        chord, inputQuality: "ok", confidence: 0.95
    }).rating;
    assert.equal(observe(0.30, "G"), null);
    assert.equal(observe(0.50, "G"), null);
    assert.equal(observe(0.66, "G"), "red");
    assert.equal(observe(0.70, "C"), "red");
    assert.equal(observe(0.92, "C"), "green");
    assert.equal(observe(0.97, "C"), "green");
    assert.equal(observe(1.50, "G"), "green");
});

test("a repeated chord in the next segment must earn green again", () => {
    const rater = new FeedbackRater([
        { start: 0, end: 2, chord: "C" },
        { start: 2, end: 4, chord: "C" }
    ]);
    rater.reset("s");
    const observe = (sequence, playbackTimeSeconds, sampleAgeMs = 0) => rater.observe({
        sessionId: "s", sequence, playbackTimeSeconds, sampleAgeMs,
        chord: "C", inputQuality: "ok", confidence: 0.95
    }).rating;
    assert.equal(observe(1, 0.30), null);
    assert.equal(observe(2, 0.52), "green");
    assert.equal(rater.advance(1.99).rating, "green");
    assert.equal(rater.advance(2.01).rating, null);
    assert.equal(observe(3, 2.20, 250), null);
    assert.equal(observe(4, 2.30), null);
    assert.equal(observe(5, 2.52), "green");
});

test("reset for a seek clears green even within the same chord segment", () => {
    const rater = new FeedbackRater([{ start: 0, end: 4, chord: "C" }]);
    rater.reset("s");
    const observe = (sequence, time) => rater.observe({
        sessionId: "s", sequence,
        playbackTimeSeconds: time, sampleAgeMs: 0,
        chord: "C", inputQuality: "ok", confidence: 0.95
    }).rating;
    assert.equal(observe(1, 0.30), null);
    assert.equal(observe(2, 0.52), "green");
    rater.reset("s");
    assert.equal(rater.advance(1.70).rating, null);
    assert.equal(observe(1, 1.71), null);
    assert.equal(observe(2, 1.93), "green");
});
