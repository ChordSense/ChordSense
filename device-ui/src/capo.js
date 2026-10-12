const NOTES = [
    "C",
    "C#",
    "D",
    "Eb",
    "E",
    "F",
    "F#",
    "G",
    "Ab",
    "A",
    "Bb",
    "B"
];

const NOTE_INDEX = {
    "C": 0,
    "C#": 1,
    "Db": 1,

    "D": 2,
    "D#": 3,
    "Eb": 3,

    "E": 4,

    "F": 5,
    "F#": 6,
    "Gb": 6,

    "G": 7,
    "G#": 8,
    "Ab": 8,

    "A": 9,
    "A#": 10,
    "Bb": 10,

    "B": 11
};


/*
 * Beginner-friendly guitar shapes.
 *
 * This is intentionally a simple heuristic.
 * We can improve the scoring later if needed.
 */
const EASY_SHAPES = new Set([
    "C",
    "D",
    "E",
    "G",
    "A",

    "Am",
    "Dm",
    "Em",

    "A7",
    "B7",
    "C7",
    "D7",
    "E7",
    "G7",

    "Am7",
    "Dm7",
    "Em7",

    "Amaj7",
    "Cmaj7",
    "Dmaj7",
    "Emaj7",
    "Gmaj7"
]);


/*
 * We allow the user to manually choose
 * capo positions 0 through 11.
 *
 * For recommendations, stop at 7 because
 * very high capo positions are usually less
 * practical.
 */
const MAX_RECOMMENDED_CAPO = 7;


function transposeNote(note, semitones) {
    const index = NOTE_INDEX[note];

    if (index === undefined) {
        return null;
    }

    const newIndex = (index + semitones + 120) % 12;

    return NOTES[newIndex];
}


/*
 * Transpose the ROOT of a chord while keeping
 * its quality unchanged.
 *
 * Examples:
 *
 * G#:min -> E:min
 * G#m    -> Em
 * B:7    -> G:7
 */
function transposeChordPart(chord, semitones) {
    const trimmed = chord.trim();

    const match = trimmed.match(/^([A-G](?:#|b)?)(.*)$/);

    if (!match) {
        return trimmed;
    }

    const root = match[1];

    const quality = match[2];

    const newRoot = transposeNote(root, semitones);

    if (!newRoot) {
        return trimmed;
    }

    return (newRoot + quality);
}


/*
 * Convert the actual sounding chord into
 * the chord shape a guitarist should play
 * with the selected capo.
 *
 * Example:
 *
 * Actual song chord: B
 * Capo: 4
 * Guitar shape: G
 */
export function chordForCapo(rawChord, capo) {
    if (!rawChord) {
        return rawChord;
    }

    const trimmed = rawChord.trim();

    if (trimmed === "" || trimmed === "N" || trimmed === "NC" || trimmed === "N.C.") {
        return trimmed;
    }

    /*
     * Handle slash chords too.
     *
     * Example:
     * C/G
     */
    const parts =
        trimmed.split("/");

    const mainChord = transposeChordPart(parts[0], -capo);

    if (parts.length === 1) {
        return mainChord;
    }

    const bass =
        transposeNote(parts[1].trim(), -capo);

    if (!bass) {
        return mainChord;
    }

    return `${mainChord}/${bass}`;
}


/*
 * Turn the various chord formats used by
 * ChordSense into one comparable name for
 * recommendation scoring.
 *
 * Examples:
 *
 * D:maj  -> D
 * D:min  -> Dm
 * D:min7 -> Dm7
 * D:7    -> D7
 */
function shapeName(rawChord) {
    if (!rawChord) {
        return null;
    }

    const base = rawChord.split("/")[0].trim();

    const match = base.match(/^([A-G](?:#|b)?)(.*)$/);

    if (!match) {
        return null;
    }

    const root = match[1];

    let quality = match[2].trim().toLowerCase();

    if (quality.startsWith(":")) {
        quality = quality.slice(1);
    }

    if (quality === "" || quality === "maj" || quality === "major"
    ) {
        return root;
    }

    if (
        quality === "m" ||
        quality === "min" ||
        quality === "minor"
    ) {
        return `${root}m`;
    }

    if (quality === "7") {
        return `${root}7`;
    }

    if (
        quality === "m7" ||
        quality === "min7" ||
        quality === "minor7"
    ) {
        return `${root}m7`;
    }

    if (
        quality === "maj7" ||
        quality === "major7"
    ) {
        return `${root}maj7`;
    }

    return null;
}


function removeConsecutiveDuplicates(
    chords
) {
    const result = [];

    for (const chord of chords) {
        if (
            result.length === 0 ||
            result[result.length - 1] !== chord
        ) {
            result.push(chord);
        }
    }

    return result;
}


/*
 * Recommend a capo from the analyzed song.
 *
 * Accepts the actual state.chords array from
 * play-along.js.
 */
export function recommendCapo(
    chordEvents
) {
    const chords =
        chordEvents
            .map(item =>
                typeof item === "string"
                    ? item
                    : item?.chord
            )
            .filter(chord =>
                chord &&
                chord !== "N" &&
                chord !== "NC" &&
                chord !== "N.C."
            );

    const progression = removeConsecutiveDuplicates(chords);

    if (!progression.length) {
        return {
            capo: 0,
            easyCount: 0,
            totalChords: 0
        };
    }

    let bestCapo = 0;
    let bestEasyCount = -1;

    for (let capo = 0; capo <= MAX_RECOMMENDED_CAPO; capo++) {
        let easyCount = 0;

        for (const chord of progression) {
            const transposed = chordForCapo(chord, capo);

            const shape = shapeName(transposed);

            if (shape && EASY_SHAPES.has(shape)) {
                easyCount++;
            }
        }

        /*
         * Strictly greater means ties naturally
         * keep the lower capo position.
         */
        if (easyCount > bestEasyCount) {
            bestEasyCount = easyCount;

            bestCapo = capo;
        }
    }

    return {
        capo: bestCapo,
        easyCount: bestEasyCount,
        totalChords:
            progression.length
    };
}