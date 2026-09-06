const { invoke } = window.__TAURI__.core;
const { open } = window.__TAURI__.dialog;

console.log("ChordSense JS loaded");

// const status = document.querySelector("#backend-status");
// const button = document.querySelector("#check-backend");

const state = {
    audioPath: null,
    audioName: null,

    analyzing: false,

    chords: [],
    analysisDuration: 0,

    lastActiveChordIndex: -1,

    isChordTransitioning: false,
};

const audio = document.querySelector("#audio-player");

const loadButton = document.querySelector("#load-audio");
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
            fileName = `${rootFile}maj7.png`
            break;
        case "minor7":
            fileName = `${rootFile}m7.png`
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
        return {
            index: -1,
            previous: null,
            current: null,
            next: state.chords[0] ?? null,
            incoming: state.chords[1] ?? null
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

    console.log(
        "RAW CHORD:",
        chord.chord,
        "PARSED:",
        simplifyChord(chord.chord)
    );
    const path =
        chordImagePath(chord.chord);

    labelElement.textContent =
        chord.chord;

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

    const time =
        audio.currentTime || 0;

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
    if (state.lastActiveChordIndex === -1) {
        renderChordSet(
            previous,
            current,
            next,
            incoming
        );

        state.lastActiveChordIndex =
            index;

        return;
    }

    if (
        index ===
        state.lastActiveChordIndex
    ) {
        return;
    }

    /*
     * Usually one step during normal playback.
     */
    if (
        index ===
        state.lastActiveChordIndex + 1
    ) {
        rollToChord(
            previous,
            current,
            next,
            incoming,
            index
        );

        return;
    }

    /*
     * Large jump / unusual timing:
     * don't animate through several chords.
     */
    renderChordSet(
        previous,
        current,
        next,
        incoming
    );

    state.lastActiveChordIndex =
        index;
}

function rollToChord(
    previous,
    current,
    next,
    incoming,
    newIndex
) {
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

        renderChordSet(
            previous,
            current,
            next,
            incoming
        );

        void chordTrack.offsetHeight;

        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                chordTrack.classList.remove(
                    "no-transition"
                );

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

    displayChord(
        next,
        nextImage,
        nextLabel
    );

    displayChord(
        incoming,
        incomingImage,
        incomingLabel
    );
}

function prettyChord(raw) {
    if (!raw || raw === "N") {
        return "No chord";
    }

    return raw
        .replace(":maj", "")
        .replace(":min", "m");
}

function getAudioMimeType(path) {
    const lower = path.toLowerCase();

    if (lower.endsWith(".mp3")) {
        return "audio/mpeg";
    }

    if (lower.endsWith(".wav")) {
        return "audio/wav";
    }

    if (lower.endsWith(".ogg")) {
        return "audio/ogg";
    }

    return "application/octet-stream";
}

async function loadAudio() {
    const selected = await open({
        multiple: false,

        filters: [
            {
                name: "Audio",
                extensions: [
                    "wav",
                    "mp3",
                    "ogg"
                ]
            }
        ]
    });

    if (!selected) {
        return;
    }

    state.audioPath = selected;

    state.audioName =
        selected
            .replaceAll("\\", "/")
            .split("/")
            .pop();

    state.chords = [];
    state.analysisDuration = 0;
    state.lastActiveChordIndex = -1;

    songName.textContent =
        state.audioName;

    status.textContent =
        "Audio loaded. Press Analyze.";

    emptyState.innerHTML =
        "Audio loaded.<br />Press Analyze to detect chords.";

    chordDisplay.classList.add("hidden");
    emptyState.classList.remove("hidden");

    audio.pause();

    const audioBytes = await invoke(
        "load_audio_file",
        {
            path: selected
        }
    );

    const audioBlob = new Blob(
        [new Uint8Array(audioBytes)],
        {
            type: getAudioMimeType(selected)
        }
    );

    audio.src = URL.createObjectURL(audioBlob);

    audio.load();
}

