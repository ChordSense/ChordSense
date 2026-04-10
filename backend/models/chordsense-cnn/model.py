import torch
import torch.nn.functional as F
import torch.nn as nn

class ChordCNN(nn.Module):
    def __init__(self, num_classes: int = 25):
        super().__init__()
 
        # Conv backbone: extracts chord features from chroma spectrogram
        # Block 1: (B,1,12,15) → (B,32,12,15)  — learns interval patterns
        # Block 2: (B,32,12,15) → (B,64,6,7)   — learns full chord shapes

        self.features = nn.Sequential(
            # Block 1
            nn.Conv2d(1, 32, kernel_size=3, padding=1),     # 320 params
            nn.BatchNorm2d(32),                              # 64 params
            nn.ReLU(),
            nn.Dropout2d(0.15),
 
            # Block 2
            nn.Conv2d(32, 64, kernel_size=3, padding=1),    # 18,496 params
            nn.BatchNorm2d(64),                              # 128 params
            nn.ReLU(),
            nn.MaxPool2d(2, 2),                              # (12,15) → (6,7)
            nn.Dropout2d(0.15),
        )
 
        # Global average pool: (B,64,6,7) → (B,64)
        self.gap = nn.AdaptiveAvgPool2d(1)
 
        # Classifier head
        self.classifier = nn.Sequential(
            nn.Linear(64, 32),                               
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(32, num_classes),                   
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:   
        x = self.features(x)
        x = self.gap(x).flatten(1)
        x = self.classifier(x)
        return x
    
    @torch.no_grad()
    def predict(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        self.eval()
        logits = self.forward(x)
        probs = F.softmax(logits, dim=1)
        return probs.argmax(dim=1), probs

def build_model(num_classes: int = 25) -> ChordCNN:
    """Create model with Kaiming initialization."""
    model = ChordCNN(num_classes)
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            if m.bias is not None:
                nn.init.zeros_(m.bias)
        elif isinstance(m, nn.BatchNorm2d):
            nn.init.ones_(m.weight)
            nn.init.zeros_(m.bias)
        elif isinstance(m, nn.Linear):
            nn.init.kaiming_normal_(m.weight, nonlinearity='relu')
            nn.init.zeros_(m.bias)
    return model