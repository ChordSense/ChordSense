import {
    chordForCapo,
    recommendCapo
} from "./capo.js";
import { FeedbackRater } from "./feedback-rating.js";

const { invoke } = window.__TAURI__.core;
const { open } = window.__TAURI__.dialog;

const state = {
    audioPath: null,

    uploadedFileName: null,

    audioName: null,

    loading: false,
    analyzing: false,

    chords: [],

    capo: 0,
    recommendedCapo: null,

    analysisDuration: 0,

    lastActiveChordIndex: null,

    isChordTransitioning: false,
};

/*
 * Audio plays in iod, out of the pedal's headphone jack (I2S DAC), not in
 * this window. `player` mirrors the parts of an <audio> element this file
 * uses (paused / currentTime / duration), so chord sync reads iod's clock:
 * position is polled from the backend and interpolated between polls.
 */
const STATUS_POLL_MS = 250;

const player = {
    loaded: false,
    paused: true,
    duration: NaN,

    // bumped by every command so a status poll that was already in flight
    // can't undo it (e.g. report "paused" just after Play was pressed)
    generation: 0,

    // last position iod reported, and when (performance.now()) we got it
    basePosition: 0,
    baseTime: 0,

    get currentTime() {
        if (this.paused) {
            return this.basePosition;
        }

        const elapsed =
            (performance.now() - this.baseTime) / 1000;

        const position =
            this.basePosition + elapsed;

        return Number.isFinite(this.duration)
            ? Math.min(position, this.duration)
            : position;
    },

    setPosition(position) {
        this.basePosition = position;
        this.baseTime = performance.now();
    }
};

function playbackCommand(action, body = null) {
    player.generation += 1;

    return invoke(
        "playback_command",
        {
            action,
            body
        }
    );
}

/*
 * source is {path} for a local file / recorded take,
 * or {upload} for a song in the backend's uploads.
 */
async function loadPlayback(source) {
    const result =
        await playbackCommand(
            "load",
            source
        );

    player.loaded = true;
    player.paused = true;
    player.duration =
        result.duration ?? NaN;
    player.setPosition(0);

    await playbackCommand(
        "volume",
        {
            volume:
                Number(volumeSlider.value)
        }
    );
}

async function syncPlaybackStatus() {
    if (
        !player.loaded ||
        seekSlider.matches(":active")
    ) {
        return;
    }

    const generation =
        player.generation;

    let result;

    try {
        result =
            await invoke("playback_status");
    } catch (error) {
        console.error(
            "Playback status failed:",
            error
        );
        return;
    }

    if (generation !== player.generation) {
        return;
    }

    if (Number.isFinite(result.duration)) {
        player.duration = result.duration;
    }

    const wasPaused = player.paused;

    player.paused = !result.playing;
    player.setPosition(
        result.position ?? 0
    );

    if (result.finished) {
        if (feedback.phase !== "idle") stopFeedback();
    } else if (wasPaused !== player.paused) {
        if (player.paused) pauseFeedback();
        else if (feedback.enabled) void ensureFeedbackReady();
    }

    if (wasPaused !== player.paused) {
        updatePlayerUI();
        updateChordDisplay();
    }
}

setInterval(
    syncPlaybackStatus,
    STATUS_POLL_MS
);

const loadButton = document.querySelector("#load-audio");
const audioLibraryModal = document.querySelector("#audio-library-modal");

const audioLibraryList = document.querySelector("#audio-library-list");

const audioLibraryEmpty = document.querySelector("#audio-library-empty");

const audioLibraryStatus = document.querySelector("#audio-library-status");

const closeAudioLibraryButton = document.querySelector("#close-audio-library");

const cancelAudioLibraryButton = document.querySelector("#cancel-audio-library");

const loadLibrarySongButton = document.querySelector("#load-library-song");

const browseLocalAudioButton = document.querySelector("#browse-local-audio");
const libraryState = {
    open: false,

    songs: [],

    selectedIndex: -1,
};

const analyzeButton = document.querySelector("#analyze");

const stopButton = document.querySelector("#stop");
const playPauseButton = document.querySelector("#play-pause");
const playPauseImage =
    document.querySelector("#play-pause-image");

const volumeSlider = document.querySelector("#volume");
const seekSlider = document.querySelector("#seek");

const timeDisplay =
    document.querySelector("#time-display");

const songName =
    document.querySelector("#song-name");

const status =
    document.querySelector("#status");

const feedbackToggle = document.querySelector("#live-feedback-toggle");
const feedbackStatus = document.querySelector("#feedback-status");
const feedbackRating = document.querySelector("#feedback-rating");
const feedbackOverlay = document.querySelector("#feedback-screen-overlay");

const feedback = {
    enabled: false,
    phase: "idle",
    backendOwned: false,
    sessionId: null,
    generation: null,
    rater: null,
    epoch: 0,
    cutoffUnixMs: 0,
    queue: Promise.resolve(),
    pending: null,
    displayedRating: null,
};

function setFeedbackStatus(message) {
    if (feedbackStatus.textContent !== message) feedbackStatus.textContent = message;
}

