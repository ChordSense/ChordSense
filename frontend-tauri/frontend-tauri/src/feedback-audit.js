/** Node-only replay of the production rating state machine. Not loaded by the UI. */
import { readFileSync, writeFileSync } from "node:fs";
import { FeedbackRater, FeedbackScore, normalizeChord } from "./feedback-rating.js";

const inputPath = process.argv[2];
if (!inputPath) throw new Error("Usage: node feedback-audit.js /path/to/replay.json");
const input = JSON.parse(readFileSync(inputPath, "utf8"));
const roots = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function shifted(label, semitones, flipQuality = false) {
    const chord = normalizeChord(label);
    if (!chord) return label;
    const minor = (chord.quality === "minor") !== flipQuality;
    return roots[(chord.pitchClass + semitones) % 12] + (minor ? "m" : "");
}

function replay(recording, dwell, semitones = 0, flipQuality = false) {
    const segments = recording.segments.map(segment => ({
        ...segment, chord: shifted(segment.chord, semitones, flipQuality)
    }));
    const rater = new FeedbackRater(segments, { experimentalDwellMs: dwell });
    const score = new FeedbackScore(segments);
    rater.reset("audit");
    const firstGreen = new Map();
    const counts = { green: 0, red: 0, yellow: 0, neutral: 0 };
    let sequence = 0;
    let switches = 0;
    let previous = null;
    for (const event of recording.events) {
        const result = rater.observe({
            sessionId: "audit", sequence: ++sequence,
            playbackTimeSeconds: event.delivery_seconds,
            sampleAgeMs: event.sample_age_ms,
            chord: event.chord, confidence: event.confidence,
            inputQuality: event.input_quality,
            model: "chordsense-chroma-template-experimental",
            qualityUncertain: event.quality_uncertain,
        });
        if (result.segmentIndex === null) { previous = null; continue; }
        counts[result.rating ?? "neutral"] += 1;
        if (result.rating !== previous) switches += 1;
        previous = result.rating;
        if (result.rating === "green" && !firstGreen.has(result.segmentIndex)) {
            firstGreen.set(result.segmentIndex,
                (event.delivery_seconds - segments[result.segmentIndex].start) * 1000);
        }
        score.record(result);
    }
    return { dwell_ms: dwell, counts, switches, score: score.summary(),
        green_segments: firstGreen.size,
        supported_segments: segments.filter(segment => normalizeChord(segment.chord)).length,
        first_green_ms: Object.fromEntries(firstGreen),
    };
}

const profiles = input.profiles.map(profile => ({
    name: profile.name,
    recordings: profile.recordings.map(recording => {
        const variants = [280, 200].map(dwell => {
            const matching = replay(recording, dwell);
            let wrongGreenSegments = 0;
            let wrongGreenEvents = 0;
            let wrongRatedEvents = 0;
            const falseGreenCases = [];
            // Every different root, with both qualities. The acoustic input
            // is unchanged: these are counterfactual charts, not new performances.
            for (let shift = 1; shift < 12; shift++) {
                for (const flip of [false, true]) {
                    const wrong = replay(recording, dwell, shift, flip);
                    wrongGreenSegments += wrong.green_segments;
                    wrongGreenEvents += wrong.counts.green;
                    wrongRatedEvents += Object.values(wrong.counts).reduce((a, b) => a + b, 0);
                    for (const [index, time] of Object.entries(wrong.first_green_ms)) {
                        const actual = recording.segments[Number(index)].chord;
                        falseGreenCases.push({ actual, expected: shifted(actual, shift, flip),
                            segment_index: Number(index), first_green_ms: time });
                    }
                }
            }
            const opposite = replay(recording, dwell, 0, true);
            return { matching, wrong_root_chart: {
                trials: matching.supported_segments * 22,
                false_green_segments: wrongGreenSegments,
                false_green_prediction_events: wrongGreenEvents,
                eligible_prediction_events: wrongRatedEvents,
                false_green_event_fraction: wrongRatedEvents ? wrongGreenEvents / wrongRatedEvents : null,
                false_green_cases: falseGreenCases,
            }, opposite_quality_chart: {
                false_green_segments: opposite.green_segments,
                counts: opposite.counts,
            }};
        });
        return { name: recording.name, label_kind: recording.label_kind, variants };
    }),
}));

// Demonstrate the current green latch and the score assigned to abstention.
const latch = new FeedbackRater([{ start: 0, end: 10, chord: "E" }]);
latch.reset("latch");
let latchResult;
for (let i = 0; i < 100; i++) {
    latchResult = latch.observe({ sessionId: "latch", sequence: i + 1,
        playbackTimeSeconds: i * 0.05, sampleAgeMs: 0,
        chord: i < 20 ? "E" : "A", confidence: 0.95, inputQuality: "ok" });
}
const noSignal = new FeedbackScore([{ start: 0, end: 10, chord: "E" }]);
noSignal.record({ segmentIndex: 0, rating: null });
const output = { scope: "Production rater replay; simulated transport, correlated windows",
    green_latch_after_four_seconds_of_wrong_chord: latchResult.rating,
    score_for_one_visited_unrated_segment: noSignal.summary(), profiles };
const outputPath = inputPath.replace(/replay\.json$/, "ratings.json");
if (outputPath === inputPath) throw new Error("Input filename must be replay.json");
writeFileSync(outputPath, JSON.stringify(output, null, 2) + "\n");
console.log(outputPath);
