import torch
import torch.nn.functional as F
import torch.nn as nn
from .config import *


MODEL_NAMES = ("baseline", "pitch_aware", "depthwise_pitch")


def _circular_pitch_pad(values: torch.Tensor, amount: int = 1) -> torch.Tensor:
    """Wrap chroma pitch rows so convolution treats B and C as adjacent."""
    return F.pad(values, (0, 0, amount, amount), mode="circular")


class ChordCNN(nn.Module):
    def __init__(self, num_classes: int = NUM_CLASSES):
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


class PitchAwareChordCNN(nn.Module):
    """Small CNN that keeps absolute pitch rows through the classifier."""

    def __init__(self, num_classes: int = NUM_CLASSES):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 16, kernel_size=3, padding=(0, 1))
        self.bn1 = nn.BatchNorm2d(16)
        self.conv2 = nn.Conv2d(16, 24, kernel_size=3, padding=(0, 1))
        self.bn2 = nn.BatchNorm2d(24)
        self.dropout = nn.Dropout2d(0.15)
        self.classifier = nn.Sequential(
            nn.Linear(24 * 12, 48),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(48, num_classes),
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        values = self.dropout(
            F.relu(self.bn1(self.conv1(_circular_pitch_pad(values))))
        )
        values = self.dropout(
            F.relu(self.bn2(self.conv2(_circular_pitch_pad(values))))
        )
        if values.shape[-1] >= 2:
            values = F.max_pool2d(values, kernel_size=(1, 2), stride=(1, 2))
        values = values.mean(dim=3).flatten(1)
        return self.classifier(values)


class DepthwisePitchChordCNN(nn.Module):
    """Depthwise-separable pitch-aware candidate for edge inference."""

    def __init__(self, num_classes: int = NUM_CLASSES):
        super().__init__()
        self.stem = nn.Conv2d(1, 16, kernel_size=3, padding=(0, 1))
        self.stem_bn = nn.BatchNorm2d(16)
        self.depthwise = nn.Conv2d(
            16,
            16,
            kernel_size=3,
            padding=(0, 1),
            groups=16,
        )
        self.pointwise = nn.Conv2d(16, 24, kernel_size=1)
        self.bn = nn.BatchNorm2d(24)
        self.dropout = nn.Dropout2d(0.15)
        self.classifier = nn.Sequential(
            nn.Linear(24 * 12, 32),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(32, num_classes),
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        values = self.dropout(
            F.relu(self.stem_bn(self.stem(_circular_pitch_pad(values))))
        )
        values = self.dropout(
            F.relu(self.bn(self.pointwise(self.depthwise(_circular_pitch_pad(values)))))
        )
        if values.shape[-1] >= 2:
            values = F.max_pool2d(values, kernel_size=(1, 2), stride=(1, 2))
        values = values.mean(dim=3).flatten(1)
        return self.classifier(values)


def build_model(
    num_classes: int = NUM_CLASSES,
    model_name: str = "baseline",
) -> nn.Module:
    """Create model with Kaiming initialization."""
    aliases = {
        "ChordCNN": "baseline",
        "PitchAwareChordCNN": "pitch_aware",
        "DepthwisePitchChordCNN": "depthwise_pitch",
    }
    model_name = aliases.get(model_name, model_name)
    builders = {
        "baseline": ChordCNN,
        "pitch_aware": PitchAwareChordCNN,
        "depthwise_pitch": DepthwisePitchChordCNN,
    }
    if model_name not in builders:
        raise ValueError(
            f"Unknown model_name {model_name!r}; expected one of {MODEL_NAMES}"
        )
    model = builders[model_name](num_classes)
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
