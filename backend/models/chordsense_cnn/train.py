import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from models.chordsense_cnn.chord_datasets import build_datasets
from models.chordsense_cnn.model import build_model

def train(model, dataloader, criterion, optimizer, device):
    model.train() # Set model in training mode
    running_loss = 0.0
    correct = 0
    total = 0

    for chromas, labels in dataloader:
        chromas = chromas.to(device)
        labels = labels.to(device)
        
        # Clear gradients from previous batch
        optimizer.zero_grad(set_to_none=True)
        # Forward pass: get predictions
        outputs = model(chromas)
        # Compute loss
        loss = criterion(outputs, labels)
        # Backward pass: compute gradients
        loss.backward()
        # Update weights
        optimizer.step()
        # Track metrics
        running_loss += loss.item()
        _, predicted = outputs.max(1)  # get the class with highest score
        total += labels.size(0)
        correct += predicted.eq(labels).sum().item()

    avg_loss = running_loss / len(dataloader)
    accuracy = 100.0 * correct / total
    return avg_loss, accuracy

def test(model, dataloader, criterion, device):
    model.eval()  # set model to evaluation mode (disables dropout etc.)
    running_loss = 0.0
    correct = 0
    total = 0
    with torch.no_grad():  # disable gradient computation for efficiency
        for chromas, labels in dataloader:
            chromas = chromas.to(device)
            labels = labels.to(device)

            outputs = model(chromas)
            loss = criterion(outputs, labels)

            running_loss += loss.item()
            _, predicted = outputs.max(1)
            total += labels.size(0)
            correct += predicted.eq(labels).sum().item()

    avg_loss = running_loss / len(dataloader)
    accuracy = 100.0 * correct / total
    return avg_loss, accuracy

def run_training(train_loader, test_loader, num_epochs=50, patience=10, lr=1e-3):
    
    device = "cpu" # Default device value
    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"

    print(f"Using device: {device}")
 
    model = build_model(num_classes=25).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
 
    # Early stopping state
    best_val_loss = float('inf')
    best_epoch = 0
    epochs_without_improvement = 0
    best_state = None
 
    for epoch in range(1, num_epochs + 1):
        train_loss, train_acc = train(model, train_loader, criterion, optimizer, device)
        val_loss, val_acc = test(model, test_loader, criterion, device)
 
        print(f"Epoch {epoch:3d}/{num_epochs} | "
              f"Train Loss: {train_loss:.4f}  Acc: {train_acc:5.1f}% | "
              f"Val Loss: {val_loss:.4f}  Acc: {val_acc:5.1f}%", end="")
 
        # Check for improvement
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_epoch = epoch
            epochs_without_improvement = 0
            best_state = model.state_dict().copy()
            print("  ★ best", end="")
        else:
            epochs_without_improvement += 1
 
        print()
 
        if epochs_without_improvement >= patience:
            print(f"\nEarly stopping: no improvement for {patience} epochs.")
            break
 
    # Restore best weights
    if best_state is not None:
        model.load_state_dict(best_state)
    print(f"\nBest model from epoch {best_epoch} (val loss: {best_val_loss:.4f})")
 
    # Save checkpoint
    torch.save({
        'model_state_dict': best_state,
        'epoch': best_epoch,
        'val_loss': best_val_loss,
    }, './checkpoints/latest_chord_cnn.pth')
    print("Saved to checkpoints/latest_chord_cnn.pth")
 
    return model

if __name__ == "__main__":
    train_dataset, test_dataset, chord_classes = build_datasets()
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)
    model = run_training(train_loader, test_loader)