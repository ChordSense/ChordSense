from .audio_processing import DEFAULT_PREPROCESSING_CONFIG

SAMPLE_RATE = DEFAULT_PREPROCESSING_CONFIG.sample_rate
HOP_LENGTH = DEFAULT_PREPROCESSING_CONFIG.hop_length
CONTEXT_FRAMES = DEFAULT_PREPROCESSING_CONFIG.context_frames
NUM_CLASSES = 25
BATCH_SIZE = 32
EPOCHS = 50
CHORD_CLASSES = [
    "A",
    "A#",
    "A#m",
    "Am",
    "B",
    "Bm",
    "C",
    "C#",
    "C#m",
    "Cm",
    "D",
    "D#",
    "D#m",
    "Dm",
    "E",
    "Em",
    "F",
    "F#",
    "F#m",
    "Fm",
    "G",
    "G#",
    "G#m",
    "Gm",
    "Noise",
]
VOTE_WINDOW = 9
POST_ONSET_OFFSET = 2
POST_ONSET_LENGTH = 5
RECORDING_OUTPUT_FILE = "temp.lab"
