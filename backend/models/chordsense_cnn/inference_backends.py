"""Inference runtimes for the streaming ChordSense classifier."""

from __future__ import annotations

import importlib
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float32]
ModelInputShape = tuple[int, int, int, int]


@runtime_checkable
class InferenceBackend(Protocol):
    """Minimal contract required by the streaming recognizer."""

    @property
    def name(self) -> str:
        """Human-readable runtime name for diagnostics and benchmarks."""
        ...

    @property
    def input_shape(self) -> ModelInputShape:
        """Required model input shape in NCHW order."""
        ...

    @property
    def class_count(self) -> int:
        """Number of logits returned for each input window."""
        ...

    def infer(self, features: FloatArray) -> FloatArray:
        """Return one logit vector per NCHW feature window."""
        ...

    def close(self) -> None:
        """Release runtime and accelerator resources."""
        ...


def _validate_features(
    features: npt.ArrayLike,
    expected_shape: ModelInputShape,
) -> FloatArray:
    values = np.asarray(features, dtype=np.float32)
    if values.shape != expected_shape:
        raise ValueError(
            f"Expected inference input shape {expected_shape}, got {values.shape}"
        )
    if not np.isfinite(values).all():
        raise ValueError("Inference input contains non-finite values")
    return np.ascontiguousarray(values)


def _validate_logits(
    logits: npt.ArrayLike,
    batch_size: int,
    class_count: int,
) -> FloatArray:
    values = np.asarray(logits, dtype=np.float32)
    if values.size != batch_size * class_count:
        raise ValueError(
            "Inference output must contain exactly "
            f"{class_count} logits per item; got shape {values.shape}"
        )
    values = values.reshape(batch_size, class_count)
    if not np.isfinite(values).all():
        raise ValueError("Inference output contains non-finite values")
    return np.ascontiguousarray(values)


class TorchInferenceBackend:
    """Run the classifier with PyTorch while satisfying InferenceBackend."""

    def __init__(
        self,
        model: object,
        input_shape: ModelInputShape,
        class_count: int,
        device: str = "cpu",
    ):
        if class_count <= 0:
            raise ValueError("class_count must be positive")
        torch = importlib.import_module("torch")
        self._torch = torch
        self._model = model.to(device).eval()
        self._input_shape = input_shape
        self._class_count = class_count
        self.device = device
        self._closed = False

    @property
    def name(self) -> str:
        return "torch"

    @property
    def input_shape(self) -> ModelInputShape:
        return self._input_shape

    @property
    def class_count(self) -> int:
        return self._class_count

    def infer(self, features: FloatArray) -> FloatArray:
        if self._closed:
            raise RuntimeError("Torch inference backend is closed")
        values = _validate_features(features, self.input_shape)
        tensor = self._torch.from_numpy(values).to(self.device)
        with self._torch.inference_mode():
            logits = self._model(tensor).detach().cpu().numpy()
        return _validate_logits(logits, values.shape[0], self.class_count)

    def close(self) -> None:
        self._closed = True


def _hailo_shape(shape: object) -> tuple[int, ...]:
    """Normalize the shape objects exposed by supported HailoRT releases."""

    if all(hasattr(shape, field) for field in ("height", "width", "features")):
        return tuple(
            int(getattr(shape, field)) for field in ("height", "width", "features")
        )
    try:
        return tuple(int(value) for value in shape)  # type: ignore[arg-type]
    except TypeError as error:
        raise ValueError(f"Unsupported Hailo tensor shape: {shape!r}") from error


class HailoInferenceBackend:
    """Run a compiled HEF classifier through HailoRT on the Pi AI HAT."""

    def __init__(
        self,
        hef_path: str | Path,
        input_shape: ModelInputShape,
        class_count: int,
        *,
        hailo_module: Any | None = None,
    ):
        path = Path(hef_path)
        if not path.is_file():
            raise FileNotFoundError(f"HEF file does not exist: {path}")
        if input_shape[0] != 1:
            raise ValueError("The streaming Hailo backend requires batch size 1")
        if class_count <= 0:
            raise ValueError("class_count must be positive")

        if hailo_module is None:
            try:
                hailo_module = importlib.import_module("hailo_platform")
            except ImportError as error:
                raise RuntimeError(
                    "HailoRT Python bindings are unavailable. Install the "
                    "hailo-all package supplied for the Raspberry Pi AI HAT."
                ) from error

        self._hailo = hailo_module
        self._input_shape = input_shape
        self._class_count = class_count
        self._closed = False
        self._resources = ExitStack()

        try:
            hef = hailo_module.HEF(str(path))
            device = self._resources.enter_context(hailo_module.VDevice())
            configure_params = hailo_module.ConfigureParams.create_from_hef(
                hef=hef,
                interface=hailo_module.HailoStreamInterface.PCIe,
            )
            network_groups = device.configure(hef, configure_params)
            if len(network_groups) != 1:
                raise ValueError(
                    "The HEF must contain exactly one Hailo network group; "
                    f"found {len(network_groups)}"
                )
            network_group = network_groups[0]
            activation_params = network_group.create_params()
            input_params = hailo_module.InputVStreamParams.make(
                network_group,
                quantized=False,
                format_type=hailo_module.FormatType.FLOAT32,
            )
            output_params = hailo_module.OutputVStreamParams.make(
                network_group,
                quantized=False,
                format_type=hailo_module.FormatType.FLOAT32,
            )
            input_infos = hef.get_input_vstream_infos()
            output_infos = hef.get_output_vstream_infos()
            if len(input_infos) != 1 or len(output_infos) != 1:
                raise ValueError(
                    "The HEF must expose exactly one input and one output stream"
                )

            expected_hwc = (
                input_shape[2],
                input_shape[3],
                input_shape[1],
            )
            actual_hwc = _hailo_shape(input_infos[0].shape)
            if actual_hwc != expected_hwc:
                raise ValueError(
                    "HEF input shape does not match streaming features: "
                    f"expected NHWC data with per-item shape {expected_hwc}, "
                    f"got {actual_hwc}"
                )
            output_size = int(np.prod(_hailo_shape(output_infos[0].shape)))
            if output_size != class_count:
                raise ValueError(
                    f"HEF output has {output_size} values; expected {class_count}"
                )

            self._input_name = input_infos[0].name
            self._output_name = output_infos[0].name
            self._pipeline = self._resources.enter_context(
                hailo_module.InferVStreams(
                    network_group,
                    input_params,
                    output_params,
                )
            )
            self._resources.enter_context(network_group.activate(activation_params))
        except Exception:
            self._resources.close()
            raise

    @property
    def name(self) -> str:
        return "hailo"

    @property
    def input_shape(self) -> ModelInputShape:
        return self._input_shape

    @property
    def class_count(self) -> int:
        return self._class_count

    def infer(self, features: FloatArray) -> FloatArray:
        if self._closed:
            raise RuntimeError("Hailo inference backend is closed")
        values = _validate_features(features, self.input_shape)
        # ChordSense builds NCHW windows; Hailo vstreams consume NHWC buffers.
        hailo_input = np.ascontiguousarray(values.transpose(0, 2, 3, 1))
        outputs = self._pipeline.infer({self._input_name: hailo_input})
        if self._output_name not in outputs:
            raise ValueError(
                f"Hailo output did not contain stream {self._output_name!r}"
            )
        return _validate_logits(
            outputs[self._output_name],
            values.shape[0],
            self.class_count,
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._resources.close()

    def __enter__(self) -> "HailoInferenceBackend":
        if self._closed:
            raise RuntimeError("Hailo inference backend is closed")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
