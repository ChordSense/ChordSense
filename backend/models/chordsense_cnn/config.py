# define the config for training and chord recognition

SAMPLE_RATE = 22050
HOP_LENGTH = 512
CONTEXT_FRAMES = 15
NUM_CLASSES = 25
BATCH_SIZE = 32
EPOCHS = 50
CHORD_CLASSES = ['A', 'A#', 'A#m', 'Am', 'B', 'Bm', 'C', 'C#', 'C#m', 'Cm', 'D', 'D#', 'D#m', 'Dm', 'E', 'Em', 'F', 'F#', 'F#m', 'Fm', 'G', 'G#', 'G#m', 'Gm', 'Noise']
VOTE_WINDOW = 9
POST_ONSESET_OFFSET = 2
POST_ONSESET_LENGTH = 5
RECORDING_OUTPUT_FILE = "temp.lab"