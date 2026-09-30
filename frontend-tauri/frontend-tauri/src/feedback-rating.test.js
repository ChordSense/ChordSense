import assert from "node:assert/strict";
import test from "node:test";

import {
    FeedbackRater,
    alignSampleTime,
    classifyChordRating,
    normalizeChord
} from "./feedback-rating.js";

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
    assert.equal(alignSampleTime(5, 900), null);
    assert.equal(alignSampleTime(5, -1), null);
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

test("experimental template waits 500 ms before showing a matching chord", () => {
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
    assert.equal(observe(0.72), null);
    assert.equal(observe(0.83), "green");
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
    assert.equal(observe(0.72, "C"), null);
    assert.equal(observe(0.83, "C"), "yellow");

    rater.reset("template-session");
    sequence = 0;
    assert.equal(observe(0.30, "G"), null);
    assert.equal(observe(0.52, "G"), null);
    assert.equal(observe(0.72, "G"), null);
    assert.equal(observe(0.83, "G"), "red");
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
    for (const time of [0.30, 0.50, 0.70]) assert.equal(observe(time, "C"), null);
    assert.equal(observe(0.83, "C"), "green");
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
