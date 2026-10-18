import test from "node:test";
import assert from "node:assert/strict";
import { createLibraryMotion } from "./library-motion.js";

function fixture() {
    const preference = new EventTarget();
    preference.matches = false;
    const calls = [];
    const element = { animate: (frames, options) => {
        const animation = {
            cancelled: false,
            cancel() { this.cancelled = true; this.oncancel?.(); },
        };
        calls.push({frames, options, animation});
        return animation;
    }};
    return {preference, calls, element, motion:createLibraryMotion(preference)};
}

test("Library effects run once and use opacity and transform only", () => {
    const f = fixture();
    f.motion.open(f.element);
    assert.equal(f.calls[0].options.duration, 180);
    assert.equal(f.calls[0].options.iterations, 1);
    assert.deepEqual(Object.keys(f.calls[0].frames[0]).sort(), ["opacity", "transform"]);
    f.motion.tab(f.element, true);
    assert.equal(f.calls[0].animation.cancelled, true);
    assert.equal(f.calls[1].options.duration, 140);
    assert.equal(f.calls[1].frames[0].transform, "translateX(10px)");
});

test("reduced motion skips effects and cancels an active effect when enabled", () => {
    const f = fixture();
    f.motion.open(f.element);
    f.preference.matches = true;
    f.preference.dispatchEvent(new Event("change"));
    assert.equal(f.calls[0].animation.cancelled, true);
    f.motion.tab(f.element, false);
    assert.equal(f.calls.length, 1);
});

test("closing cancels all effects and finished effects are released", () => {
    const f = fixture();
    f.motion.open(f.element);
    const second = {animate:f.element.animate};
    f.motion.tab(second, false);
    f.calls[0].animation.onfinish();
    f.motion.cancelAll();
    assert.equal(f.calls[0].animation.cancelled, false);
    assert.equal(f.calls[1].animation.cancelled, true);
});

test("rapid tab changes cannot let an older completion clear the newer effect", () => {
    const f = fixture();
    f.motion.tab(f.element, true);
    f.motion.tab(f.element, false);
    f.calls[0].animation.onfinish();
    f.motion.cancelAll();
    assert.equal(f.calls[1].animation.cancelled, true);
});
