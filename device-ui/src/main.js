import {
    loadRecordedAnalysis,
    stopPlayback
} from "./play-along.js";

import {
    initRecordMode
} from "./record.js";
import { initUploadPairing } from "./upload-pairing.js";
import { initTunerMode } from "./tuner.js";

initUploadPairing();

const tuner = initTunerMode();


const playMode =
    document.querySelector("#play-mode");

const recordMode =
    document.querySelector("#record-mode");

const playModeButton =
    document.querySelector("#play-mode-button");

const recordModeButton =
    document.querySelector("#record-mode-button");

const tunerMode =
    document.querySelector("#tuner-mode");

const tunerModeButton =
    document.querySelector("#tuner-mode-button");

const MODES = ["play", "record", "tune"];

const MODE_VIEWS = {
    play: playMode,
    record: recordMode,
    tune: tunerMode
};

const MODE_BUTTONS = {
    play: playModeButton,
    record: recordModeButton,
    tune: tunerModeButton
};

const MODE_TITLES = {
    play: "Mode: Play Along",
    record: "Mode: Record",
    tune: "Mode: Tune"
};

const modeTitle =
    document.querySelector("#mode-title");


let currentMode = "play";

let isModeTransitioning = false;

let isRecordingLocked = false;

function wait(milliseconds) {
    return new Promise(
        resolve =>
            setTimeout(
                resolve,
                milliseconds
            )
    );
}

async function updateHeader(mode) {


    modeTitle.classList.add(
        "header-leaving"
    );
    await wait(180);

    modeTitle.textContent =
        MODE_TITLES[mode];

    for (const [name, button] of Object.entries(MODE_BUTTONS)) {
        button.classList.toggle(
            "active",
            mode === name
        );

        button.setAttribute(
            "aria-pressed",
            String(mode === name)
        );
    }

    modeTitle.classList.remove(
        "header-leaving"
    );

    modeTitle.classList.add(
        "header-entering"
    );

    void modeTitle.offsetHeight;

    modeTitle.classList.remove(
        "header-entering"
    );

    await wait(180);
}

async function showMode(mode) {

    if (
        mode === currentMode ||
        isModeTransitioning ||
        (
            mode !== "record" &&
            isRecordingLocked
        )
    ) {
        return;
    }

    isModeTransitioning = true;

    const outgoingView =
        MODE_VIEWS[currentMode];

    const incomingView =
        MODE_VIEWS[mode];
    // Release the live stream before Record can claim the input. Tuning
    // mid-song also stops the backing track so the two never overlap.
    if (currentMode === "play") {
        await stopPlayback();
    }
    if (currentMode === "tune") {
        await tuner.leave();
    }
    // fade out
    const headerTransition = updateHeader(mode);
    outgoingView.classList.add("mode-leaving");
    await wait(180);
    // hide the old mode
    outgoingView.classList.add("hidden");
    outgoingView.classList.remove("mode-leaving");

    incomingView.classList.add("mode-entering");

    incomingView.classList.remove("hidden");

    void incomingView.offsetHeight;
    // animate the incoming view
    incomingView.classList.remove("mode-entering");
    // update mode controls
    currentMode = mode;
    if (mode === "tune") {
        void tuner.enter();
    }
    await Promise.all([
        headerTransition,
        wait(180)
    ]);
    isModeTransitioning = false;
}



playModeButton.addEventListener(
    "click",
    () => showMode("play")
);


recordModeButton.addEventListener(
    "click",
    () => showMode("record")
);


tunerModeButton.addEventListener(
    "click",
    () => showMode("tune")
);


/*
 * Preserve the old M shortcut; it now cycles Play Along, Record and Tune.
 */
window.addEventListener(
    "keydown",
    event => {

        const target =
            event.target;

        if (
            target instanceof HTMLInputElement ||
            target instanceof HTMLTextAreaElement
        ) {
            return;
        }

        if (event.key.toLowerCase() === "m") {
            showMode(
                MODES[
                    (MODES.indexOf(currentMode) + 1) % MODES.length
                ]
            );
        }
    }
);


initRecordMode({

    async onAnalysisComplete(result) {

        /*
         * Send the recorded analysis to the
         * Play Along code, which also loads the
         * captured take for playback.
         */
        await loadRecordedAnalysis(result);

        /*
         * Same behavior as old Rust UI:
         * return to Play Along after recording.
         */
        showMode("play");
    },

    onRecordingLockChanged(locked) {

        isRecordingLocked = locked;

        /*
         * Prevent accidentally leaving Record Mode
         * halfway through recording.
         */
        playModeButton.disabled =
            locked;

        tunerModeButton.disabled =
            locked;
    }
});