function renderFeedbackRating(rating) {
    if (feedback.displayedRating === rating) return;
    feedback.displayedRating = rating;
    feedbackOverlay.classList.toggle("visible", rating === "green");
    feedbackRating.classList.toggle("hidden", !rating);
    if (rating) {
        feedbackRating.dataset.rating = rating;
        feedbackRating.textContent = {
            green: "Chord match",
            yellow: "Same root · check quality",
            red: "Different chord",
        }[rating];
        setFeedbackStatus(feedbackRating.textContent);
    } else {
        delete feedbackRating.dataset.rating;
        feedbackRating.textContent = "";
    }
}

function clearFeedbackEvaluation() {
    feedback.cutoffUnixMs = Date.now();
    feedback.rater?.reset(feedback.sessionId);
    renderFeedbackRating(null);
}

function queueFeedback(action) {
    const task = feedback.queue.then(action);
    feedback.queue = task.catch(() => {});
    return task;
}

function stopFeedback() {
    const wasActive = feedback.phase !== "idle" ||
        feedback.sessionId !== null || feedback.backendOwned;
    feedback.epoch += 1;
    feedback.phase = "idle";
    feedback.sessionId = null;
    feedback.generation = null;
    feedback.rater = null;
    clearFeedbackEvaluation();
    let pending = feedback.queue;
    if (wasActive) {
        pending = queueFeedback(async () => {
            if (!feedback.backendOwned) return;
            await invoke("stop_live_feedback");
            feedback.backendOwned = false;
        }).catch(error => {
            console.error("Could not stop live feedback:", error);
            setFeedbackStatus(`Could not stop live feedback: ${error}`);
        });
    }
    setFeedbackStatus(feedback.enabled ? "Live feedback ready." : "Live feedback off.");
    return pending;
}

function pauseFeedback() {
    if (feedback.phase !== "running") return;
    const token = ++feedback.epoch;
    feedback.phase = "paused";
    clearFeedbackEvaluation();
    setFeedbackStatus("Live feedback paused.");
    void queueFeedback(() => invoke("pause_live_feedback")).catch(error => {
        if (feedback.epoch !== token) return;
        console.error("Could not pause live feedback:", error);
        stopFeedback();
        setFeedbackStatus(`Live feedback error: ${error}`);
    });
}

const feedbackListenerReady = window.__TAURI__.event.listen(
    "live-feedback-event",
    ({ payload }) => {
        if (!feedback.enabled || payload.session_id !== feedback.sessionId) return;
        if (payload.generation != null && payload.generation !== feedback.generation) return;
        if (payload.type === "status") {
            const messages = {
                warming_up: "Listening for your guitar…",
                silence: "No guitar signal detected.",
                stream_gap: "Audio stream interrupted; listening again…",
                disconnected: "Live feedback disconnected; reconnecting…",
                paused: "Live feedback paused.",
            };
            if (payload.status === "stopped" || payload.status === "error") {
                const message = payload.error || "Live feedback stopped.";
                stopFeedback();
                setFeedbackStatus(message);
                return;
            }
            if (messages[payload.status]) {
                if (payload.status !== "paused") clearFeedbackEvaluation();
                setFeedbackStatus(messages[payload.status]);
            }
            return;
        }
        if (payload.type !== "prediction" || feedback.phase !== "running" || player.paused) return;
        if (payload.input_quality !== "ok") {
            clearFeedbackEvaluation();
            setFeedbackStatus(payload.input_quality === "clipping"
                ? "Guitar input is clipping; check the input signal and level."
                : "Waiting for a clear guitar signal…");
            return;
        }
        const sampleAge = payload.sample_age_ms;
        const emittedAt = payload.emitted_at_unix_ms;
        if (typeof sampleAge !== "number" || typeof emittedAt !== "number") return;
        const totalAge = sampleAge + Math.max(0, Date.now() - emittedAt);
        if (!Number.isFinite(totalAge) ||
            emittedAt - sampleAge < feedback.cutoffUnixMs) return;
        const result = feedback.rater?.observe({
            sessionId: feedback.sessionId,
            sequence: payload.sequence,
            playbackTimeSeconds: player.currentTime,
            sampleAgeMs: totalAge,
            chord: payload.chord,
            inputQuality: payload.input_quality,
            confidence: payload.confidence,
            model: payload.model,
            qualityUncertain: payload.quality_uncertain === true,
        });
        renderFeedbackRating(result?.rating ?? null);
        if (!result?.rating && feedbackStatus.textContent !== "Listening for your guitar…") {
            setFeedbackStatus("Listening for your guitar…");
        }
    }
);