async function analyzeAudio() {
    if (!state.audioPath) {
        status.textContent =
            "Please load an audio file first.";

        return;
    }

    if (state.analyzing) {
        return;
    }

    state.analyzing = true;

    analyzeButton.disabled = true;
    loadButton.disabled = true;

    status.textContent =
        "Analyzing audio...";

    emptyState.textContent =
        "Loading analysis...";

    try {
        const result = await invoke(
            "analyze_audio",
            {
                path: state.audioPath,
                chordDict: "submission"
            }
        );

        console.log(
            "Analysis result:",
            result
        );

        state.chords =
            result.chords ?? [];

        state.analysisDuration =
            result.duration ?? 0;

        state.lastActiveChordIndex = -1;

        status.textContent =
            `Analysis complete. ` +
            `${state.chords.length} chords found.`;

        emptyState.classList.add("hidden");
        chordDisplay.classList.remove("hidden");

        updateChordDisplay();

    } catch (error) {
        console.error(error);

        status.textContent =
            `Analysis failed: ${error}`;

        emptyState.textContent =
            "Analysis failed.";

    } finally {
        state.analyzing = false;

        analyzeButton.disabled = false;
        loadButton.disabled = false;
    }
}

// async function togglePlayback() {
//     if (!state.audioPath) {
//         return;
//     }

//     if (audio.paused) {
//         await audio.play();
//     } else {
//         audio.pause();
//     }
// }

async function togglePlayback() {
    if (!state.audioPath) {
        console.error("No audio path");
        return;
    }

    console.log("Audio src:", audio.src);
    console.log("readyState:", audio.readyState);
    console.log("networkState:", audio.networkState);
    console.log("duration:", audio.duration);
    console.log("audio error:", audio.error);

    if (audio.paused) {
        try {
            await audio.play();
            console.log("Playback started");
        } catch (error) {
            console.error("PLAYBACK FAILED:", error);
        }
    } else {
        audio.pause();
    }
}

export function stopPlayback() {
    audio.pause();
    audio.currentTime = 0;

    updatePlayerUI();
    updateChordDisplay();
}

function setVolume() {
    audio.volume =
        Number(volumeSlider.value);
}

// for using slider in the playback
function seekAudio() {
    audio.currentTime =
        Number(seekSlider.value);

    state.lastActiveChordIndex = -1;

    updatePlayerUI();
    updateChordDisplay();
}

function getDuration() {
    const audioDuration =
        Number.isFinite(audio.duration)
            ? audio.duration
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
            audio.currentTime || 0;
    }

    timeDisplay.textContent =
        `${formatTime(audio.currentTime)} / ` +
        `${formatTime(duration)}`;

    playPauseImage.src =
        audio.paused
            ? "assets/icons/play-button.png"
            : "assets/icons/pause.png";
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

audio.addEventListener(
    "timeupdate",
    () => {
        updatePlayerUI();
        updateChordDisplay();
    }
);

audio.addEventListener(
    "loadedmetadata",
    updatePlayerUI
);

audio.addEventListener(
    "play",
    updatePlayerUI
);

audio.addEventListener(
    "pause",
    updatePlayerUI
);

audio.addEventListener(
    "ended",
    () => {
        updatePlayerUI();
        updateChordDisplay();
    }
);

function playbackLoop() {
    if (!audio.paused) {
        updatePlayerUI();
        updateChordDisplay();
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
    loadAudio
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
    seekAudio
);

audio.volume = 0.8;

updatePlayerUI();

export async function loadRecordedAnalysis(
    result
) {

    /*
     * Stop any previously loaded song.
     */
    audio.pause();

    audio.removeAttribute("src");

    audio.load();


    state.audioPath = null;

    state.audioName =
        "Recorded Session";

    state.chords =
        result.chords ?? [];

    state.analysisDuration =
        result.duration ?? 0;

    state.lastActiveChordIndex =
        -1;


    songName.textContent =
        "Recorded Session";


    emptyState.classList.add(
        "hidden"
    );

    chordDisplay.classList.remove(
        "hidden"
    );


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

            const audioBytes = await invoke(
                "load_audio_file",
                {
                    path: wavPath
                }
            );

            const audioBlob = new Blob(
                [new Uint8Array(audioBytes)],
                {
                    type: getAudioMimeType(wavPath)
                }
            );

            audio.src =
                URL.createObjectURL(audioBlob);

            audio.load();

            /*
             * Setting this is what re-enables the transport
             * controls: play/pause, stop and seek all bail
             * out while audioPath is null.
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


// old function for checking backend connection
// async function checkBackend() {
//     status.textContent = "Checking...";

//     try {
//         const result = await invoke("backend_health");

//         console.log(result);

//         status.textContent = "Backend connected";
//     } catch (error) {
//         console.error(error);

//         status.textContent = `Backend error: ${error}`;
//     }
// }

// button.addEventListener("click", checkBackend);

// checkBackend();