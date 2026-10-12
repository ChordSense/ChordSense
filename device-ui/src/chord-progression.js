const MIN_VISIBLE_REST_SECONDS = 0.08;

export function buildChordTimeline(chords, duration = 0) {
    const ordered = (chords ?? [])
        .map((chord, sourceIndex) => ({
            ...chord,
            sourceIndex,
            start: Number(chord.start),
            end: Number(chord.end)
        }))
        .filter(chord =>
            Number.isFinite(chord.start) &&
            Number.isFinite(chord.end) &&
            chord.end > chord.start
        )
        .sort((a, b) => a.start - b.start || a.sourceIndex - b.sourceIndex);

    const timeline = [];
    let cursor = 0;

    for (const chord of ordered) {
        let start = Math.max(0, chord.start);
        const end = Math.max(0, chord.end);

        if (end <= cursor) continue;

        const gap = start - cursor;
        if (gap <= MIN_VISIBLE_REST_SECONDS && timeline.length === 0) {
            start = 0;
        } else if (gap > MIN_VISIBLE_REST_SECONDS) {
            timeline.push({ chord: "N", start: cursor, end: start, rest: true });
            cursor = start;
        } else if (gap > 0 && timeline.length) {
            timeline.at(-1).end = start;
            cursor = start;
        }

        timeline.push({
            ...chord,
            start: Math.max(start, cursor),
            end,
            rest: chord.chord === "N"
        });
        cursor = end;
    }

    const songEnd = Number(duration);
    if (Number.isFinite(songEnd) && songEnd - cursor > MIN_VISIBLE_REST_SECONDS) {
        timeline.push({ chord: "N", start: cursor, end: songEnd, rest: true });
    }

    return timeline;
}

export function chordProgressionAt(timeline, position) {
    const time = Number.isFinite(position) ? Math.max(0, position) : 0;
    const index = timeline.findIndex(chord => time >= chord.start && time < chord.end);

    if (index < 0) {
        const nextIndex = timeline.findIndex(chord => chord.start > time);
        return {
            index: -1,
            current: null,
            next: nextIndex < 0 ? null : timeline[nextIndex],
            then: nextIndex < 0 ? null : timeline[nextIndex + 1] ?? null,
            progress: 0,
            remaining: 0
        };
    }

    const current = timeline[index];
    const length = current.end - current.start;
    const progress = length > 0
        ? Math.max(0, Math.min(1, (time - current.start) / length))
        : 0;

    return {
        index,
        current,
        next: timeline[index + 1] ?? null,
        then: timeline[index + 2] ?? null,
        progress,
        remaining: Math.max(0, current.end - time)
    };
}