async function ensureFeedbackReady() {
    if (!feedback.enabled || !state.chords.length) {
        if (feedback.enabled) setFeedbackStatus("Analyze the track before live feedback.");
        return;
    }
    if (feedback.phase === "running") return;
    if (feedback.phase === "starting") return feedback.pending;
    const resuming = feedback.phase === "paused" && feedback.sessionId !== null;
    const token = ++feedback.epoch;
    feedback.phase = "starting";
    clearFeedbackEvaluation();
    setFeedbackStatus(resuming ? "Resuming live feedback…" : "Starting live feedback…");
    const task = queueFeedback(async () => {
        try {
            await feedbackListenerReady;
            if (feedback.epoch !== token) return;
            if (!resuming && feedback.backendOwned) {
                await invoke("stop_live_feedback");
                feedback.backendOwned = false;
            }
            const response = await invoke(resuming
                ? "resume_live_feedback" : "start_live_feedback");
            feedback.backendOwned = true;
            if (feedback.epoch !== token) return;
            feedback.sessionId = response.session_id;
            feedback.generation = response.generation;
            feedback.rater = new FeedbackRater(state.chords);
            feedback.rater.reset(feedback.sessionId);
            feedback.phase = "running";
            feedback.cutoffUnixMs = Date.now();
            setFeedbackStatus("Listening for your guitar…");
        } catch (error) {
            if (feedback.epoch !== token) return;
            console.error("Could not start live feedback:", error);
            if (resuming) {
                try {
                    await invoke("stop_live_feedback");
                    feedback.backendOwned = false;
                } catch (stopError) {
                    console.error("Could not release live feedback:", stopError);
                }
            }
            feedback.phase = "idle";
            feedback.sessionId = null;
            feedback.rater = null;
            setFeedbackStatus(`Live feedback unavailable: ${error}`);
        }
    });
    feedback.pending = task;
    await task;
    if (feedback.pending === task) feedback.pending = null;
}

function advanceFeedback() {
    if (feedback.phase !== "running" || player.paused) return;
    const rating = feedback.rater?.advance(player.currentTime).rating ?? null;
    if (rating !== feedback.displayedRating) {
        renderFeedbackRating(rating);
        if (!rating) setFeedbackStatus("Listening for your guitar…");
    }
}

feedbackToggle.addEventListener("click", () => {
    feedback.enabled = !feedback.enabled;
    feedbackToggle.setAttribute("aria-pressed", String(feedback.enabled));
    feedbackToggle.textContent = feedback.enabled
        ? "Live Feedback: On" : "Live Feedback: Off";
    if (feedback.enabled) {
        if (!player.paused) void ensureFeedbackReady();
        else setFeedbackStatus(state.chords.length
            ? "Live feedback ready." : "Analyze the track before live feedback.");
    } else {
        stopFeedback();
    }
});

setFeedbackStatus("Live feedback off.");

const emptyState =
    document.querySelector("#empty-state");

const chordDisplay =
    document.querySelector("#chord-display");

const previousImage =
    document.querySelector("#previous-image");

const currentImage =
    document.querySelector("#current-image");

const nextImage =
    document.querySelector("#next-image");

const previousLabel =
    document.querySelector("#previous-label");

const currentLabel =
    document.querySelector("#current-label");

const nextLabel =
    document.querySelector("#next-label");

const incomingImage =
    document.querySelector("#incoming-image");

const incomingLabel =
    document.querySelector("#incoming-label");

const chordTrack =
    document.querySelector("#chord-track");

// Map these to physical buttons (TODO)
const capoDownButton =
    document.querySelector(
        "#capo-down"
    );

const capoUpButton =
    document.querySelector(
        "#capo-up"
    );

const capoPosition =
    document.querySelector(
        "#capo-position"
    );

const capoRecommendation =
    document.querySelector(
        "#capo-recommendation"
    );

const capoUseRecommendedButton =
    document.querySelector(
        "#capo-use-recommended"
    );

function updateCapoControls() {
    capoPosition.textContent =
        String(state.capo);

    capoDownButton.disabled =
        state.capo <= 0;

    capoUpButton.disabled =
        state.capo >= 11;
}


function refreshChordDisplayForCapo() {
    /*
     * Force the current carousel cards to
     * re-render even though playback has not
     * moved to another chord.
     */
    state.lastActiveChordIndex = null;

    updateChordDisplay();
}


function setCapo(position) {
    state.capo = Math.max(0, Math.min(11, position));

    updateCapoControls();

    refreshChordDisplayForCapo();
}


function clearCapoRecommendation() {
    state.recommendedCapo = null;

    capoRecommendation.textContent = "Recommended: —";

    capoRecommendation.removeAttribute("title");

    capoUseRecommendedButton.disabled = true;
}


function updateCapoRecommendation() {
    const recommendation = recommendCapo(state.chords);

    state.recommendedCapo = recommendation.capo;

    capoRecommendation.textContent =
        recommendation.capo === 0
            ? "Recommended: No Capo"
            : `Recommended: Capo ${recommendation.capo}`;

    capoRecommendation.title = `${recommendation.easyCount} of ` + `${recommendation.totalChords} ` + `chord shapes should be easier.`;

    capoUseRecommendedButton.disabled = false;
}


function resetCapoForNewAudio() {
    state.capo = 0;

    updateCapoControls();
    clearCapoRecommendation();
}

function formatFileSize(bytes) {

    if (!Number.isFinite(bytes)) {
        return "";
    }

    if (bytes < 1024) {
        return `${bytes} B`;
    }

    if (bytes < 1024 * 1024) {
        return (
            `${(bytes / 1024).toFixed(1)} KB`
        );
    }

    return (
        `${(
            bytes /
            (1024 * 1024)
        ).toFixed(1)} MB`
    );
}

