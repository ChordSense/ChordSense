/*
 * Pure tuner display logic: tunings, string matching, colour zones, the
 * in-tune lock, and holding a reading briefly while a string decays. The
 * backend already smooths pitch; this turns its events into what to draw.
 */

export const NOTE_NAMES = [
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B"
];

// Low string first, as MIDI note numbers.
export const TUNINGS = [
    { id: "standard", name: "Standard", strings: [40, 45, 50, 55, 59, 64] },
    { id: "half-step-down", name: "Half Step Down", strings: [39, 44, 49, 54, 58, 63] },
    { id: "drop-d", name: "Drop D", strings: [38, 45, 50, 55, 59, 64] },
    { id: "dadgad", name: "DADGAD", strings: [38, 45, 50, 55, 57, 62] },
    { id: "open-g", name: "Open G", strings: [38, 43, 50, 55, 59, 62] }
];

export const DEFAULT_A4_HZ = 440;
export const MIN_A4_HZ = 415;
export const MAX_A4_HZ = 466;

export const IN_TUNE_CENTS = 3;
export const CLOSE_CENTS = 10;
export const METER_RANGE_CENTS = 50;
// Hold the in-tune band this long before declaring the string tuned.
export const LOCK_DELAY_MS = 500;
// Leave the lock only once clearly outside the band, so it doesn't flicker.
export const UNLOCK_CENTS = 5;
// Keep the last reading on screen this long after the signal fades.
export const HOLD_MS = 600;

export function noteName(midi) {
    return NOTE_NAMES[((midi % 12) + 12) % 12];
}

export function noteOctave(midi) {
    return Math.floor(midi / 12) - 1;
}

export function tuningById(id) {
    return TUNINGS.find(tuning => tuning.id === id) ?? TUNINGS[0];
}

/*
 * The first string of a tuning gets an upper-case name and a repeated note
 * name on the top string a lower-case one, as on a guitar headstock (E ... e).
 */
export function stringLabels(tuning) {
    const seen = new Set();
    return tuning.strings.map(midi => {
        const name = noteName(midi);
        const label = seen.has(name) ? name.toLowerCase() : name;
        seen.add(name);
        return label;
    });
}

export function nearestStringIndex(tuning, midi) {
    let best = 0;
    for (let index = 1; index < tuning.strings.length; index += 1) {
        if (Math.abs(tuning.strings[index] - midi) < Math.abs(tuning.strings[best] - midi)) {
            best = index;
        }
    }
    return best;
}

export function zoneFor(cents) {
    const offset = Math.abs(cents);
    if (offset <= IN_TUNE_CENTS) return "in-tune";
    if (offset <= CLOSE_CENTS) return "close";
    return "far";
}

export function clampA4(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return DEFAULT_A4_HZ;
    return Math.min(MAX_A4_HZ, Math.max(MIN_A4_HZ, Math.round(number)));
}

/* Needle angle in degrees for a cents offset: ±50 cents spans ±60°. */
export function needleAngle(cents) {
    const limited = Math.max(-METER_RANGE_CENTS, Math.min(METER_RANGE_CENTS, cents));
    return limited / METER_RANGE_CENTS * 60;
}

export function createTunerState() {
    return {
        reading: null,
        lastHeardAt: -Infinity,
        inTuneSince: null,
        locked: false,
        lockedKey: null,
        quality: "warming_up"
    };
}

/*
 * Apply one backend event. `targetMidi` is the string the player picked, or
 * null for automatic: with a target, cents are measured from that string even
 * when the guitar is far enough off to sound like a neighbouring note.
 */
export function applyTunerEvent(state, event, nowMs, targetMidi = null) {
    if (event.type === "status") {
        if (["silence", "clipping", "listening", "warming_up",
             "stream_gap", "disconnected", "error", "stopped"].includes(event.status)) {
            state.quality = event.status;
        }
        if (event.status !== "listening") clearLock(state);
        return state;
    }
    if (event.type !== "pitch") return state;
    if (event.note === null || event.midi_note === null || event.cents === null) {
        // Unclear frame (a decaying or noisy string): keep the last reading
        // until HOLD_MS runs out. A single unclear frame mid-note must not
        // restart the in-tune timer, so leave the lock state alone too.
        return state;
    }
    state.quality = "listening";
    const midi = event.midi_note + event.cents / 100;
    const reference = targetMidi ?? event.midi_note;
    const cents = (midi - reference) * 100;
    const key = `${reference}`;
    if (state.lockedKey !== key) clearLock(state);
    state.reading = {
        midi: reference,
        cents,
        hz: event.f0_hz,
        name: noteName(reference),
        octave: noteOctave(reference)
    };
    state.lastHeardAt = nowMs;

    if (Math.abs(cents) <= IN_TUNE_CENTS) {
        state.inTuneSince ??= nowMs;
        if (nowMs - state.inTuneSince >= LOCK_DELAY_MS) {
            state.locked = true;
            state.lockedKey = key;
        }
    } else {
        state.inTuneSince = null;
        if (Math.abs(cents) > UNLOCK_CENTS) {
            state.locked = false;
        }
    }
    if (!state.locked) state.lockedKey = key;
    return state;
}

function clearLock(state) {
    state.inTuneSince = null;
    state.locked = false;
    state.lockedKey = null;
}

/*
 * What the screen should show right now. `mode` is "idle" (no recent note),
 * "live" (a current reading) or "held" (fading reading kept briefly).
 */
export function tunerDisplay(state, nowMs, tuning) {
    const age = nowMs - state.lastHeardAt;
    if (!state.reading || age > HOLD_MS) {
        return {
            mode: "idle",
            quality: state.quality,
            stringIndex: null
        };
    }
    const { reading } = state;
    const held = age > 150;
    return {
        mode: held ? "held" : "live",
        quality: state.quality,
        name: reading.name,
        octave: reading.octave,
        cents: reading.cents,
        hz: reading.hz,
        zone: state.locked ? "in-tune" : zoneFor(reading.cents),
        locked: state.locked,
        direction: state.locked || Math.abs(reading.cents) <= IN_TUNE_CENTS
            ? null
            : reading.cents < 0 ? "up" : "down",
        stringIndex: nearestStringIndex(tuning, reading.midi),
        offScale: Math.abs(reading.cents) > METER_RANGE_CENTS
    };
}

/* Short screen-reader text, e.g. "A 2, 7 cents flat, tune up". */
export function describeDisplay(display) {
    if (display.mode === "idle") {
        if (display.quality === "clipping") return "Input too loud. Turn down the guitar volume.";
        if (display.quality === "error" || display.quality === "disconnected") {
            return "Guitar input unavailable.";
        }
        return "Play a single string.";
    }
    const note = `${display.name} ${display.octave}`;
    if (display.locked || display.zone === "in-tune") return `${note}, in tune.`;
    const cents = Math.round(Math.abs(display.cents));
    return display.cents < 0
        ? `${note}, ${cents} cents flat, tune up.`
        : `${note}, ${cents} cents sharp, tune down.`;
}
