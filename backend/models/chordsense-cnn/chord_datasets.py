from torch.utils.data import Dataset
import torch
import numpy as np
from audio_processing import slice_into_windows, extract_chroma_cqt
import os
from collections import defaultdict
import random
from datasets import load_dataset


GC_ROOT = "./Datasets/GuitarChordsV3"
HF_DATASET_NAME = "rodriler/isolated-guitar-chords"
SEED = 42


def get_guitarchords_files(root_folder):
    samples = []
    for folder in os.listdir(root_folder):
        folder_path = os.path.join(root_folder, folder)
        if os.path.isdir(folder_path):
            for file in os.listdir(folder_path):
                if file.endswith('.wav'):
                    file_path = os.path.join(folder_path, file)
                    label = folder
                    if label == 'Bb':
                        label = 'A#'
                    samples.append((file_path, label))
    return samples


class ChordDataset(Dataset):
    def __init__(self, samples, noise_label, rms_threshold=0.01):
        all_windows = []
        all_labels = []

        for chroma, waveform, label in samples:
            windows = slice_into_windows(chroma)

            hop_length = 512
            context_frames = 15
            context_samples = context_frames * hop_length

            for i, window in enumerate(windows):
                start = i * hop_length
                end = start + context_samples
                segment = waveform[start:end]

                rms = np.sqrt(np.mean(segment ** 2)) if len(segment) > 0 else 0.0
                if rms < rms_threshold:
                    all_labels.append(noise_label)
                else:
                    all_labels.append(label)
                all_windows.append(window)

        all_windows = np.array(all_windows)
        self.features = torch.tensor(all_windows, dtype=torch.float32).unsqueeze(1)
        self.labels = torch.LongTensor(all_labels)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return self.features[idx], self.labels[idx]


def _load_hf_split(ds, split):
    samples = []
    for sample in ds[split]:
        chroma, waveform = extract_chroma_cqt(sample)
        samples.append((chroma, waveform, sample["label"]))
    return samples


def _load_and_split_guitarchords(label_to_idx, train_ratio=0.9, seed=SEED):
    """Decode GuitarChordsV3 once and split per-class so every chord appears in both sets."""
    rng = random.Random(seed)

    gc_samples = []
    for filepath, label in get_guitarchords_files(GC_ROOT):
        if label not in label_to_idx:
            print("Label not in class list:", label)
            continue
        chroma, waveform = extract_chroma_cqt(filepath)
        gc_samples.append((chroma, waveform, label_to_idx[label]))

    by_class = defaultdict(list)
    for s in gc_samples:
        by_class[s[2]].append(s)

    gc_train, gc_test = [], []
    for items in by_class.values():
        rng.shuffle(items)
        split = int(train_ratio * len(items))
        gc_train.extend(items[:split])
        gc_test.extend(items[split:])

    print(f"GuitarChordsV3 split: {len(gc_train)} train, {len(gc_test)} test")
    return gc_train, gc_test


def build_datasets():
    """Build train and test ChordDatasets from HF + GuitarChordsV3.

    Returns:
        train_dataset, test_dataset, chord_classes
    """
    ds = load_dataset(HF_DATASET_NAME)
    chord_classes = ds["train"].features["label"].names
    label_to_idx = {name: i for i, name in enumerate(chord_classes)}
    noise_label = label_to_idx["Noise"]

    hf_train = _load_hf_split(ds, "train")
    hf_test = _load_hf_split(ds, "test")
    gc_train, gc_test = _load_and_split_guitarchords(label_to_idx)

    train_dataset = ChordDataset(hf_train + gc_train, noise_label=noise_label)
    test_dataset = ChordDataset(hf_test + gc_test, noise_label=noise_label)

    return train_dataset, test_dataset, chord_classes