// macOS preview playback. The Pi keeps using iod and its I2S output.
const AUDIO_TYPES = {
    wav: "audio/wav",
    mp3: "audio/mpeg",
    ogg: "audio/ogg",
    flac: "audio/flac",
    m4a: "audio/mp4",
};

export class LocalPlayback {
    constructor(loadBytes, audio = new Audio(), objectUrls = URL) {
        this.loadBytes = loadBytes;
        this.audio = audio;
        this.objectUrls = objectUrls;
        this.objectUrl = null;
    }

    async load(source) {
        this.release();
        const bytes = await this.loadBytes(source);
        const name = source.path ?? source.upload ?? "";
        const extension = name.split(".").pop()?.toLowerCase();
        const blob = new Blob(
            [new Uint8Array(bytes)],
            { type: AUDIO_TYPES[extension] ?? "application/octet-stream" }
        );
        this.objectUrl = this.objectUrls.createObjectURL(blob);
        this.audio.src = this.objectUrl;
        this.audio.playbackRate = 1;
        this.audio.preservesPitch = true;
        if ("webkitPreservesPitch" in this.audio) {
            this.audio.webkitPreservesPitch = true;
        }

        try {
            await new Promise((resolve, reject) => {
                const cleanup = () => {
                    this.audio.removeEventListener("loadedmetadata", loaded);
                    this.audio.removeEventListener("error", failed);
                };
                const loaded = () => {
                    cleanup();
                    resolve();
                };
                const failed = () => {
                    cleanup();
                    reject(new Error("This audio file could not be played on this Mac."));
                };
                this.audio.addEventListener("loadedmetadata", loaded);
                this.audio.addEventListener("error", failed);
                this.audio.load();
            });
        } catch (error) {
            this.release();
            throw error;
        }
        return this.status();
    }

    async command(action, body = {}) {
        switch (action) {
            case "load":
                return this.load(body);
            case "play":
                if (this.audio.ended) this.audio.currentTime = 0;
                await this.audio.play();
                break;
            case "pause":
                this.audio.pause();
                break;
            case "stop":
                this.audio.pause();
                this.audio.currentTime = 0;
                break;
            case "seek":
                if (!Number.isFinite(Number(body.position_secs))) {
                    throw new Error("Seek position must be finite.");
                }
                this.audio.currentTime = Math.max(
                    0,
                    Math.min(Number(body.position_secs), this.audio.duration)
                );
                break;
            case "speed":
                if (!Number.isFinite(Number(body.speed)) ||
                    Number(body.speed) < 0.5 || Number(body.speed) > 1) {
                    throw new Error("Playback speed must be from 0.5× to 1×.");
                }
                this.audio.playbackRate = Number(body.speed);
                break;
            case "volume":
                if (!Number.isFinite(Number(body.volume)) ||
                    Number(body.volume) < 0 || Number(body.volume) > 1) {
                    throw new Error("Volume must be from 0 to 1.");
                }
                this.audio.volume = Number(body.volume);
                break;
            default:
                throw new Error(`Unknown playback action: ${action}`);
        }
        return this.status();
    }

    status() {
        return {
            playing: !this.audio.paused && !this.audio.ended,
            paused: this.audio.paused,
            finished: this.audio.ended,
            position: this.audio.currentTime,
            duration: this.audio.duration,
            speed: this.audio.playbackRate,
        };
    }

    release() {
        this.audio.pause();
        if (this.objectUrl) {
            this.audio.removeAttribute("src");
            this.audio.load();
            this.objectUrls.revokeObjectURL(this.objectUrl);
        }
        this.objectUrl = null;
    }
}
