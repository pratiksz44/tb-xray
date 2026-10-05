"""TB chest X-ray API.  Run: uv run uvicorn app.main:app --port 8000"""

from __future__ import annotations

import io
import logging
import time
import zipfile
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, UploadFile

from app.config import LOG_LEVEL, MAX_UPLOAD_MB, MAX_ZIP_IMAGES, MAX_ZIP_MB
from app.logger import ProcessLog
from app.logging_config import log_requests, setup_logging
from app.model import InvalidImageError, TBModel

setup_logging(LOG_LEVEL)
logger = logging.getLogger(__name__)

DISCLAIMER = "Research prototype. Not a medical device and not a diagnosis."
MB = 1024 * 1024
IMAGE_LIMIT = int(MAX_UPLOAD_MB * MB)
ZIP_LIMIT = int(MAX_ZIP_MB * MB)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
UNREADABLE_IMAGE = "Could not read the image. Upload a PNG or JPEG chest X-ray."
LOGGED_RESULT_FIELDS = ("is_chest_xray", "tb_probability", "prediction")


@lru_cache(maxsize=1)
def get_model() -> TBModel:
    return TBModel()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    start = time.perf_counter()
    get_model()  # load all models once at startup
    logger.info("models loaded", extra={"duration_s": round(time.perf_counter() - start, 1)})
    yield


app = FastAPI(
    title="TB chest X-ray screening API",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)
app.middleware("http")(log_requests)


def read_upload(file: UploadFile, limit: int, limit_mb: float) -> bytes:
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File larger than {limit_mb:g} MB.")
    return data


def is_zip_image(info: zipfile.ZipInfo) -> bool:
    """PNG/JPEG files, skipping folders and macOS/hidden metadata (__MACOSX/, ._x.png, .DS_Store)."""
    path = PurePosixPath(info.filename)
    hidden = any(part.startswith((".", "__MACOSX")) for part in path.parts)
    return not info.is_dir() and not hidden and path.suffix.lower() in IMAGE_EXTENSIONS


def predict_zip_entry(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, model: TBModel, position: str
) -> dict[str, object]:
    item: dict[str, object] = {"filename": info.filename}
    trace = ProcessLog()
    trace.step("received", source="zip", position=position, size_bytes=info.file_size)
    too_large = f"File larger than {MAX_UPLOAD_MB:g} MB."
    if info.file_size > IMAGE_LIMIT:
        trace.fail("extract", reason="too large")
        return {**item, "error": too_large}
    try:
        with zf.open(info) as f:
            data = f.read(IMAGE_LIMIT + 1)  # don't trust the header size (zip bombs)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError):
        trace.fail("extract", reason="corrupt or encrypted")
        return {**item, "error": "Could not extract this file (corrupt or encrypted)."}
    if len(data) > IMAGE_LIMIT:
        trace.fail("extract", reason="too large")
        return {**item, "error": too_large}
    trace.step("extract", passed=True)
    try:
        return {**item, **model.predict(data, trace)}
    except InvalidImageError:
        return {**item, "error": UNREADABLE_IMAGE}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/predict")
def predict(file: UploadFile, model: Annotated[TBModel, Depends(get_model)]) -> dict[str, object]:
    data = read_upload(file, IMAGE_LIMIT, MAX_UPLOAD_MB)
    trace = ProcessLog()
    trace.step("received", source="upload", size_bytes=len(data))
    try:
        result = model.predict(data, trace)
    except InvalidImageError as exc:
        logger.warning("unreadable image", extra={"size_bytes": len(data)})
        raise HTTPException(400, UNREADABLE_IMAGE) from exc
    logger.info("prediction", extra={k: result.get(k) for k in LOGGED_RESULT_FIELDS})
    return {**result, "disclaimer": DISCLAIMER}


@app.post("/api/predict-batch")
def predict_batch(file: UploadFile, model: Annotated[TBModel, Depends(get_model)]) -> dict[str, object]:
    """ZIP of PNG/JPEG chest X-rays -> one result per image (a bad image doesn't fail the whole batch)."""
    data = read_upload(file, ZIP_LIMIT, MAX_ZIP_MB)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, "Could not read the ZIP file.") from exc
    with zf:
        entries = sorted((i for i in zf.infolist() if is_zip_image(i)), key=lambda i: i.filename)
        if not entries:
            raise HTTPException(400, "The ZIP file contains no PNG or JPEG images.")
        if len(entries) > MAX_ZIP_IMAGES:
            raise HTTPException(413, f"The ZIP has {len(entries)} images; the limit is {MAX_ZIP_IMAGES}.")
        n = len(entries)
        results = [predict_zip_entry(zf, info, model, f"{i}/{n}") for i, info in enumerate(entries, 1)]

    accepted = [r for r in results if r.get("is_chest_xray")]
    summary = {
        "total": len(results),
        "tb_suspected": sum(r["prediction"] == "TB suspected" for r in accepted),
        "no_tb": sum(r["prediction"] != "TB suspected" for r in accepted),
        "not_accepted": sum(r.get("is_chest_xray") is False for r in results),
        "errors": sum("error" in r for r in results),
    }
    logger.info("batch prediction", extra=summary)
    return {"summary": summary, "results": results, "disclaimer": DISCLAIMER}
