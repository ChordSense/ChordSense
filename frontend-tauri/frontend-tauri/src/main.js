import {
    loadRecordedAnalysis,
    stopPlayback
} from "./play-along.js";

import {
    initRecordMode
} from "./record.js";


const playMode =
    document.querySelector("#play-mode");

const recordMode =
    document.querySelector("#record-mode");

const playModeButton =
    document.querySelector("#play-mode-button");

const recordModeButton =
    document.querySelector("#record-mode-button");

const modeTitle =
    document.querySelector("#mode-title");


let currentMode = "play";

let isModeTransitioning = false;

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
        mode === "play"
            ? "Mode: Play Along"
            : "Mode: Record";

    playModeButton.classList.toggle(
        "active",
        mode === "play"
    );

    recordModeButton.classList.toggle(
        "active",
        mode === "record"
    );

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
        isModeTransitioning
    ) {
        return;
    }

    isModeTransitioning = true;

    const outgoingView =
        currentMode === "play"
            ? playMode
            : recordMode;

    const incomingView =
        mode === "play"
            ? playMode
            : recordMode;
    // stop playback when enterin record mode
    if (mode === "record") {
        stopPlayback();
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


/*
 * Preserve the old M shortcut.
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
                currentMode === "play"
                    ? "record"
                    : "play"
            );
        }
    }
);


initRecordMode({

    onAnalysisComplete(result) {

        /*
         * Send the recorded analysis to the
         * Play Along code.
         */
        loadRecordedAnalysis(result);

        /*
         * Same behavior as old Rust UI:
         * return to Play Along after recording.
         */
        showMode("play");
    },

    onRecordingLockChanged(locked) {

        /*
         * Prevent accidentally leaving Record Mode
         * halfway through recording.
         */
        playModeButton.disabled =
            locked;
    }
});
