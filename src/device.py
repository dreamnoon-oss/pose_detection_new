"""Device selection and CPU/GPU tuning for the detection engine.

Apple Silicon (M-series) Macs expose the PyTorch ``mps`` backend; this module
picks the fastest available device and applies sensible thread/FP16 settings
so inference makes best use of the chip.
"""

import os

import torch


def resolve_device(preferred=None):
    """Resolve ``"auto"`` (or None) to the best available device.

    Priority: explicit value > CUDA > MPS (Apple Silicon) > CPU.
    """
    if preferred and preferred not in ("auto", ""):
        return preferred
    if torch.cuda.is_available():
        return "cuda:0"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def mps_available():
    return torch.backends.mps.is_available()


def use_half(device, requested=True):
    """FP16 is only beneficial on GPU/MPS; ignore it on CPU."""
    if not requested:
        return False
    return device.startswith("cuda") or device == "mps"


def tune_threads():
    """Give torch/OpenCV the machine's CPU cores (decode + pre/post-processing)."""
    n = os.cpu_count() or 4
    try:
        torch.set_num_threads(n)
    except Exception:
        pass
    try:
        import cv2
        cv2.setNumThreads(n)
    except Exception:
        pass
    return n
