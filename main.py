import tkinter as tk
from tkinter import filedialog, messagebox
import numpy as np
import librosa
import pyaudio
import wave
import threading
import queue
from collections import deque, Counter

# --- CONFIGURATION ---
CHUNK_SIZE = 4096
# Analyze half a second of audio at a time
BUFFER_SECONDS = 0.5 
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'] 

def normalize(v):
    norm = np.linalg.norm(v)
    return v if norm == 0 else v / norm

def build_chord_templates():
    templates = {}
    for root in range(12):
        major = np.zeros(12)
        minor = np.zeros(12)

        # Weighted templates to account for harmonics (Root is strongest)
        major[root] = 1.0
        major[(root + 4) % 12] = 0.6  # Major 3rd
        major[(root + 7) % 12] = 0.8  # Perfect 5th

        minor[root] = 1.0
        minor[(root + 3) % 12] = 0.6  # Minor 3rd
        minor[(root + 7) % 12] = 0.8  # Perfect 5th

        templates[NOTE_NAMES[root]] = major
        templates[NOTE_NAMES[root] + "m"] = minor

    return templates

def detect_chord_from_chroma(mean_chroma, templates):
    mean_chroma = normalize(mean_chroma)
    best_chord = "---"
    best_score = -1

    for chord_name, template in templates.items():
        score = np.dot(mean_chroma, normalize(template))
        if score > best_score:
            best_score = score
            best_chord = chord_name

    return best_chord, best_score

def format_time(seconds):
    seconds = max(0, int(seconds))
    mins = seconds // 60
    secs = seconds % 60
    return f"{mins}:{secs:02d}"

class RealTimeChordApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Real-Time Chord Sense Pro")
        self.root.geometry("520x420")

        # UI: Top Controls
        self.top_frame = tk.Frame(root)
        self.top_frame.pack(pady=10)
        
        self.btn_load = tk.Button(self.top_frame, text="Load WAV File", command=self.load_file, font=("Arial", 12))
        self.btn_load.pack()

        self.file_label = tk.Label(root, text="No file loaded", font=("Arial", 10, "italic"), fg="gray")
        self.file_label.pack(pady=5)

        # UI: Chord Display
        self.label_title = tk.Label(root, text="Detected Chord:", font=("Arial", 14))
        self.label_title.pack(pady=5)

        self.chord_var = tk.StringVar(value="---")
        self.label_chord = tk.Label(root, textvariable=self.chord_var, font=("Arial", 60, "bold"), fg="blue")
        self.label_chord.pack(pady=5)

        # UI: Time & Slider
        self.time_var = tk.StringVar(value="0:00 / 0:00")
        self.label_time = tk.Label(root, textvariable=self.time_var, font=("Arial", 12))
        self.label_time.pack(pady=5)

        self.slider = tk.Scale(
            root, from_=0, to=100, orient="horizontal", length=420, 
            resolution=0.01, showvalue=0, state="disabled"
        )
        self.slider.pack(pady=10)
        self.slider.bind("<Button-1>", self.on_slider_press)
        self.slider.bind("<ButtonRelease-1>", self.on_slider_release)

        # UI: Play/Stop
        self.btn_frame = tk.Frame(root)
        self.btn_frame.pack(pady=10)

        self.btn_play = tk.Button(self.btn_frame, text="Start", command=self.start_playback, font=("Arial", 12), bg="green", fg="white", width=10, state="disabled")
        self.btn_play.grid(row=0, column=0, padx=5)

        self.btn_stop = tk.Button(self.btn_frame, text="Stop", command=self.stop_playback, font=("Arial", 12), bg="red", fg="white", width=10, state="disabled")
        self.btn_stop.grid(row=0, column=1, padx=5)

        # State Variables
        self.audio_file = None
        self.is_playing = False
        self.is_dragging_slider = False
        self.seek_request_seconds = None
        self.lock = threading.Lock()
        
        # Audio & Analysis processing
        self.chord_templates = build_chord_templates()
        self.recent_chords = deque(maxlen=8)
        self.analysis_queue = queue.Queue(maxsize=3) 
        self.rolling_buffer = None

    def load_file(self):
        filepath = filedialog.askopenfilename(filetypes=[("WAV Files", "*.wav")])
        if not filepath:
            return

        try:
            with wave.open(filepath, 'rb') as wf:
                self.total_frames = wf.getnframes()
                self.sample_rate = wf.getframerate()
                self.channels = wf.getnchannels()
            
            self.audio_file = filepath
            self.total_duration = self.total_frames / self.sample_rate
            
            buffer_size = int(self.sample_rate * BUFFER_SECONDS)
            self.rolling_buffer = np.zeros(buffer_size, dtype=np.float32)

            # Update UI
            self.file_label.config(text=filepath.split("/")[-1])
            self.slider.config(to=self.total_duration, state="normal")
            self.time_var.set(f"0:00 / {format_time(self.total_duration)}")
            self.btn_play.config(state="normal")
            self.btn_stop.config(state="normal")
            
        except Exception as e:
            messagebox.showerror("Error", f"Could not load file:\n{e}")

    def on_slider_press(self, event):
        self.is_dragging_slider = True

    def on_slider_release(self, event):
        new_time = self.slider.get()
        with self.lock:
            self.seek_request_seconds = new_time
            # Clear queue and buffer to instantly analyze new position
            while not self.analysis_queue.empty():
                try: self.analysis_queue.get_nowait()
                except queue.Empty: break
            if self.rolling_buffer is not None:
                self.rolling_buffer.fill(0)
        self.is_dragging_slider = False

    def start_playback(self):
        if not self.audio_file:
            return
        if not self.is_playing:
            self.is_playing = True
            
            self.play_thread = threading.Thread(target=self.audio_playback_worker, daemon=True)
            self.analyze_thread = threading.Thread(target=self.chord_analysis_worker, daemon=True)
            
            self.play_thread.start()
            self.analyze_thread.start()

    def stop_playback(self):
        self.is_playing = False

    def audio_playback_worker(self):
        wf = wave.open(self.audio_file, 'rb')
        p = pyaudio.PyAudio()

        stream = p.open(
            format=p.get_format_from_width(wf.getsampwidth()),
            channels=wf.getnchannels(),
            rate=wf.getframerate(),
            output=True
        )

        sr = wf.getframerate()

        try:
            while self.is_playing:
                with self.lock:
                    seek_time = self.seek_request_seconds
                    self.seek_request_seconds = None

                # Handle Seeking
                if seek_time is not None:
                    target_frame = int(seek_time * sr)
                    target_frame = max(0, min(target_frame, self.total_frames - 1))
                    wf.setpos(target_frame)
                    self.recent_chords.clear()
                    self.root.after(0, self.chord_var.set, "---")

                data = wf.readframes(CHUNK_SIZE)
                if not data:
                    break 

                stream.write(data)

                try:
                    self.analysis_queue.put_nowait(data)
                except queue.Full:
                    pass 

                current_time = wf.tell() / sr
                if not self.is_dragging_slider:
                    self.root.after(0, self.slider.set, current_time)
                self.root.after(0, self.time_var.set, f"{format_time(current_time)} / {format_time(self.total_duration)}")

        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
            wf.close()
            self.is_playing = False
            self.root.after(0, self.chord_var.set, "Stopped")

    def chord_analysis_worker(self):
        while self.is_playing:
            try:
                data = self.analysis_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # 1. Process raw audio bytes
            audio_chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

            if self.channels > 1:
                frames = len(audio_chunk) // self.channels
                audio_chunk = audio_chunk[:frames * self.channels].reshape(-1, self.channels).mean(axis=1)

            # 2. Update Rolling Buffer
            chunk_len = len(audio_chunk)
            if chunk_len < len(self.rolling_buffer):
                self.rolling_buffer = np.roll(self.rolling_buffer, -chunk_len)
                self.rolling_buffer[-chunk_len:] = audio_chunk

            # 3. Noise Gate: Skip silence
            rms = librosa.feature.rms(y=self.rolling_buffer)[0]
            if np.mean(rms) < 0.005:
                self.root.after(0, self.chord_var.set, "---")
                continue

            # 4. Harmonic Separation
            y_harmonic = librosa.effects.harmonic(self.rolling_buffer, margin=1.0)

            # 5. CENS Chroma - (I think its better for chords than STFT MAYBE)
            chroma = librosa.feature.chroma_cens(
                y=y_harmonic,
                sr=self.sample_rate,
                hop_length=512
            )

            if chroma.size > 0:
                mean_chroma = np.mean(chroma, axis=1)
                detected_chord, score = detect_chord_from_chroma(mean_chroma, self.chord_templates)

                # 6. Require higher confidence, smooth over longer period
                if score > 0.55:
                    self.recent_chords.append(detected_chord)
                    # Only change display if the most common chord appears frequently enough
                    counts = Counter(self.recent_chords)
                    most_common, count = counts.most_common(1)[0]
                    
                    if count >= 4:
                        self.root.after(0, self.chord_var.set, most_common)

    def on_close(self):
        self.is_playing = False
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = RealTimeChordApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()