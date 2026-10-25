import {
    applyTunerEvent,
    clampA4,
    createTunerState,
    DEFAULT_A4_HZ,
    describeDisplay,
    needleAngle,
    stringLabels,
    TUNINGS,
    tunerDisplay,
    tuningById
} from "./tuner-model.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const SETTINGS_KEY = "chordsense.tuner";
// Meter geometry, shared with the SVG in index.html.
const PIVOT_X = 200;
const PIVOT_Y = 214;
const SCALE_RADIUS = 180;
const ANNOUNCE_INTERVAL_MS = 1000;

function loadSettings() {
    try {
        const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}");
        return {
            tuningId: tuningById(saved.tuningId).id,
            a4Hz: clampA4(saved.a4Hz ?? DEFAULT_A4_HZ)
        };
    } catch {
        return { tuningId: TUNINGS[0].id, a4Hz: DEFAULT_A4_HZ };
    }
}

function saveSettings(settings) {
    try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch {
        // Settings are a convenience; the tuner works without storage.
    }
}

function polar(angleDegrees, radius) {
    const radians = angleDegrees * Math.PI / 180;
    return [
        PIVOT_X + radius * Math.sin(radians),
        PIVOT_Y - radius * Math.cos(radians)
    ];
}

function arcPath(fromDegrees, toDegrees, radius) {
    const [x1, y1] = polar(fromDegrees, radius);
    const [x2, y2] = polar(toDegrees, radius);
    return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${radius} ${radius} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

function buildScale(group) {
    const svg = "http://www.w3.org/2000/svg";
    // The amber band marks ±10 cents; labelling it too crowds the centre.
    const labels = new Map([[-50, "−50"], [-20, "−20"], [0, "0"], [20, "+20"], [50, "+50"]]);
    for (const [className, cents] of [["close", 10], ["in-tune", 3]]) {
        const band = document.createElementNS(svg, "path");
        band.setAttribute("class", `tuner-band ${className}`);
        band.setAttribute("d", arcPath(needleAngle(-cents), needleAngle(cents), SCALE_RADIUS + 8));
        group.append(band);
    }
    for (let cents = -50; cents <= 50; cents += 5) {
        const major = cents % 10 === 0;
        const angle = needleAngle(cents);
        const [x1, y1] = polar(angle, SCALE_RADIUS);
        const [x2, y2] = polar(angle, SCALE_RADIUS - (cents === 0 ? 26 : major ? 16 : 9));
        const tick = document.createElementNS(svg, "line");
        tick.setAttribute("class", cents === 0 ? "tuner-tick centre" : major ? "tuner-tick major" : "tuner-tick");
        tick.setAttribute("x1", x1.toFixed(2));
        tick.setAttribute("y1", y1.toFixed(2));
        tick.setAttribute("x2", x2.toFixed(2));
        tick.setAttribute("y2", y2.toFixed(2));
        group.append(tick);
        if (labels.has(cents)) {
            const [x, y] = polar(angle, SCALE_RADIUS - 40);
            const label = document.createElementNS(svg, "text");
            label.setAttribute("class", "tuner-scale-label");
            label.setAttribute("x", x.toFixed(2));
            label.setAttribute("y", y.toFixed(2));
            label.textContent = labels.get(cents);
            group.append(label);
        }
    }
}

function setText(element, text) {
    if (element.textContent !== text) element.textContent = text;
}

function idleMessage(quality) {
    switch (quality) {
        case "warming_up": return "Listening…";
        case "clipping": return "Input too loud — turn down the guitar volume";
        case "stream_gap": return "Audio interrupted — listening again…";
        case "disconnected": return "Reconnecting to the guitar input…";
        default: return "Play a single string";
    }
}

export function initTunerMode() {
    const panel = document.querySelector("#tuner-panel");
    const needle = document.querySelector("#tuner-needle");
    const noteName = document.querySelector("#tuner-note-name");
    const noteOctave = document.querySelector("#tuner-note-octave");
    const flat = document.querySelector("#tuner-flat");
    const sharp = document.querySelector("#tuner-sharp");
    const detail = document.querySelector("#tuner-detail");
    const retry = document.querySelector("#tuner-retry");
    const stringsGroup = document.querySelector("#tuner-strings");
    const tuningName = document.querySelector("#tuner-tuning-name");
    const a4Value = document.querySelector("#tuner-a4-value");
    const announcer = document.querySelector("#tuner-status");

    buildScale(document.querySelector("#tuner-scale"));

    const settings = loadSettings();
    let targetIndex = null;
    let state = createTunerState();
    let active = false;
    let sessionId = null;
    let buffered = [];
    let failure = null;
    let queue = Promise.resolve();
    let animationFrame = null;
    let needleAngleNow = 0;
    let wasLocked = false;
    let lastAnnouncement = "";
    let lastAnnouncedAt = -Infinity;
    let stringButtons = [];

    const tuning = () => tuningById(settings.tuningId);
    const targetMidi = () => targetIndex === null ? null : tuning().strings[targetIndex];

    function enqueue(action) {
        const task = queue.then(action);
        queue = task.catch(() => {});
        return task;
    }

    function handleEvent(payload) {
        // Readings computed against the previous reference pitch would show
        // the wrong cents for a frame or two after an A4 change.
        if (payload.type === "pitch" && payload.a4_hz !== settings.a4Hz) return;
        if (payload.type === "status" && payload.status === "error") {
            failure = payload.error ?? "Guitar input unavailable";
        }
        applyTunerEvent(state, payload, performance.now(), targetMidi());
    }

    listen("tuner-event", ({ payload }) => {
        if (!active) return;
        // The bridge starts forwarding before start_tuner returns its ID.
        if (sessionId === null) {
            buffered.push(payload);
            return;
        }
        if (payload.session_id === sessionId) handleEvent(payload);
    });

    function renderStrings() {
        stringsGroup.replaceChildren();
        const auto = document.createElement("button");
        auto.type = "button";
        auto.className = "tuner-string auto";
        auto.textContent = "Auto";
        auto.setAttribute("aria-pressed", String(targetIndex === null));
        auto.addEventListener("click", () => selectString(null));
        stringsGroup.append(auto);
        const labels = stringLabels(tuning());
        stringButtons = tuning().strings.map((midi, index) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "tuner-string";
            button.textContent = labels[index];
            button.setAttribute("aria-label", `String ${6 - index}, ${labels[index]}`);
            button.setAttribute("aria-pressed", String(targetIndex === index));
            button.addEventListener("click", () => selectString(targetIndex === index ? null : index));
            stringsGroup.append(button);
            return button;
        });
        setText(tuningName, tuning().name);
        setText(a4Value, `${settings.a4Hz} Hz`);
    }

    function pushSettings(changes) {
        // Clear any lock so a reading measured against the old target or
        // reference cannot carry over as "in tune".
        state = createTunerState();
        if (!active) return;
        void enqueue(() => invoke("configure_tuner", { settings: changes })).catch(error => {
            console.error("Could not update the tuner:", error);
        });
    }

    function selectString(index) {
        targetIndex = index;
        renderStrings();
        pushSettings({ target_midi: targetMidi() });
    }

    function stepTuning(step) {
        const index = TUNINGS.findIndex(item => item.id === settings.tuningId);
        settings.tuningId = TUNINGS[(index + step + TUNINGS.length) % TUNINGS.length].id;
        saveSettings(settings);
        selectString(null);
    }

    function stepA4(step) {
        settings.a4Hz = clampA4(settings.a4Hz + step);
        saveSettings(settings);
        renderStrings();
        pushSettings({ a4_hz: settings.a4Hz });
    }

    document.querySelector("#tuner-tuning-prev").addEventListener("click", () => stepTuning(-1));
    document.querySelector("#tuner-tuning-next").addEventListener("click", () => stepTuning(1));
    document.querySelector("#tuner-a4-down").addEventListener("click", () => stepA4(-1));
    document.querySelector("#tuner-a4-up").addEventListener("click", () => stepA4(1));
    retry.addEventListener("click", () => {
        void leave().then(enter);
    });

    function render(now) {
        animationFrame = requestAnimationFrame(render);
        const display = tunerDisplay(state, now, tuning());
        const idle = display.mode === "idle";

        const target = idle ? 0 : needleAngle(display.cents);
        if (Math.abs(target - needleAngleNow) > 0.01) {
            needleAngleNow += (target - needleAngleNow) * 0.22;
            needle.setAttribute("transform", `rotate(${needleAngleNow.toFixed(2)} ${PIVOT_X} ${PIVOT_Y})`);
        }

        panel.dataset.mode = failure ? "error" : display.mode;
        panel.dataset.zone = idle ? "none" : display.zone;
        if (display.locked && !wasLocked) {
            panel.classList.remove("pulse");
            void panel.offsetWidth;
            panel.classList.add("pulse");
        }
        wasLocked = Boolean(display.locked);
        flat.classList.toggle("lit", display.direction === "up");
        sharp.classList.toggle("lit", display.direction === "down");
        retry.hidden = !failure;

        if (failure) {
            setText(noteName, "!");
            setText(noteOctave, "");
            setText(detail, `Tuner unavailable: ${failure}`);
        } else if (idle) {
            setText(noteName, "–");
            setText(noteOctave, "");
            setText(detail, idleMessage(display.quality));
        } else {
            setText(noteName, display.name);
            setText(noteOctave, String(display.octave));
            const cents = Math.round(display.cents);
            const offset = display.locked || display.zone === "in-tune"
                ? "In tune"
                : `${cents > 0 ? "+" : "−"}${Math.abs(cents)} cents · tune ${display.direction}`;
            setText(detail, `${offset} · ${display.hz.toFixed(1)} Hz`);
        }

        stringButtons.forEach((button, index) => {
            button.classList.toggle("near", targetIndex === null && display.stringIndex === index);
        });

        const announcement = failure ? detail.textContent : describeDisplay(display);
        if (announcement !== lastAnnouncement && now - lastAnnouncedAt >= ANNOUNCE_INTERVAL_MS) {
            lastAnnouncement = announcement;
            lastAnnouncedAt = now;
            setText(announcer, announcement);
        }
    }

    function enter() {
        if (active) return Promise.resolve();
        active = true;
        sessionId = null;
        buffered = [];
        failure = null;
        state = createTunerState();
        renderStrings();
        animationFrame ??= requestAnimationFrame(render);
        const settingsBody = { a4_hz: settings.a4Hz, target_midi: targetMidi() };
        return enqueue(async () => {
            if (!active) return;
            const started = await invoke("start_tuner", { settings: settingsBody });
            if (!active) return;
            sessionId = started.session_id;
            for (const payload of buffered) {
                if (payload.session_id === sessionId) handleEvent(payload);
            }
            buffered = [];
        }).catch(error => {
            console.error("Could not start the tuner:", error);
            failure = String(error);
        });
    }

    function leave() {
        const wasActive = active;
        active = false;
        sessionId = null;
        buffered = [];
        if (animationFrame !== null) {
            cancelAnimationFrame(animationFrame);
            animationFrame = null;
        }
        if (!wasActive) return queue;
        // Release the stream before Record asks iod for an exclusive capture.
        return enqueue(() => invoke("stop_tuner")).catch(error => {
            console.error("Could not stop the tuner:", error);
        });
    }

    renderStrings();
    return { enter, leave };
}
