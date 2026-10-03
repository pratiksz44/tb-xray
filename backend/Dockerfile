FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    TF_CPP_MIN_LOG_LEVEL=2

WORKDIR /app
RUN useradd --create-home --uid 10001 appuser

# 1) Dependencies (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# 2) TorchXRayVision weights baked in, so startup never depends on GitHub (cached layer)
USER appuser
RUN python -c "import torchxrayvision as xrv; \
xrv.models.DenseNet(weights='densenet121-res224-all'); xrv.baseline_models.xinario.ViewModel()"

# 3) App code + model files (CD downloads them from Blob Storage into backend/models before building)
COPY --chown=appuser config.yaml ./
COPY --chown=appuser app/ app/
COPY --chown=appuser models/ models/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
