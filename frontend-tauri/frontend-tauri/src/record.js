const { invoke } =
    window.__TAURI__.core;


const RecordState = {
    IDLE: "idle",
    COUNTDOWN: "countdown",
    STARTING: "starting",
    RECORDING: "recording",
    PROCESSING: "processing"
};


let recordState =
    RecordState.IDLE;

let countdownStartedAt = 0;

let recordingStartedAt = 0;

let animationFrame = null;

let beginRecordingCalled = false;


export function initRecordMode({
    onAnalysisComplete,
    onRecordingLockChanged
}) {

    const startButton =
        document.querySelector(
            "#start-recording"
        );

    const stopButton =
        document.querySelector(
            "#stop-recording"
        );

    const recordTitle =
        document.querySelector(
            "#record-title"
        );

    const recordDetail =
        document.querySelector(
            "#record-detail"
        );

    const recordStatus =
        document.querySelector(
            "#record-status"
        );

    const countdown =
        document.querySelector(
            "#record-countdown"
        );

    const timer =
        document.querySelector(
            "#record-timer"
        );

    const indicator =
        document.querySelector(
            "#record-indicator"
        );


    function setStatus(
        message,
        isError = false
    ) {
        recordStatus.textContent =
            message;

        recordStatus.classList.toggle(
            "error",
            isError
        );
    }


    function updateButtons() {

        startButton.disabled =
            recordState !==
            RecordState.IDLE;

        stopButton.disabled = ![
            RecordState.COUNTDOWN,
            RecordState.RECORDING
        ].includes(recordState);
    }


    function updateModeLock() {

        onRecordingLockChanged?.(
            recordState !==
            RecordState.IDLE
        );
    }


    function formatTime(seconds) {

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
            String(minutes)
                .padStart(2, "0")
            +
            ":"
            +
            String(remaining)
                .padStart(2, "0")
        );
    }


    function setIdle() {

        stopAnimationLoop();

        recordState =
            RecordState.IDLE;

        beginRecordingCalled =
            false;

        countdown.classList.add(
            "hidden"
        );

        timer.classList.add(
            "hidden"
        );

        timer.textContent =
            "00:00";

        indicator.classList.remove(
            "recording"
        );

        recordTitle.textContent =
            "Ready to Record";

        recordDetail.textContent =
            "Press Start Recording when you're ready to play.";

        updateButtons();
        updateModeLock();
    }


    function startCountdown() {

        if (
            recordState !==
            RecordState.IDLE
        ) {
            return;
        }

        recordState =
            RecordState.COUNTDOWN;

        countdownStartedAt =
            performance.now();

        beginRecordingCalled =
            false;

        recordTitle.textContent =
            "Get Ready";

        recordDetail.textContent =
            "Recording will begin after the countdown.";

        countdown.textContent =
            "4";

        countdown.classList.remove(
            "hidden"
        );

        timer.classList.add(
            "hidden"
        );

        setStatus(
            "Recording starts soon..."
        );

        updateButtons();
        updateModeLock();

        scheduleUpdateLoop();
    }


    async function beginRecording() {

        if (
            beginRecordingCalled ||
            recordState !== RecordState.COUNTDOWN
        ) {
            return;
        }

        beginRecordingCalled =
            true;

        recordState =
            RecordState.STARTING;

        countdown.classList.add(
            "hidden"
        );

        recordTitle.textContent =
            "Starting Recording";

        recordDetail.textContent =
            "Connecting to the microphone...";

        updateButtons();

        try {

            setStatus(
                "Starting microphone..."
            );

            const result =
                await invoke(
                    "begin_recording"
                );

            recordState =
                RecordState.RECORDING;

            recordingStartedAt =
                performance.now();

            countdown.classList.add(
                "hidden"
            );

            timer.classList.remove(
                "hidden"
            );

            timer.textContent =
                "00:00";

            recordTitle.textContent =
                "Recording";

            recordDetail.textContent =
                "Play naturally. ChordSense is listening.";

            indicator.classList.add(
                "recording"
            );

            setStatus(
                result.message ??
                "Recording in progress..."
            );

            updateButtons();
            scheduleUpdateLoop();

        } catch (error) {

            console.error(error);

            setIdle();

            recordTitle.textContent =
                "Microphone Unavailable";

            recordDetail.textContent =
                "Check the recording service, then try again.";

            setStatus(
                `Could not start recording: ${error}`,
                true
            );
        }
    }


    async function stopRecording() {

        /*
         * Stop during countdown simply cancels it.
         */
        if (
            recordState ===
            RecordState.COUNTDOWN
        ) {

            stopAnimationLoop();

            setStatus(
                "Recording cancelled."
            );

            setIdle();

            return;
        }


        if (
            recordState !==
            RecordState.RECORDING
        ) {
            return;
        }


        recordState =
            RecordState.PROCESSING;

        stopAnimationLoop();

        indicator.classList.remove(
            "recording"
        );

        recordTitle.textContent =
            "Processing";

        recordDetail.textContent =
            "Detecting chords from your recording...";

        setStatus(
            "Stopping recording and analyzing..."
        );

        updateButtons();


        try {

            const result =
                await invoke(
                    "end_recording"
                );

            setStatus(
                `Analysis complete. ${
                    result.chords?.length ?? 0
                } chords found.`
            );

            setIdle();

            onAnalysisComplete?.(
                result
            );

        } catch (error) {

            console.error(error);

            setIdle();

            recordTitle.textContent =
                "Recording Failed";

            recordDetail.textContent =
                "ChordSense could not process the recording.";

            setStatus(
                `Recording failed: ${error}`,
                true
            );
        }
    }


    function stopAnimationLoop() {

        if (animationFrame === null) {
            return;
        }

        cancelAnimationFrame(
            animationFrame
        );

        animationFrame = null;
    }


    function scheduleUpdateLoop() {

        if (animationFrame !== null) {
            return;
        }

        animationFrame =
            requestAnimationFrame(
                updateLoop
            );
    }


    function updateLoop() {

        animationFrame = null;

        if (
            recordState ===
            RecordState.COUNTDOWN
        ) {

            const elapsed =
                (
                    performance.now()
                    -
                    countdownStartedAt
                ) / 1000;


            const remaining =
                Math.max(
                    1,
                    4 -
                    Math.floor(elapsed)
                );


            countdown.textContent =
                String(remaining);


            if (elapsed < 3) {

                recordDetail.textContent =
                    "Get ready...";

            } else if (elapsed < 4) {

                recordDetail.textContent =
                    "Start Playing!";

            } else {

                beginRecording();

                return;
            }
        }


        if (
            recordState ===
            RecordState.RECORDING
        ) {

            const elapsed =
                (
                    performance.now()
                    -
                    recordingStartedAt
                ) / 1000;

            timer.textContent =
                formatTime(elapsed);
        }


        if (
            recordState ===
                RecordState.COUNTDOWN ||
            recordState ===
                RecordState.RECORDING
        ) {

            scheduleUpdateLoop();
        }
    }


    startButton.addEventListener(
        "click",
        startCountdown
    );


    stopButton.addEventListener(
        "click",
        stopRecording
    );


    setIdle();
}
