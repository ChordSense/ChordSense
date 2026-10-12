import test from "node:test";
import assert from "node:assert/strict";
import { LocalPlayback } from "./local-playback.js";

class FakeAudio extends EventTarget {
    constructor() {
        super();
        this.src = "";
        this.duration = 34.9;
        this.currentTime = 0;
        this.playbackRate = 1;
        this.volume = 1;
        this.paused = true;
        this.ended = false;
        this.failNextLoad = false;
    }

    load() {
        if (!this.src) return;
        const type = this.failNextLoad ? "error" : "loadedmetadata";
        this.failNextLoad = false;
        queueMicrotask(() => this.dispatchEvent(new Event(type)));
    }

    async play() {
        this.paused = false;
        this.ended = false;
    }

    pause() {
        this.paused = true;
    }

    removeAttribute(name) {
        if (name === "src") this.src = "";
    }
}

function fixture(audio = new FakeAudio()) {
    const active = new Map();
    const revoked = [];
    let sequence = 0;
    const objectUrls = {
        createObjectURL(blob) {
            const url = `blob:test-${++sequence}`;
            active.set(url, blob);
            return url;
        },
        revokeObjectURL(url) {
            active.delete(url);
            revoked.push(url);
        },
    };
    const playback = new LocalPlayback(
        async () => [1, 2, 3], audio, objectUrls
    );
    return { playback, audio, active, revoked };
}

test("local audio follows the song clock through play, seek, speed, and completion", async () => {
    const { playback, audio, active } = fixture();
    const loaded = await playback.command("load", { path: "/songs/Perfect.mp3" });
    assert.equal(loaded.duration, 34.9);
    assert.equal(loaded.position, 0);
    assert.equal(loaded.playing, false);
    assert.equal(active.get(audio.src).type, "audio/mpeg");
    assert.equal(audio.preservesPitch, true);

    assert.equal((await playback.command("play")).playing, true);
    assert.equal((await playback.command("seek", { position_secs: 12.5 })).position, 12.5);
    assert.equal((await playback.command("speed", { speed: 0.75 })).speed, 0.75);
    await playback.command("volume", { volume: 0.4 });
    assert.equal(audio.volume, 0.4);
    assert.equal((await playback.command("pause")).paused, true);

    audio.ended = true;
    assert.equal(playback.status().finished, true);
    assert.equal((await playback.command("stop")).position, 0);
    assert.equal(playback.status().playing, false);
});

test("replacing or rejecting a song releases its blob URL", async () => {
    const { playback, audio, active, revoked } = fixture();
    await playback.load({ upload: "first.wav" });
    const firstUrl = audio.src;
    await playback.load({ upload: "second.wav" });
    assert.deepEqual(revoked, [firstUrl]);
    assert.equal(active.size, 1);

    audio.failNextLoad = true;
    await assert.rejects(playback.load({ upload: "broken.wav" }), /could not be played/);
    assert.equal(active.size, 0);
    assert.equal(audio.src, "");
});