function renderAudioLibrary() {

    audioLibraryList.replaceChildren();


    if (!libraryState.songs.length) {

        audioLibraryEmpty.classList.remove(
            "hidden"
        );

        loadLibrarySongButton.disabled =
            true;

        return;
    }


    audioLibraryEmpty.classList.add(
        "hidden"
    );


    libraryState.songs.forEach(
        (song, index) => {

            const row =
                document.createElement(
                    "button"
                );

            row.type =
                "button";

            row.className =
                "audio-library-song";

            row.tabIndex =
                -1;

            row.setAttribute(
                "role",
                "option"
            );


            const name =
                document.createElement(
                    "span"
                );

            name.className =
                "audio-library-song-name";

            name.textContent =
                song.name;


            const size =
                document.createElement(
                    "span"
                );

            size.className =
                "audio-library-song-size";

            size.textContent =
                formatFileSize(
                    song.size
                );


            row.append(
                name,
                size
            );


            row.addEventListener(
                "click",
                () => {
                    selectLibrarySong(
                        index
                    );
                }
            );


            row.addEventListener(
                "dblclick",
                () => {
                    selectLibrarySong(
                        index
                    );

                    loadSelectedLibrarySong();
                }
            );


            audioLibraryList.appendChild(
                row
            );
        }
    );


    selectLibrarySong(
        libraryState.selectedIndex >= 0
            ? libraryState.selectedIndex
            : 0
    );
}

function selectLibrarySong(index) {

    const count =
        libraryState.songs.length;


    if (!count) {

        libraryState.selectedIndex =
            -1;

        loadLibrarySongButton.disabled =
            true;

        return;
    }


    /*
     * Wrap around:
     *
     * Up on first song -> last song
     * Down on last song -> first song
     */
    const normalizedIndex =
        (
            index +
            count
        ) % count;


    libraryState.selectedIndex =
        normalizedIndex;


    const rows =
        audioLibraryList.querySelectorAll(
            ".audio-library-song"
        );


    rows.forEach(
        (row, rowIndex) => {

            const selected =
                rowIndex ===
                normalizedIndex;

            row.classList.toggle(
                "selected",
                selected
            );

            row.setAttribute(
                "aria-selected",
                String(selected)
            );
        }
    );


    const selectedRow =
        rows[normalizedIndex];


    selectedRow?.scrollIntoView({
        block: "nearest"
    });


    loadLibrarySongButton.disabled =
        false;
}

async function openAudioLibrary() {

    libraryState.open =
        true;

    libraryState.songs =
        [];

    libraryState.selectedIndex =
        -1;


    audioLibraryModal.classList.remove(
        "hidden"
    );

    audioLibraryModal.setAttribute(
        "aria-hidden",
        "false"
    );


    audioLibraryList.replaceChildren();

    audioLibraryEmpty.classList.add(
        "hidden"
    );

    loadLibrarySongButton.disabled =
        true;


    audioLibraryStatus.classList.remove(
        "error"
    );

    audioLibraryStatus.textContent =
        "Loading uploaded songs...";


    try {

        const songs =
            await invoke(
                "list_uploaded_audio"
            );


        libraryState.songs =
            songs ?? [];


        libraryState.selectedIndex =
            libraryState.songs.length
                ? 0
                : -1;


        audioLibraryStatus.textContent =
            libraryState.songs.length
                ? `${libraryState.songs.length} song${
                    libraryState.songs.length === 1
                        ? ""
                        : "s"
                  } available.`
                : "";


        renderAudioLibrary();


        requestAnimationFrame(
            () => {
                audioLibraryList.focus();
            }
        );

    } catch (error) {

        console.error(error);

        audioLibraryStatus.classList.add(
            "error"
        );

        audioLibraryStatus.textContent =
            `Could not load ChordSense Library: ${error}`;

        audioLibraryEmpty.classList.remove(
            "hidden"
        );

        audioLibraryEmpty.textContent =
            "ChordSense Library is unavailable.";
    }
}

function closeAudioLibrary() {

    libraryState.open =
        false;


    audioLibraryModal.classList.add(
        "hidden"
    );


    audioLibraryModal.setAttribute(
        "aria-hidden",
        "true"
    );


    loadButton.focus();
}

async function clearAudioSource() {
    const feedbackStopped = stopFeedback();
    const playbackStopped = player.loaded
        ? playbackCommand("stop")
        : Promise.resolve();

    player.loaded = false;
    player.paused = true;
    player.duration = NaN;
    player.setPosition(0);

    await Promise.all([feedbackStopped, playbackStopped]);
}

