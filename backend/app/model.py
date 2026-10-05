"""TB chest X-ray model: 1) is it a frontal chest X-ray?  2) if yes, average the 5 InceptionV3 folds.

Expected files in MODEL_DIR (produced by ml/export_for_deployment.py):
    config.json                      img_size, threshold, gate.knn_k, gate.knn_threshold
    gate_ref_feats.npy               TorchXRayVision features of the training X-rays
    inception_fold{1..5}.keras       the 5 fold models
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import cv2
import numpy as np

from app.config import COLOUR_MAX, MODEL_DIR, NOT_FRONTAL_MAX, TORCH_THREADS
from app.logger import ProcessLog

N_FOLDS = 5
NOT_XRAY_MESSAGE = "This does not look like a frontal (PA/AP) chest X-ray. Please upload a chest X-ray image."


class InvalidImageError(ValueError):
    """The upload could not be decoded as an image."""


def decode(data: bytes) -> np.ndarray:
    """Bytes -> BGR uint8 image."""
    bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
    if bgr is None:
        raise InvalidImageError("could not decode image")
    return bgr


def colourfulness(bgr: np.ndarray) -> float:
    c = bgr.astype(np.float32)
    return float((np.abs(c[..., 0] - c[..., 1]) + np.abs(c[..., 1] - c[..., 2])).mean() / 2)


def xrv_normalize(img: np.ndarray) -> np.ndarray:
    """TorchXRayVision convention: [0, 255] -> [-1024, 1024], shape (1, 1, H, W)."""
    return (((img.astype(np.float32) / 255.0) * 2.0 - 1.0) * 1024.0)[None, None]


class TBModel:
    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        # torch/torchxrayvision must be imported before keras: the other order can segfault on Linux.
        import torch
        import torchxrayvision as xrv

        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
        import keras

        cfg = json.loads((model_dir / "config.json").read_text())
        self.img_size = int(cfg["img_size"])
        self.threshold = float(cfg["threshold"])
        self.knn_k = int(cfg["gate"]["knn_k"])
        self.knn_threshold = float(cfg["gate"]["knn_threshold"])
        ref = np.load(model_dir / "gate_ref_feats.npy").astype(np.float32)
        self.reference = ref / np.maximum(np.linalg.norm(ref, axis=1, keepdims=True), 1e-8)

        # Step 1 models: chest X-ray check
        torch.set_num_threads(TORCH_THREADS)
        self.torch = torch
        self.view_model = xrv.baseline_models.xinario.ViewModel().eval()
        self.densenet = xrv.models.DenseNet(weights="densenet121-res224-all").eval()

        # Step 2 models: the 5 InceptionV3 folds
        paths = sorted(model_dir.glob("inception_fold*.keras"))
        if len(paths) != N_FOLDS:
            raise FileNotFoundError(f"expected {N_FOLDS} inception_fold*.keras files in {model_dir}")
        self.folds = [keras.models.load_model(p, compile=False) for p in paths]
        self._lock = threading.Lock()  # one prediction at a time per replica (bounds memory)

    def is_chest_xray(self, bgr: np.ndarray, gray: np.ndarray, trace: ProcessLog) -> bool:
        colour = colourfulness(bgr)
        passed = colour <= COLOUR_MAX
        trace.step("colour_check", colourfulness=round(colour, 2), limit=COLOUR_MAX, passed=passed)
        if colour > COLOUR_MAX:
            return False
        with self.torch.no_grad():
            logits = self.view_model(self.torch.from_numpy(xrv_normalize(gray)))
            p_not_frontal = float(self.torch.softmax(logits, dim=1)[0, 1])
            trace.step(
                "view_check",
                p_not_frontal=round(p_not_frontal, 4),
                limit=NOT_FRONTAL_MAX,
                passed=p_not_frontal < NOT_FRONTAL_MAX,
            )
            if p_not_frontal >= NOT_FRONTAL_MAX:
                return False
            small = cv2.resize(gray, (224, 224), interpolation=cv2.INTER_AREA)
            feats = self.densenet.features2(self.torch.from_numpy(xrv_normalize(small))).numpy()[0]
        # Mean cosine distance to the k nearest training X-rays (threshold calibrated during training)
        feats = feats / max(float(np.linalg.norm(feats)), 1e-8)
        distance = 1.0 - float(np.sort(self.reference @ feats)[-self.knn_k :].mean())
        passed = distance <= self.knn_threshold
        trace.step(
            "similarity_check",
            knn_distance=round(distance, 4),
            limit=round(self.knn_threshold, 4),
            k=self.knn_k,
            passed=passed,
        )
        return passed

    def predict(self, data: bytes, trace: ProcessLog | None = None) -> dict[str, object]:
        trace = trace or ProcessLog()
        try:
            bgr = decode(data)
        except InvalidImageError:
            trace.fail("decode", reason="not a readable PNG/JPEG")
            raise
        trace.step("decode", width=bgr.shape[1], height=bgr.shape[0], passed=True)
        # Resize while uint8, exactly like the training notebook.
        size = (self.img_size, self.img_size)
        gray = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), size, interpolation=cv2.INTER_AREA)
        trace.step("preprocess", grayscale_size=self.img_size)
        with self._lock:
            trace.step("model_lock")  # step_ms = time queued behind other images
            if not self.is_chest_xray(bgr, gray, trace):
                trace.step("result", is_chest_xray=False)
                return {"is_chest_xray": False, "message": NOT_XRAY_MESSAGE}
            x = np.repeat(gray[None, ..., None], 3, axis=-1).astype(np.float32)  # raw 0-255, scaled in-model
            fold_probs = [float(np.ravel(m(x, training=False))[0]) for m in self.folds]
            prob = float(np.mean(fold_probs))
            trace.step("tb_model", fold_probabilities=[round(p, 4) for p in fold_probs], mean=round(prob, 4))
        prediction = "TB suspected" if prob >= self.threshold else "No TB signs detected"
        trace.step(
            "result",
            is_chest_xray=True,
            tb_probability=round(prob, 4),
            threshold=round(self.threshold, 4),
            prediction=prediction,
        )
        return {
            "is_chest_xray": True,
            "tb_probability": round(prob, 4),
            "threshold": round(self.threshold, 4),
            "prediction": prediction,
        }
