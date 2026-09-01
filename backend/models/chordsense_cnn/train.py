import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from models.chordsense_cnn.audio_processing import (
    DEFAULT_PREPROCESSING_CONFIG,
    PreprocessingConfig,
)
from models.chordsense_cnn.chord_datasets import build_datasets
from models.chordsense_cnn.config import BATCH_SIZE, EPOCHS, NUM_CLASSES
from models.chordsense_cnn.model import build_model


def train(model, dataloader, criterion, optimizer, device):
    model.train()
    running_loss = 0.0
    correct = 0
    total = 0

    for chromas, labels in dataloader:
        chromas = chromas.to(device)
        labels = labels.to(device)
        optimizer.zero_grad(set_to_none=True)
        outputs = model(chromas)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        running_loss += loss.item()
        predictions = outputs.argmax(dim=1)
        total += labels.size(0)
        correct += predictions.eq(labels).sum().item()

    return running_loss / len(dataloader), 100.0 * correct / total


def test(model, dataloader, criterion, device):
    model.eval()
    running_loss = 0.0
    correct = 0
    total = 0

    with torch.no_grad():
        for chromas, labels in dataloader:
            chromas = chromas.to(device)
            labels = labels.to(device)
            outputs = model(chromas)
            running_loss += criterion(outputs, labels).item()
            predictions = outputs.argmax(dim=1)
            total += labels.size(0)
            correct += predictions.eq(labels).sum().item()

    return running_loss / len(dataloader), 100.0 * correct / total


def select_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def run_training(
    train_loader,
    test_loader,
    num_epochs=EPOCHS,
    patience=10,
    lr=1e-3,
    preprocessing: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
):
    device = select_device()
    print(f"Using device: {device}")

    model = build_model(num_classes=NUM_CLASSES).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    best_validation_loss = float("inf")
    best_epoch = 0
    epochs_without_improvement = 0
    best_state = None

    for epoch in range(1, num_epochs + 1):
        train_loss, train_accuracy = train(
            model,
            train_loader,
            criterion,
            optimizer,
            device,
        )
        validation_loss, validation_accuracy = test(
            model,
            test_loader,
            criterion,
            device,
        )
        print(
            f"Epoch {epoch:3d}/{num_epochs} | "
            f"Train Loss: {train_loss:.4f} Acc: {train_accuracy:5.1f}% | "
            f"Validation Loss: {validation_loss:.4f} "
            f"Acc: {validation_accuracy:5.1f}%",
            end="",
        )

        if validation_loss < best_validation_loss:
            best_validation_loss = validation_loss
            best_epoch = epoch
            epochs_without_improvement = 0
            best_state = deepcopy(model.state_dict())
            print("  ★ best", end="")
        else:
            epochs_without_improvement += 1
        print()

        if epochs_without_improvement >= patience:
            print(f"Early stopping after {patience} epochs without improvement.")
            break

    if best_state is None:
        raise RuntimeError("Training did not produce a checkpoint")
    model.load_state_dict(best_state)
    print(
        f"Best model from epoch {best_epoch} "
        f"(validation loss: {best_validation_loss:.4f})"
    )

    checkpoint_path = Path(__file__).resolve().parent / "checkpoints" / "latest_chord_cnn.pth"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "epoch": best_epoch,
            "val_loss": best_validation_loss,
            "preprocessing": asdict(preprocessing),
        },
        checkpoint_path,
    )
    print(f"Saved to {checkpoint_path}")
    return model


if __name__ == "__main__":
    preprocessing = DEFAULT_PREPROCESSING_CONFIG
    train_dataset, test_dataset, _ = build_datasets(preprocessing)
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False)
    run_training(train_loader, test_loader, preprocessing=preprocessing)