async function applyLoadedAudio({
    name,
    localPath = null,
    uploadedFileName = null
}) {

    await clearAudioSource();


    state.audioPath =
        localPath;

    state.uploadedFileName =
        uploadedFileName;

    state.audioName =
        name;


    state.chords = [];

    resetCapoForNewAudio();

    state.analysisDuration =
        0;

    state.lastActiveChordIndex =
        null;


    songName.textContent =
        name;


    status.textContent =
        "Audio loaded. Press Analyze.";


    emptyState.textContent =
        "Audio loaded. Press Analyze to detect chords.";


    chordDisplay.classList.add(
        "hidden"
    );

    emptyState.classList.remove(
        "hidden"
    );


    await loadPlayback(
        localPath
            ? { path: localPath }
            : { upload: uploadedFileName }
    );

    updatePlayerUI();
}

function simplifyChord(raw) {
    if (!raw || raw === "N") {
        return null;
    }

    const base = raw
        .split("/")[0]
        .trim();

    let root;
    let type;

    /*
     * Colon format from the model:
     *
     * D:maj
     * D:min
     * D:7
     * E:min7
     */
    if (base.includes(":")) {
        const [rawRoot, rawQuality = "maj"] =
            base.split(":");

        root = rawRoot.trim();

        const quality = rawQuality.trim().toLowerCase();

        if (
            quality === "maj7" ||
            quality === "major7"
        ) {
            type = "major7";
        }
        else if (
            quality === "min7" ||
            quality === "m7" ||
            quality === "minor7"
        ) {
            type = "minor7";
        }
        else if (
            quality === "7"
        ) {
            type = "7";
        }
        else if (
            quality.startsWith("min")
        ) {
            type = "minor";
        }
        else {
            type = "major";
        }
    }

    /*
     * Compact format:
     *
     * D
     * Dm
     * D7
     * Dm7
     * C#7
     * C#m7
     * Bb7
     * Bbm7
     */
    else {
        const match = base.match(/^([A-G](?:#|b)?)(m)?(7)?$/);

        if (!match) {
            console.warn("Unrecognized chord format:", raw);

            return null;
        }

        root = match[1];

        const isMinor =
            Boolean(match[2]);

        const isSeventh =
            Boolean(match[3]);

        if (isMinor && isSeventh) {
            type = "minor7";
        }
        else if (isMinor) {
            type = "minor";
        }
        else if (isSeventh) {
            type = "7";
        }
        else {
            type = "major";
        }
    }

    return {
        root,
        type
    };
}

const chordAssets = {
    "A": "a",
    "Ab": "ab",
    "G#": "ab",

    "B": "b",
    "Bb": "bb",
    "A#": "bb",

    "C": "c",
    "C#": "csharp",
    "Db": "csharp",

    "D": "d",

    "E": "e",
    "Eb": "eb",
    "D#": "eb",

    "F": "f",
    "F#": "fsharp",
    "Gb": "fsharp",

    "G": "g"
};

function chordImagePath(rawChord) {
    const parsed =
        simplifyChord(rawChord);

    if (!parsed) {
        return null;
    }

    const rootFile =
        chordAssets[parsed.root];

    if (!rootFile) {
        console.warn(
            "No root mapping for:",
            rawChord
        );

        return null;
    }

    let fileName;

    switch (parsed.type) {

        case "major":
            fileName = `${rootFile}.png`;
            break;

        case "minor":
            fileName = `${rootFile}m.png`;
            break;

        case "7":
            fileName = `${rootFile}7.png`;
            break;
        case "major7":
            fileName = `${rootFile}maj7.png`;
            break;
        case "minor7":
            fileName = `${rootFile}m7.png`;
            break;
        default:
            console.warn(
                "No exact chord diagram type for:",
                rawChord,
                parsed
            );

            return null;
    }

    return `assets/chords/${fileName}`;
}

function activeChordIndex(time) {
    return state.chords.findIndex(
        chord =>
            time >= chord.start &&
            time < chord.end
    );
}

function getChordSet(time) {
    const index =
        activeChordIndex(time);

    if (index === -1) {
        const nextIndex =
            state.chords.findIndex(
                chord => time < chord.start
            );

        if (nextIndex === -1) {
            return {
                index: -1,
                previous:
                    state.chords.at(-1) ?? null,
                current: null,
                next: null,
                incoming: null
            };
        }

        return {
            index: -1,
            previous:
                nextIndex > 0
                    ? state.chords[nextIndex - 1]
                    : null,
            current: null,
            next:
                state.chords[nextIndex] ?? null,
            incoming:
                state.chords[nextIndex + 1] ?? null
        };
    }

    return {
        index,

        previous:
            index > 0
                ? state.chords[index - 1]
                : null,

        current:
            state.chords[index],

        next:
            index < state.chords.length - 1
                ? state.chords[index + 1]
                : null,

        incoming:
            index < state.chords.length - 2
                ? state.chords[index + 2]
                : null
    };
}

function displayChord(
    chord,
    imageElement,
    labelElement
) {
    if (!chord) {
        imageElement.style.visibility = "hidden";
        imageElement.removeAttribute("src");
        labelElement.textContent = "";
        return;
    }

    /*
     * IMPORTANT:
     *
     * state.chords remains untouched.
     *
     * We only transpose the chord immediately
     * before it is displayed.
     */
    const displayedChord = chordForCapo(chord.chord, state.capo);

    const path = chordImagePath(displayedChord);

    labelElement.textContent = displayedChord;

    if (!path) {
        imageElement.style.visibility = "hidden";

        imageElement.removeAttribute("src");

        return;
    }

    imageElement.src = path;

    imageElement.style.visibility = "visible";
}

function updateChordDisplay() {
    if (!state.chords.length) {
        return;
    }

    const time = player.currentTime || 0;

    const {
        index,
        previous,
        current,
        next,
        incoming
    } = getChordSet(time);

    /*
     * Initial render or manual seek:
     * immediately show correct chords.
     */
    if (state.lastActiveChordIndex === null) {
        renderChordSet(previous, current, next, incoming);
        state.lastActiveChordIndex = index;
        return;
    }

    if (index === state.lastActiveChordIndex) {
        return;
    }

    /*
     * Usually one step during normal playback.
     */
    if (index === state.lastActiveChordIndex + 1) {
        rollToChord(previous, current, next, incoming, index);
        return;
    }

    /*
     * Large jump / unusual timing:
     * don't animate through several chords.
     */
    renderChordSet(previous, current, next, incoming);
    state.lastActiveChordIndex = index;
}

function rollToChord(previous, current, next, incoming, newIndex) {
    if (state.isChordTransitioning) {
        return;
    }
    state.isChordTransitioning = true;
    /*
     * IMPORTANT:
     *
     * Do NOT change the fourth card here.
     *
     * It already contains the chord that needs
     * to roll into the Next position.
     */
    chordTrack.classList.add("rolling");

    const finishTransition = () => {
        /*
        * 1. Freeze ALL transitions:
        *    track + individual chord cards.
        */
        chordTrack.classList.add("no-transition");

        chordTrack.classList.remove("rolling");

        renderChordSet(previous, current, next, incoming);

        void chordTrack.offsetHeight;

        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                chordTrack.classList.remove("no-transition");
                state.lastActiveChordIndex = newIndex;
                state.isChordTransitioning = false;
            });
        });
    };

    const onTrackTransitionEnd = (event) => {
        if (event.target !== chordTrack || event.propertyName !== "transform") {
            return;
        }

        chordTrack.removeEventListener("transitionend", onTrackTransitionEnd);
        finishTransition();
    };

    chordTrack.addEventListener("transitionend", onTrackTransitionEnd);
}

