import test from "node:test";
import assert from "node:assert/strict";
import {
    HOLD_MS,
    LOCK_DELAY_MS,
    TUNINGS,
    applyTunerEvent,
    clampA4,
    createTunerState,
    describeDisplay,
    nearestStringIndex,
    needleAngle,
    stringLabels,
    tunerDisplay,
    tuningById,
    zoneFor
} from "./tuner-model.js";

const standard = tuningById("standard");

function pitch(midiNote, cents, hz = 110) {
    return { type: "pitch", note: "X", midi_note: midiNote, cents, f0_hz: hz };
}

test("tunings list low to high and label repeated top strings in lower case", () => {
    assert.deepEqual(stringLabels(standard), ["E", "A", "D", "G", "B", "e"]);
    assert.deepEqual(stringLabels(tuningById("dadgad")), ["D", "A", "d", "G", "a", "d"]);
    assert.equal(tuningById("missing"), TUNINGS[0]);
    for (const tuning of TUNINGS) {
        assert.deepEqual([...tuning.strings].sort((a, b) => a - b), tuning.strings);
    }
});

test("nearest string, colour zones, needle angle and A4 clamping", () => {
    assert.equal(nearestStringIndex(standard, 40.3), 0);
    assert.equal(nearestStringIndex(standard, 47.4), 1);
    assert.equal(nearestStringIndex(standard, 63), 5);
    assert.equal(zoneFor(-2.9), "in-tune");
    assert.equal(zoneFor(8), "close");
    assert.equal(zoneFor(-25), "far");
    assert.equal(needleAngle(0), 0);
    assert.equal(needleAngle(-25), -30);
    assert.equal(needleAngle(400), 60);
    assert.equal(clampA4("442"), 442);
    assert.equal(clampA4(1000), 466);
    assert.equal(clampA4("abc"), 440);
});

test("a steady in-tune note locks only after the dwell time and survives small wobbles", () => {
    const state = createTunerState();
    applyTunerEvent(state, pitch(45, 1.5), 0);
    assert.equal(tunerDisplay(state, 0, standard).locked, false);
    assert.equal(tunerDisplay(state, 0, standard).zone, "in-tune");

    applyTunerEvent(state, pitch(45, -2), LOCK_DELAY_MS - 1);
    assert.equal(state.locked, false);
    applyTunerEvent(state, pitch(45, 2), LOCK_DELAY_MS);
    assert.equal(state.locked, true);

    // 4 cents is outside the band but inside the unlock hysteresis.
    applyTunerEvent(state, pitch(45, 4), LOCK_DELAY_MS + 20);
    assert.equal(state.locked, true);
    assert.equal(tunerDisplay(state, LOCK_DELAY_MS + 20, standard).direction, null);

    applyTunerEvent(state, pitch(45, 9), LOCK_DELAY_MS + 40);
    const display = tunerDisplay(state, LOCK_DELAY_MS + 40, standard);
    assert.equal(display.locked, false);
    assert.equal(display.zone, "close");
    assert.equal(display.direction, "down");
});

test("unclear frames do not restart the in-tune timer", () => {
    const state = createTunerState();
    applyTunerEvent(state, pitch(45, 0), 0);
    applyTunerEvent(state, { type: "pitch", note: null, midi_note: null, cents: null }, 200);
    applyTunerEvent(state, pitch(45, 1), LOCK_DELAY_MS);
    assert.equal(state.locked, true);
});

test("a different note or silence clears the lock", () => {
    const state = createTunerState();
    applyTunerEvent(state, pitch(45, 0), 0);
    applyTunerEvent(state, pitch(45, 0), LOCK_DELAY_MS);
    assert.equal(state.locked, true);
    applyTunerEvent(state, pitch(50, 0), LOCK_DELAY_MS + 20);
    assert.equal(state.locked, false);

    applyTunerEvent(state, pitch(50, 0), LOCK_DELAY_MS * 3);
    assert.equal(state.locked, true);
    applyTunerEvent(state, { type: "status", status: "silence" }, LOCK_DELAY_MS * 3 + 20);
    assert.equal(state.locked, false);
    assert.equal(state.quality, "silence");
});

test("readings fade from live to held to idle", () => {
    const state = createTunerState();
    assert.equal(tunerDisplay(state, 0, standard).mode, "idle");
    applyTunerEvent(state, pitch(40, -12, 81.8), 1000);
    assert.equal(tunerDisplay(state, 1050, standard).mode, "live");
    assert.equal(tunerDisplay(state, 1300, standard).mode, "held");
    assert.equal(tunerDisplay(state, 1000 + HOLD_MS + 1, standard).mode, "idle");
    const live = tunerDisplay(state, 1050, standard);
    assert.equal(live.name, "E");
    assert.equal(live.octave, 2);
    assert.equal(live.stringIndex, 0);
    assert.equal(live.direction, "up");
});

test("a picked string measures cents from that string even past the next note", () => {
    const state = createTunerState();
    // Low E string tuned down to D#2 + 30 cents: auto mode would call it D#.
    applyTunerEvent(state, pitch(39, 30), 0, 40);
    const display = tunerDisplay(state, 0, standard);
    assert.equal(display.name, "E");
    assert.equal(Math.round(display.cents), -70);
    assert.equal(display.offScale, true);
    assert.equal(display.direction, "up");
    assert.equal(needleAngle(display.cents), -60);
});

test("screen-reader descriptions name the note and the direction to turn", () => {
    const state = createTunerState();
    applyTunerEvent(state, pitch(45, -7.4), 0);
    assert.equal(describeDisplay(tunerDisplay(state, 0, standard)), "A 2, 7 cents flat, tune up.");
    applyTunerEvent(state, pitch(45, 12), 10);
    assert.equal(describeDisplay(tunerDisplay(state, 10, standard)), "A 2, 12 cents sharp, tune down.");
    applyTunerEvent(state, pitch(45, 1), 20);
    assert.equal(describeDisplay(tunerDisplay(state, 20, standard)), "A 2, in tune.");
    assert.equal(
        describeDisplay({ mode: "idle", quality: "clipping" }),
        "Input too loud. Turn down the guitar volume."
    );
    assert.equal(describeDisplay({ mode: "idle", quality: "silence" }), "Play a single string.");
});
