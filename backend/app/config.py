"""Settings from backend/config.yaml (path can be overridden with the CONFIG_FILE env var)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

BACKEND_DIR = Path(__file__).resolve().parents[1]
CONFIG_FILE = Path(os.environ.get("CONFIG_FILE", BACKEND_DIR / "config.yaml"))

config = yaml.safe_load(CONFIG_FILE.read_text())

MODEL_DIR = BACKEND_DIR / config["model"]["dir"]
COLOUR_MAX = float(config["chest_xray_check"]["colour_max"])
NOT_FRONTAL_MAX = float(config["chest_xray_check"]["not_frontal_max"])
MAX_UPLOAD_MB = float(config["api"]["max_upload_mb"])
MAX_ZIP_MB = float(config["api"]["max_zip_mb"])
MAX_ZIP_IMAGES = int(config["api"]["max_zip_images"])
TORCH_THREADS = int(config["api"]["torch_threads"])