function renderChordSet(previous, current, next, incoming) {
    displayChord(previous, previousImage, previousLabel);

    displayChord(current, currentImage, currentLabel);

    displayChord(next, nextImage, nextLabel);

    displayChord(incoming, incomingImage, incomingLabel);
}

function updateControls() {
    const hasAudio = player.loaded;
    const canAnalyze = Boolean(state.audioPath || state.uploadedFileName);
    loadButton.disabled = state.loading || state.analyzing;
    analyzeButton.disabled = !canAnalyze || state.loading || state.analyzing;
    playPauseButton.disabled = !hasAudio;
    stopButton.disabled = !hasAudio;
    seekSlider.disabled = !hasAudio;
}

async function browseLocalAudio() {
    let selected;
    try {
        selected = await open({
            multiple: false,

            filters: [
                {
                    name: "Audio",

                    extensions: [
                        "wav",
                        "mp3",
                        "ogg",
                        "flac",
                        "m4a"
                    ]
                }
            ]
        });
    } catch (error) {

        console.error(error);
        status.textContent = `Could not open the file picker: ${error}`;
        return;
    }
    if (!selected) {
        return;
    }
    state.loading = true;
    updateControls();
    status.textContent = "Loading audio...";
    try {
        const name = selected.replaceAll("\\", "/").split("/").pop();
        await applyLoadedAudio({
            name,

            localPath:
                selected,

            uploadedFileName:
                null
        });

    } catch (error) {

        console.error(error);

        status.textContent = `Could not load audio: ${error}`;

    } finally {
        state.loading = false;
        updateControls();
    }
}

async function loadSelectedLibrarySong() {

    const index = libraryState.selectedIndex;


    if (index < 0 ||index >= libraryState.songs.length) {
        return;
    }
    const song =
        libraryState.songs[index];
    loadLibrarySongButton.disabled = true;
    state.loading = true;

    updateControls();
    audioLibraryStatus.textContent = `Loading ${song.name}...`;
    try {
        await applyLoadedAudio({
            name:
                song.name,

            localPath:
                null,

            uploadedFileName:
                song.name
        });


        closeAudioLibrary();

    } catch (error) {

        console.error(error);
        audioLibraryStatus.classList.add("error");
        audioLibraryStatus.textContent = `Could not load ${song.name}: ${error}`;
        loadLibrarySongButton.disabled = false;

    } finally {
        state.loading = false;
        updateControls();
    }
}

