import {
    loadRecordedAnalysis
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


function showMode(mode) {

    currentMode = mode;

    if (mode === "play") {

        playMode.classList.remove("hidden");
        recordMode.classList.add("hidden");

        playModeButton.classList.add("active");
        recordModeButton.classList.remove("active");

        modeTitle.textContent =
            "Mode: Play Along";

    } else {

        playMode.classList.add("hidden");
        recordMode.classList.remove("hidden");

        playModeButton.classList.remove("active");
        recordModeButton.classList.add("active");

        modeTitle.textContent =
            "Mode: Record";
    }
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

        if (
            event.key.toLowerCase() === "m"
        ) {
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


showMode("play");