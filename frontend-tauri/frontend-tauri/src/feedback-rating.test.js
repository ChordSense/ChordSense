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

test("requires stable evidence and resets at chord boundaries", () => {
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
    assert.equal(observe(1.00, "G"), null);
    assert.equal(observe(1.20, "G"), null);
    assert.equal(observe(1.40, "G"), "red");
    assert.equal(observe(3.05, "G"), null);
    assert.equal(observe(3.30, "Dm"), null);
    assert.equal(observe(3.50, "Dm"), "green");
    assert.equal(observe(3.60, null, "silence"), "green");
    assert.equal(observe(3.85, null, "silence"), null);
    assert.equal(rater.observe({
        sessionId: "old-session", sequence: 100,
        playbackTimeSeconds: 4, sampleAgeMs: 0,
        chord: "G", inputQuality: "ok", confidence: 0.99
    }).rating, null);
});

test("clears a stale rating without new predictions and ignores previous-chord samples", () => {
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
    assert.equal(rater.advance(0.80).rating, null);
    assert.equal(observe(3, 2.10, 300), null);
    assert.equal(rater.advance(2.50).segmentIndex, 1);
});
