# --- Audio ---
SAMPLE_RATE = 22050
CHUNK_SIZE = 4096
 
# --- CQT / Chroma feature extraction ---
N_CHROMA = 12            # 12 pitch classes (chroma CQT)
N_BINS_FULL = 84         # 7 octaves × 12 bins/octave (Phase 3 upgrade)
BINS_PER_OCTAVE = 12
HOP_LENGTH = 512         # ~23ms per frame at 22050 Hz
FMIN = 32.7              # C1 — lowest guitar note (drop tuning)
USE_CHROMA = True        # True = chroma CQT (12 bins), False = full CQT (84)
CONTEXT_FRAMES = 15
