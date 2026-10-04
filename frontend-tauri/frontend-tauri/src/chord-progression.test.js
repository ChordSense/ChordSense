import test from "node:test";
import assert from "node:assert/strict";
import { buildChordTimeline, chordProgressionAt } from "./chord-progression.js";

test("the active chord advances exactly at its boundary and previews two chords", () => {
    const timeline = buildChordTimeline([
        { chord: "C:maj", start: 0, end: 4 },
        { chord: "G:maj", start: 4, end: 7 },
        { chord: "A:min", start: 7, end: 9 }
    ], 9);

    const before = chordProgressionAt(timeline, 3);
    assert.equal(before.current.chord, "C:maj");
    assert.equal(before.next.chord, "G:maj");
    assert.equal(before.then.chord, "A:min");
    assert.equal(before.progress, 0.75);
    assert.equal(before.remaining, 1);

    const atChange = chordProgressionAt(timeline, 4);
    assert.equal(atChange.current.chord, "G:maj");
    assert.equal(atChange.progress, 0);
    assert.equal(atChange.next.chord, "A:min");
});

test("rests fill meaningful gaps while short analysis seams do not flash", () => {
    const timeline = buildChordTimeline([
        { chord: "C:maj", start: 0.04, end: 2 },
        { chord: "G:maj", start: 3, end: 5 }
    ], 6);

    assert.deepEqual(timeline.map(chord => chord.chord), ["C:maj", "N", "G:maj", "N"]);
    assert.equal(timeline[0].start, 0);
    assert.equal(chordProgressionAt(timeline, 2.5).current.rest, true);
    assert.equal(chordProgressionAt(timeline, 2.5).next.chord, "G:maj");
    assert.equal(chordProgressionAt(timeline, 5.5).current.rest, true);
});

test("seeks and invalid segments cannot produce invalid progress", () => {
    const timeline = buildChordTimeline([
        { chord: "C:maj", start: 0, end: 2 },
        { chord: "bad", start: 2, end: 2 },
        { chord: "G:maj", start: 2, end: 4 }
    ], 4);

    assert.equal(chordProgressionAt(timeline, 3).current.chord, "G:maj");
    assert.equal(chordProgressionAt(timeline, 1).current.chord, "C:maj");
    assert.equal(chordProgressionAt(timeline, Number.NaN).progress, 0);
    assert.equal(chordProgressionAt(timeline, 4).current, null);
});
