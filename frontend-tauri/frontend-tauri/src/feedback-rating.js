/** Pure chord matching and temporal rating state for playback feedback. */

const EXPERIMENTAL_TEMPLATE_MODEL = "chordsense-chroma-template-experimental";

const PITCH_CLASS = Object.freeze({
    C: 0, "B#": 0, "C#": 1, Db: 1,
    D: 2, "D#": 3, Eb: 3,
    E: 4, Fb: 4, F: 5, "E#": 5,
    "F#": 6, Gb: 6, G: 7, "G#": 8, Ab: 8,
    A: 9, "A#": 10, Bb: 10, B: 11, Cb: 11
});

const QUALITY = Object.freeze({
    "": "major", maj: "major", major: "major",
    m: "minor", min: "minor", minor: "minor",
    "7": "dominant7", dom7: "dominant7",
    maj7: "major7", major7: "major7",
    m7: "minor7", min7: "minor7", minor7: "minor7"
});

export function normalizeChord(label) {
    if (typeof label !== "string") return null;
    const base = label.trim().split("/", 1)[0];
    if (!base || /^(N|Noise)$/i.test(base)) return null;
    const match = base.match(/^([A-Ga-g](?:#|b)?)(?::(.*)|(m(?:aj)?7|m7|7|m))?$/);
    if (!match) return null;
    const root = match[1][0].toUpperCase() + match[1].slice(1);
    const qualityText = (match[2] ?? match[3] ?? "").toLowerCase();
    const quality = QUALITY[qualityText];
    if (quality === undefined || PITCH_CLASS[root] === undefined) return null;
    return { pitchClass: PITCH_CLASS[root], quality };
}

export function classifyChordRating(expectedLabel, playedLabel) {
    const expected = normalizeChord(expectedLabel);
    const played = normalizeChord(playedLabel);
    if (!expected || !played) return null;
    if (!["major", "minor"].includes(played.quality)) return null;
    if (expected.pitchClass !== played.pitchClass) return "red";
    if (expected.quality === played.quality) return "green";
    return "yellow";
}

export function alignSampleTime(playbackTimeSeconds, sampleAgeMs, maxAgeMs = 750) {
    if (!Number.isFinite(playbackTimeSeconds) || !Number.isFinite(sampleAgeMs) ||
        sampleAgeMs < 0 || sampleAgeMs > maxAgeMs) return null;
    return Math.max(0, playbackTimeSeconds - sampleAgeMs / 1000);
}

export function expectedSegmentAt(segments, seconds) {
    if (!Number.isFinite(seconds)) return null;
    let low = 0;
    let high = segments.length - 1;
    while (low <= high) {
        const middle = (low + high) >> 1;
        const segment = segments[middle];
        if (seconds < segment.start) high = middle - 1;
        else if (seconds >= segment.end) low = middle + 1;
        else return { index: middle, segment };
    }
    return null;
}

export class FeedbackScore {
    constructor(segments) {
        this.segments = segments;
        this.reset();
    }

    reset() {
        this.segmentRatings = new Map();
    }

    record(result) {
        const index = result?.segmentIndex;
        if (!Number.isInteger(index) || index < 0 || index >= this.segments.length ||
            !normalizeChord(this.segments[index]?.chord)) return;

        const previous = this.segmentRatings.get(index);
        if (previous === "green") return;

        if (result.rating === "green" || result.rating === "red" ||
            result.rating === "yellow") {
            this.segmentRatings.set(index, result.rating);
        } else if (!this.segmentRatings.has(index)) {
            this.segmentRatings.set(index, null);
        }
    }

    summary() {
        const total = this.segmentRatings.size;
        if (!total) return null;

        let points = 0;
        for (const rating of this.segmentRatings.values()) {
            if (rating === "green") points += 1;
            else if (rating !== "red") points += 0.5;
        }

        return {
            points,
            total,
            percent: Math.round(points / total * 100)
        };
    }
}

export class FeedbackRater {
    constructor(segments, options = {}) {
        this.segments = segments;
        this.options = {
            confidence: 0.60,
            redConfidence: 0.70,
            chordGraceMs: 250,
            positiveDwellMs: 200,
            redDwellMs: 350,
            experimentalDwellMs: 500,
            minDisplayMs: 300,
            dropoutHoldMs: 240,
            maxAgeMs: 750,
            ...options
        };
        this.reset();
    }

    reset(sessionId = null) {
        this.sessionId = sessionId;
        this.lastSequence = -1;
        this.segmentIndex = null;
        this.rating = null;
        this.candidate = null;
        this.candidateSince = null;
        this.lastRatingChangeAt = null;
        this.lastReliableAt = null;
        this.lastRatingSupportAt = null;
    }

    advance(playbackTimeSeconds) {
        const current = expectedSegmentAt(this.segments, playbackTimeSeconds);
        if (!current || !normalizeChord(current.segment.chord)) {
            this.segmentIndex = null;
            this.rating = null;
            this.candidate = null;
            this.candidateSince = null;
            this.lastRatingSupportAt = null;
            return this._result(playbackTimeSeconds);
        }
        if (current.index !== this.segmentIndex) {
            this.segmentIndex = current.index;
            this.rating = null;
            this.candidate = null;
            this.candidateSince = null;
            this.lastRatingChangeAt = null;
            this.lastReliableAt = null;
            this.lastRatingSupportAt = null;
        } else {
            if (this.lastReliableAt !== null &&
                playbackTimeSeconds * 1000 - this.lastReliableAt > this.options.dropoutHoldMs) {
                this.candidate = null;
                this.candidateSince = null;
            }
            if (this.rating !== "green" && this.lastRatingSupportAt !== null &&
                playbackTimeSeconds * 1000 - this.lastRatingSupportAt > this.options.dropoutHoldMs) {
                this.rating = null;
                this.lastRatingChangeAt = null;
                this.lastRatingSupportAt = null;
            }
        }
        return this._result(playbackTimeSeconds);
    }

    _result(sampleTime = null) {
        return {
            rating: this.rating,
            segmentIndex: this.segmentIndex,
            expectedChord: this.segmentIndex === null
                ? null : this.segments[this.segmentIndex].chord,
            samplePlaybackTime: sampleTime
        };
    }

    observe(event) {
        if (event.sessionId !== this.sessionId ||
            !Number.isInteger(event.sequence) || event.sequence <= this.lastSequence) {
            return this._result();
        }
        if (this.lastSequence >= 0 && event.sequence > this.lastSequence + 1) {
            this.candidate = null;
            this.candidateSince = null;
        }
        this.lastSequence = event.sequence;
        const visible = this.advance(event.playbackTimeSeconds);
        // A confirmed match belongs to the chart segment, not to each later
        // inference window. Only a segment change or explicit reset clears it.
        if (this.rating === "green") return visible;
        const sampleTime = alignSampleTime(
            event.playbackTimeSeconds,
            event.sampleAgeMs,
            this.options.maxAgeMs
        );
        if (sampleTime === null) return visible;
        const current = expectedSegmentAt(this.segments, sampleTime);
        if (current?.index !== this.segmentIndex) return visible;
        if (!current || !normalizeChord(current.segment.chord)) {
            this.segmentIndex = null;
            this.rating = null;
            this.candidate = null;
            this.lastRatingSupportAt = null;
            return this._result(sampleTime);
        }
        if (current.index !== this.segmentIndex) {
            this.segmentIndex = current.index;
            this.rating = null;
            this.candidate = null;
            this.candidateSince = null;
            this.lastRatingChangeAt = null;
            this.lastReliableAt = null;
            this.lastRatingSupportAt = null;
        }
        const sampleMs = sampleTime * 1000;
        if (sampleMs - current.segment.start * 1000 < this.options.chordGraceMs) {
            return this._result(sampleTime);
        }
        const isExperimentalTemplate = event.model === EXPERIMENTAL_TEMPLATE_MODEL;
        const chordRating = classifyChordRating(current.segment.chord, event.chord);
        const proposedRating = isExperimentalTemplate &&
            event.qualityUncertain === true && chordRating === "green"
                ? "yellow" : chordRating;
        const neededConfidence =
            proposedRating === "red"
                ? this.options.redConfidence : this.options.confidence;
        const reliable = event.inputQuality === "ok" &&
            Number.isFinite(event.confidence) &&
            event.confidence >= neededConfidence;
        const proposed = reliable ? proposedRating : null;
        if (!proposed) {
            this.candidate = null;
            this.candidateSince = null;
            if (this.lastRatingSupportAt === null ||
                event.playbackTimeSeconds * 1000 - this.lastRatingSupportAt > this.options.dropoutHoldMs) {
                this.rating = null;
                this.lastRatingChangeAt = null;
                this.lastRatingSupportAt = null;
            }
            return this._result(sampleTime);
        }
        this.lastReliableAt = event.playbackTimeSeconds * 1000;
        if (proposed === this.rating) {
            this.lastRatingSupportAt = event.playbackTimeSeconds * 1000;
            this.candidate = null;
            this.candidateSince = null;
            return this._result(sampleTime);
        }
        if (proposed !== this.candidate) {
            this.candidate = proposed;
            this.candidateSince = sampleMs;
            return this._result(sampleTime);
        }
        const dwell = isExperimentalTemplate
            ? this.options.experimentalDwellMs
            : proposed === "red"
                ? this.options.redDwellMs : this.options.positiveDwellMs;
        const heldLongEnough = this.lastRatingChangeAt === null ||
            sampleMs - this.lastRatingChangeAt >= this.options.minDisplayMs;
        if (sampleMs - this.candidateSince >= dwell && heldLongEnough) {
            this.rating = proposed;
            this.lastRatingChangeAt = sampleMs;
            this.lastRatingSupportAt = event.playbackTimeSeconds * 1000;
            this.candidate = null;
            this.candidateSince = null;
        }
        return this._result(sampleTime);
    }
}