async function analyzeAudio() {
    if (!state.audioPath && !state.uploadedFileName) {
        status.textContent = "Please load an audio file first.";
        return;
    }
    if (state.analyzing) {
        return;
    }
    stopFeedback();

    state.analyzing = true;
    updateControls();

    status.textContent = "Analyzing audio...";
    emptyState.textContent = "Loading analysis...";

    try {
        let result;
        if (state.uploadedFileName) {

            /*
            * Song came from runtime/uploads.
            */
            result =
                await invoke("analyze_uploaded_audio",
                    {
                        filename:
                            state.uploadedFileName,

                        chordDict:
                            "submission"
                    }
                );

        } else {

            /*
            * Song came from normal local
            * filesystem picker.
            */
            result =
                await invoke(
                    "analyze_audio",
                    {
                        path:
                            state.audioPath,

                        chordDict:
                            "submission"
                    }
                );
        }

        console.log("Analysis result:", result);

        state.chords = result.chords ?? [];

        updateCapoRecommendation();

        state.analysisDuration = result.duration ?? 0;

        state.lastActiveChordIndex = null;

        if (result.cached) {

            status.textContent =
                `Loaded saved analysis. ` +
                `${state.chords.length} chords found.`;

            if (feedback.enabled && !player.paused) void ensureFeedbackReady();

        } else {

            status.textContent =
                `Analysis complete. ` +
                `${state.chords.length} chords found.`;
        }

        if (state.chords.length) {
            emptyState.classList.add("hidden");
            chordDisplay.classList.remove("hidden");
            updateChordDisplay();

        } else {
            chordDisplay.classList.add("hidden");
            emptyState.textContent = "No chords were detected in this audio.";
            emptyState.classList.remove("hidden");
        }

    } catch (error) {
        console.error(error);

        status.textContent = `Analysis failed: ${error}`;

        emptyState.textContent = "Analysis failed.";

    } finally {
        state.analyzing = false;
        updateControls();
    }
}

async function togglePlayback() {

    if (!player.loaded) {
        status.textContent = "Load an audio file before starting playback.";
        return;
    }

    if (player.paused) {
        try {
            if (feedback.enabled) await ensureFeedbackReady();

            await playbackCommand("play");

            const position = player.currentTime;
            player.paused = false;
            player.setPosition(position);
        } catch (error) {
            console.error("Playback failed:", error);
            status.textContent = `Playback failed: ${error}`;
            stopFeedback();
        }

    } else {
        const position =
            player.currentTime;

        player.paused = true;
        player.setPosition(position);
        pauseFeedback();

        try {
            await playbackCommand("pause");
        } catch (error) {
            console.error(error);
            status.textContent =
                `Pause failed: ${error}`;
        }
    }

    updatePlayerUI();
}

export function stopPlayback() {
    const feedbackStopped = stopFeedback();
    const playbackStopped = player.loaded
        ? playbackCommand("stop").catch(console.error)
        : Promise.resolve();

    player.paused = true;
    player.setPosition(0);

    updatePlayerUI();
    updateChordDisplay();
    return Promise.all([feedbackStopped, playbackStopped]);
}

function setVolume() {
    if (!player.loaded) {
        return;
    }

    playbackCommand(
        "volume",
        {
            volume:
                Number(volumeSlider.value)
        }
    ).catch(console.error);
}

// while dragging: move the display only
function previewSeek() {
    player.setPosition(
        Number(seekSlider.value)
    );

    state.lastActiveChordIndex = null;

    updatePlayerUI();
    updateChordDisplay();
}

// on release: actually move iod's playback position
function seekAudio() {
    const position =
        Number(seekSlider.value);

    player.setPosition(position);
    clearFeedbackEvaluation();
    if (feedback.enabled && feedback.phase === "running") {
        setFeedbackStatus("Listening for your guitar…");
    }

    playbackCommand(
        "seek",
        {
            position_secs: position
        }
    ).catch(error => {
        console.error(error);
        status.textContent =
            `Seek failed: ${error}`;
    });

    state.lastActiveChordIndex = null;

    updatePlayerUI();
    updateChordDisplay();
}

function getDuration() {
    const audioDuration =
        Number.isFinite(player.duration)
            ? player.duration
            : 0;

    return Math.max(
        audioDuration,
        state.analysisDuration
    );
}

function updatePlayerUI() {
    const duration =
        getDuration();

    seekSlider.max =
        duration || 0;

    if (!seekSlider.matches(":active")) {
        seekSlider.value =
            player.currentTime || 0;
    }

    timeDisplay.textContent =
        `${formatTime(player.currentTime)} / ` +
        `${formatTime(duration)}`;

    playPauseImage.src =
        player.paused
            ? "assets/icons/play-button.png"
            : "assets/icons/pause.png";

    playPauseButton.setAttribute(
        "aria-label",
        player.paused ? "Play" : "Pause"
    );

    updateControls();
}

function formatTime(seconds) {
    if (!Number.isFinite(seconds)) {
        seconds = 0;
    }

    const total =
        Math.max(
            0,
            Math.floor(seconds)
        );

    const minutes =
        Math.floor(total / 60);

    const remaining =
        total % 60;

    return (
        String(minutes).padStart(2, "0") +
        ":" +
        String(remaining).padStart(2, "0")
    );
}

function playbackLoop() {
    if (!player.paused) {
        updatePlayerUI();
        updateChordDisplay();
        advanceFeedback();
    }

    requestAnimationFrame(
        playbackLoop
    );
}

requestAnimationFrame(
    playbackLoop
);

