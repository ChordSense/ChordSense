import test from "node:test";
import assert from "node:assert/strict";
import { createTrackRenamer } from "./rename-track.js";

// Minimal DOM fixture for dialog workflows; no browser or real backend is involved.
class Element extends EventTarget {
    children = [];
    attributes = {};
    value = "";
    maxLength = 80;
    selectionStart = 0;
    selectionEnd = 0;
    disabled = false;
    open = false;
    classes = new Set();
    classList = {
        contains: name => this.classes.has(name),
        add: name => this.classes.add(name),
        toggle: (name, force) => force ? this.classes.add(name) : this.classes.delete(name),
    };
    setAttribute(name, value) { this.attributes[name] = value; }
    append(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    focus() {}
    blur() {}
    select() { this.setSelectionRange(0, this.value.length); }
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
    showModal() { this.open = true; }
    close() { this.open = false; queueMicrotask(() => this.dispatchEvent(new Event("close"))); }
    click() { if (!this.disabled) this.dispatchEvent(new Event("click")); }
}

function fixture(invoke) {
    const ids = ["rename-track-dialog", "rename-track-form", "rename-track-input", "rename-track-error", "rename-keyboard", "rename-keyboard-toggle", "rename-track-cancel", "rename-track-save"];
    const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
    elements["rename-track-form"].querySelectorAll = () => Object.values(elements);
    globalThis.document = {
        querySelector: selector => elements[selector.slice(1)],
        createElement: () => new Element(),
    };
    const open = createTrackRenamer(invoke);
    return {
        open,
        get: id => elements[`rename-${id}`],
        submit: () => elements["rename-track-form"].dispatchEvent(new Event("submit", { cancelable: true })),
        key: text => elements["rename-keyboard"].children.flatMap(row => row.children).find(button => button.textContent === text),
    };
}

test("rename sends the original filename and returns the saved display title", async () => {
    const calls = [];
    const f = fixture(async (...args) => { calls.push(args); return "New title"; });
    const pending = f.open({kind:"songs", song:{name:"Old title", filename:"original.wav"}});
    f.get("track-input").value = "  New title  ";
    f.submit();
    assert.equal(await pending, "New title");
    assert.deepEqual(calls, [["rename_library_track", {kind:"songs", id:null, filename:"original.wav", title:"New title"}]]);
});

test("cancel preserves a recording title and performs no request", async () => {
    const f = fixture(() => assert.fail("Cancel must not save"));
    const pending = f.open({kind:"recordings", song:{id:7, name:"Recording #7"}});
    f.get("track-cancel").click();
    assert.equal(await pending, null);
});

test("a failed save stays open, shows an error, and allows a recording retry", async () => {
    let count = 0;
    const f = fixture(async (_, payload) => {
        assert.equal(payload.id, 7);
        if (++count === 1) throw new Error("disk full");
        return payload.title;
    });
    const pending = f.open({kind:"recordings", song:{id:7, name:"Take"}});
    f.submit();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.get("track-dialog").open, true);
    assert.match(f.get("track-error").textContent, /disk full/);
    assert.equal(f.get("track-save").disabled, false);
    f.get("track-input").value = "Retry";
    f.submit();
    assert.equal(await pending, "Retry");
});

test("keyboard replaces the selection, inserts at the cursor, and deletes a Unicode character", async () => {
    const f = fixture(async (_, payload) => payload.title);
    const pending = f.open({kind:"songs", song:{name:"Original", filename:"song.wav"}});
    f.get("keyboard-toggle").click();
    f.key("q").click();
    assert.equal(f.get("track-input").value, "q");
    f.key("Space").click();
    f.key("Shift").click();
    f.key("A").click();
    assert.equal(f.get("track-input").value, "q A");
    f.get("track-input").value = "Song 🎸";
    f.get("track-input").setSelectionRange(7, 7);
    f.key("⌫").click();
    assert.equal(f.get("track-input").value, "Song ");
    f.get("track-cancel").click();
    await pending;
});

test("saving blocks duplicate requests and Escape until the request completes", async () => {
    let finish;
    let calls = 0;
    const f = fixture(() => { calls++; return new Promise(resolve => { finish = resolve; }); });
    const pending = f.open({kind:"songs", song:{name:"Title", filename:"song.wav"}});
    f.submit(); f.submit();
    const escape = new Event("cancel", {cancelable:true});
    f.get("track-dialog").dispatchEvent(escape);
    assert.equal(escape.defaultPrevented, true);
    assert.equal(calls, 1);
    finish("Title");
    assert.equal(await pending, "Title");
});
