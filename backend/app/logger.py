"""Process log: every step one image goes through, from upload to result.

The normal logs (app/logging_config.py) say *that* a request happened. The process log says *what happened to
each image*: one JSON line per step, all with the same image_id (and the request_id), how long the step took,
and the values the decision was based on. Filter on "logger": "process" to see only these lines.

    {"logger": "process", "message": "view_check", "request_id": "...", "image_id": "a1b2c3d4e5f6",
     "step_ms": 212.4, "total_ms": 260.9, "p_not_frontal": 0.0213, "limit": 0.5, "passed": true}

Steps, in order (the image stops at the first failed check):
    received -> decode -> preprocess -> model_lock (time spent queued behind other images)
    -> colour_check -> view_check -> similarity_check -> tb_model (5 fold probabilities) -> result

Never log image bytes or file names: they can contain patient information.
"""

from __future__ import annotations

import logging
import time
import uuid

_logger = logging.getLogger("process")


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


class ProcessLog:
    """Step-by-step log for one image. Create one per image, then call step() after each stage."""

    def __init__(self) -> None:
        self.image_id = uuid.uuid4().hex[:12]
        self._start = self._last = time.perf_counter()

    def step(self, name: str, **details: object) -> None:
        self._log(logging.INFO, name, details)

    def fail(self, name: str, **details: object) -> None:
        """A step that stopped processing (unreadable, too large, ...)."""
        self._log(logging.WARNING, name, {**details, "passed": False})

    def _log(self, level: int, name: str, details: dict[str, object]) -> None:
        now = time.perf_counter()
        timing = {"step_ms": _ms(now - self._last), "total_ms": _ms(now - self._start)}
        _logger.log(level, name, extra={"image_id": self.image_id, **timing, **details})
        self._last = now