loadButton.addEventListener(
    "click",
    openAudioLibrary
);

analyzeButton.addEventListener(
    "click",
    analyzeAudio
);

playPauseButton.addEventListener(
    "click",
    togglePlayback
);

stopButton.addEventListener(
    "click",
    stopPlayback
);

volumeSlider.addEventListener(
    "input",
    setVolume
);

seekSlider.addEventListener(
    "input",
    previewSeek
);
updateCapoControls();
clearCapoRecommendation();

seekSlider.addEventListener(
    "change",
    seekAudio
);

updatePlayerUI();

export async function loadRecordedAnalysis(result) {

    /*
     * Stop any previously loaded song.
     */
    await clearAudioSource();


    state.audioPath = null;
    state.uploadedFileName = null;

    state.audioName =
        "Recorded Session";

    state.chords =
        result.chords ?? [];

    resetCapoForNewAudio();
    updateCapoRecommendation();

    state.analysisDuration =
        result.duration ?? 0;

    state.lastActiveChordIndex =
        null;


    songName.textContent =
        "Recorded Session";


    if (state.chords.length) {

        emptyState.classList.add(
            "hidden"
        );

        chordDisplay.classList.remove(
            "hidden"
        );

    } else {

        chordDisplay.classList.add(
            "hidden"
        );

        emptyState.textContent =
            "No chords were detected in this recording.";

        emptyState.classList.remove(
            "hidden"
        );
    }


    /*
     * Load the take itself so the chord diagrams roll
     * against it, the same way a file loaded in Sense
     * mode does.
     *
     * iod writes the WAV on this machine and the backend
     * hands back its absolute path in wav_path.
     */
    const wavPath = result.wav_path;

    if (wavPath) {

        try {

            await loadPlayback({
                path: wavPath
            });

            /*
             * Also lets Analyze re-run the take through the
             * Sense-mode model.
             */
            state.audioPath = wavPath;

            status.textContent =
                `Recorded analysis loaded. ` +
                `${state.chords.length} chords found. ` +
                `Press play to hear your take.`;

        } catch (error) {

            console.error(error);

            /*
             * Analysis still succeeded, so show the chords
             * rather than failing the whole recording.
             */
            status.textContent =
                `Recorded analysis loaded ` +
                `(${state.chords.length} chords), but the ` +
                `take could not be played back: ${error}`;
        }

    } else {

        status.textContent =
            `Recorded analysis loaded. ` +
            `${state.chords.length} chords found.`;
    }


    updateChordDisplay();
    updatePlayerUI();
}

document.addEventListener(
    "keydown",
    event => {

        if (!libraryState.open) {
            return;
        }


        /*
         * Prevent the normal M shortcut
         * from switching modes while the
         * library is open.
         */
        if (
            event.key
                .toLowerCase() === "m"
        ) {

            event.stopPropagation();

            return;
        }


        if (
            event.key ===
            "ArrowDown"
        ) {

            event.preventDefault();
            event.stopPropagation();


            selectLibrarySong(
                libraryState.selectedIndex +
                1
            );

            return;
        }


        if (
            event.key ===
            "ArrowUp"
        ) {

            event.preventDefault();
            event.stopPropagation();


            selectLibrarySong(
                libraryState.selectedIndex -
                1
            );

            return;
        }


        if (
            event.key ===
            "Escape"
        ) {

            event.preventDefault();
            event.stopPropagation();


            closeAudioLibrary();

            return;
        }


        if (
            event.key ===
            "Enter"
        ) {

            /*
             * Let normal footer buttons
             * keep their normal Enter
             * behavior.
             */
            if (
                document.activeElement ===
                    browseLocalAudioButton ||
                document.activeElement ===
                    closeAudioLibraryButton ||
                document.activeElement ===
                    cancelAudioLibraryButton ||
                document.activeElement ===
                    loadLibrarySongButton
            ) {
                return;
            }


            event.preventDefault();
            event.stopPropagation();


            loadSelectedLibrarySong();
        }

    },
    true
);

closeAudioLibraryButton.addEventListener(
    "click",
    closeAudioLibrary
);


cancelAudioLibraryButton.addEventListener(
    "click",
    closeAudioLibrary
);


loadLibrarySongButton.addEventListener(
    "click",
    loadSelectedLibrarySong
);


browseLocalAudioButton.addEventListener(
    "click",
    async () => {

        closeAudioLibrary();

        await browseLocalAudio();
    }
);


audioLibraryModal.addEventListener(
    "click",
    event => {

        /*
         * Click dark background outside
         * dialog to close.
         */
        if (
            event.target ===
            audioLibraryModal
        ) {
            closeAudioLibrary();
        }
    }
);

capoDownButton.addEventListener(
    "click",
    () => {
        setCapo(
            state.capo - 1
        );
    }
);


capoUpButton.addEventListener(
    "click",
    () => {
        setCapo(
            state.capo + 1
        );
    }
);


capoUseRecommendedButton.addEventListener(
    "click",
    () => {
        if (
            state.recommendedCapo === null
        ) {
            return;
        }

        setCapo(
            state.recommendedCapo
        );
    }
);
