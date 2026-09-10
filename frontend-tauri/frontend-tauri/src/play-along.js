const { invoke } = window.__TAURI__.core;
const { open } = window.__TAURI__.dialog;

const state = {
    audioPath: null,

    uploadedFileName: null,

    audioName: null,

    audioObjectUrl: null,

    loading: false,
    analyzing: false,

    chords: [],
    analysisDuration: 0,

    lastActiveChordIndex: null,

    isChordTransitioning: false,
};

const audio = document.querySelector("#audio-player");

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

function clearAudioSource() {

    audio.pause();

    audio.removeAttribute("src");

    audio.load();


    if (state.audioObjectUrl) {

        URL.revokeObjectURL(
            state.audioObjectUrl
        );

        state.audioObjectUrl =
            null;
    }
}

function applyLoadedAudio({
    name,
    bytes,
    localPath = null,
    uploadedFileName = null
}) {

    clearAudioSource();


    state.audioPath =
        localPath;

    state.uploadedFileName =
        uploadedFileName;

    state.audioName =
        name;


    state.chords = [];

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


    const audioBlob =
        new Blob(
            [
                new Uint8Array(
                    bytes
                )
            ],
            {
                type:
                    getAudioMimeType(
                        name
                    )
            }
        );


    state.audioObjectUrl =
        URL.createObjectURL(
            audioBlob
        );


    audio.src =
        state.audioObjectUrl;


    audio.load();

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
    if (state.lastActiveChordIndex === null) {
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

function getAudioMimeType(path) {

    const lower =
        path.toLowerCase();


    if (lower.endsWith(".mp3")) {
        return "audio/mpeg";
    }


    if (lower.endsWith(".wav")) {
        return "audio/wav";
    }


    if (lower.endsWith(".ogg")) {
        return "audio/ogg";
    }


    if (lower.endsWith(".flac")) {
        return "audio/flac";
    }


    if (lower.endsWith(".m4a")) {
        return "audio/mp4";
    }


    return "application/octet-stream";
}

function updateControls() {

    const hasAudio =
        Boolean(
            audio.getAttribute("src")
        );

    const canAnalyze =
        Boolean(
            state.audioPath ||
            state.uploadedFileName
        );


    loadButton.disabled =
        state.loading || state.analyzing;

    analyzeButton.disabled =
        !canAnalyze ||
        state.loading ||
        state.analyzing;

    playPauseButton.disabled =
        !hasAudio;

    stopButton.disabled =
        !hasAudio;

    seekSlider.disabled =
        !hasAudio;
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

        status.textContent =
            `Could not open the file picker: ${error}`;

        return;
    }


    if (!selected) {
        return;
    }


    state.loading = true;

    updateControls();

    status.textContent =
        "Loading audio...";


    try {

        const audioBytes =
            await invoke(
                "load_audio_file",
                {
                    path: selected
                }
            );


        const name =
            selected
                .replaceAll(
                    "\\",
                    "/"
                )
                .split("/")
                .pop();


        applyLoadedAudio({
            name,
            bytes:
                audioBytes,

            localPath:
                selected,

            uploadedFileName:
                null
        });

    } catch (error) {

        console.error(error);

        status.textContent =
            `Could not load audio: ${error}`;

    } finally {

        state.loading = false;

        updateControls();
    }
}

async function loadSelectedLibrarySong() {

    const index =
        libraryState.selectedIndex;


    if (
        index < 0 ||
        index >=
            libraryState.songs.length
    ) {
        return;
    }


    const song =
        libraryState.songs[index];


    loadLibrarySongButton.disabled =
        true;

    state.loading = true;

    updateControls();


    audioLibraryStatus.textContent =
        `Loading ${song.name}...`;


    try {

        const audioBytes =
            await invoke(
                "load_uploaded_audio",
                {
                    filename:
                        song.name
                }
            );


        applyLoadedAudio({
            name:
                song.name,

            bytes:
                audioBytes,

            localPath:
                null,

            uploadedFileName:
                song.name
        });


        closeAudioLibrary();

    } catch (error) {

        console.error(error);


        audioLibraryStatus.classList.add(
            "error"
        );


        audioLibraryStatus.textContent =
            `Could not load ${song.name}: ${error}`;


        loadLibrarySongButton.disabled =
            false;

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

    state.analyzing = true;

    updateControls();

    status.textContent =
        "Analyzing audio...";

    emptyState.textContent =
        "Loading analysis...";

    try {
        let result;


        if (state.uploadedFileName) {

            /*
            * Song came from runtime/uploads.
            */
            result =
                await invoke(
                    "analyze_uploaded_audio",
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

        console.log(
            "Analysis result:",
            result
        );

        state.chords =
            result.chords ?? [];

        state.analysisDuration =
            result.duration ?? 0;

        state.lastActiveChordIndex = null;

        status.textContent =
            `Analysis complete. ` +
            `${state.chords.length} chords found.`;

        if (state.chords.length) {

            emptyState.classList.add("hidden");

            chordDisplay.classList.remove("hidden");

            updateChordDisplay();

        } else {

            chordDisplay.classList.add("hidden");

            emptyState.textContent =
                "No chords were detected in this audio.";

            emptyState.classList.remove("hidden");
        }

    } catch (error) {
        console.error(error);

        status.textContent =
            `Analysis failed: ${error}`;

        emptyState.textContent =
            "Analysis failed.";

    } finally {
        state.analyzing = false;

        updateControls();
    }
}

async function togglePlayback() {

    /*
     * Playback depends on whether the audio
     * element has a source, not whether that
     * source came from a local filesystem path.
     */
    if (!audio.getAttribute("src")) {
        status.textContent =
            "Load an audio file before starting playback.";
        return;
    }

    if (audio.paused) {
        try {

            await audio.play();

        } catch (error) {

            console.error(
                "Playback failed:",
                error
            );

            status.textContent =
                `Playback failed: ${error}`;
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

    state.lastActiveChordIndex = null;

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

    playPauseButton.setAttribute(
        "aria-label",
        audio.paused ? "Play" : "Pause"
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
    seekAudio
);

audio.volume = Number(volumeSlider.value);

updatePlayerUI();

export async function loadRecordedAnalysis(
    result
) {

    /*
     * Stop any previously loaded song.
     */
    clearAudioSource();


    state.audioPath = null;
    state.uploadedFileName = null;

    state.audioName =
        "Recorded Session";

    state.chords =
        result.chords ?? [];

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

            state.audioObjectUrl =
                URL.createObjectURL(audioBlob);

            audio.src =
                state.audioObjectUrl;

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
