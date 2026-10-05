"""API tests with a fake model (no model files needed)."""

import io
import zipfile

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.auth import require_user
from app.main import app, get_model
from app.model import InvalidImageError, colourfulness, decode


class FakeModel:
    def predict(self, data: bytes, trace=None) -> dict[str, object]:
        if data == b"chest":
            return {"is_chest_xray": True, "tb_probability": 0.9, "prediction": "TB suspected"}
        if data == b"cat":
            return {"is_chest_xray": False, "message": "not a chest X-ray"}
        raise InvalidImageError("bad")


app.dependency_overrides[get_model] = FakeModel
app.dependency_overrides[require_user] = lambda: "pratik"  # signed in; the login tests remove this
client = TestClient(app)  # not used as a context manager, so startup does not load the real models


def post(data: bytes):
    return client.post("/api/predict", files={"file": ("x.png", data, "image/png")})


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_chest_xray_is_predicted():
    body = post(b"chest").json()
    assert body["is_chest_xray"] is True
    assert body["prediction"] == "TB suspected"
    assert "disclaimer" in body


def test_non_xray_is_rejected():
    body = post(b"cat").json()
    assert body["is_chest_xray"] is False


def test_unreadable_image_is_400():
    assert post(b"garbage").status_code == 400


def test_too_large_is_413():
    assert post(b"0" * (20 * 1024 * 1024 + 1)).status_code == 413


def test_decode_and_colourfulness():
    gray = np.full((64, 64, 3), 120, np.uint8)
    ok, png = cv2.imencode(".png", gray)
    assert ok
    assert colourfulness(decode(png.tobytes())) == 0
    colour = gray.copy()
    colour[..., 2] = 255
    assert colourfulness(colour) > 10


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def post_zip(data: bytes):
    return client.post("/api/predict-batch", files={"file": ("xrays.zip", data, "application/zip")})


def test_batch_predicts_each_image():
    data = make_zip(
        {
            "a/chest.png": b"chest",
            "b/cat.jpg": b"cat",
            "c/bad.jpeg": b"garbage",
            "notes.txt": b"ignored",
            "__MACOSX/a/._chest.png": b"ignored",
            "folder/": b"",
        }
    )
    body = post_zip(data).json()
    assert [r["filename"] for r in body["results"]] == ["a/chest.png", "b/cat.jpg", "c/bad.jpeg"]
    assert body["summary"] == {"total": 3, "tb_suspected": 1, "no_tb": 0, "not_accepted": 1, "errors": 1}
    assert "error" in body["results"][2]
    assert "disclaimer" in body


def test_batch_not_a_zip_is_400():
    assert post_zip(b"not a zip").status_code == 400


def test_batch_without_images_is_400():
    assert post_zip(make_zip({"readme.txt": b"hi"})).status_code == 400


def test_batch_too_many_images_is_413():
    assert post_zip(make_zip({f"{i}.png": b"chest" for i in range(51)})).status_code == 413


def test_batch_too_large_is_413():
    assert post_zip(b"0" * (100 * 1024 * 1024 + 1)).status_code == 413


def test_request_id_is_returned_and_logs_are_json():
    import json
    import logging

    from app.logging_config import JsonFormatter, request_id_var

    res = client.get("/api/health", headers={"X-Request-ID": "abc123"})
    assert res.headers["X-Request-ID"] == "abc123"
    assert res.headers["Server-Timing"].startswith("app;dur=")

    token = request_id_var.set("abc123")
    record = logging.LogRecord("app", logging.INFO, "", 0, "prediction", None, None)
    record.tb_probability = 0.9
    line = json.loads(JsonFormatter().format(record))
    request_id_var.reset(token)
    assert line["message"] == "prediction"
    assert line["request_id"] == "abc123"
    assert line["tb_probability"] == 0.9


@pytest.fixture
def real_login(monkeypatch):
    """Use the real login check (not the signed-in override) with a known password and no 1 s delay."""
    monkeypatch.setenv("APP_PASSWORD", "secret-pw")
    monkeypatch.setattr("app.main.time.sleep", lambda _: None)
    monkeypatch.delitem(app.dependency_overrides, require_user)
    yield TestClient(app)


def test_predict_requires_login(real_login):
    res = real_login.post("/api/predict", files={"file": ("x.png", b"chest", "image/png")})
    assert res.status_code == 401
    assert real_login.get("/api/me").status_code == 401


def test_wrong_password_is_401(real_login):
    res = real_login.post("/api/login", json={"username": "pratik", "password": "nope"})
    assert res.status_code == 401
    assert "tbx_session" not in res.cookies


def test_login_then_predict_then_logout(real_login):
    res = real_login.post("/api/login", json={"username": "pratik", "password": "secret-pw"})
    assert res.status_code == 200
    assert real_login.get("/api/me").json() == {"username": "pratik"}
    res = real_login.post("/api/predict", files={"file": ("x.png", b"chest", "image/png")})
    assert res.status_code == 200
    real_login.post("/api/logout")
    assert real_login.get("/api/me").status_code == 401


def test_tampered_session_is_rejected(real_login):
    from app.auth import create_session, session_user

    token = create_session("pratik")
    assert session_user(token) == "pratik"
    assert session_user(token.replace("pratik", "admin", 1)) is None
