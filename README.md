# TB chest X-ray screening

A web app that estimates the probability of tuberculosis from a frontal chest X-ray.

> Research prototype. Not a medical device and not a diagnosis.

## How it works

```
image ──► is it a frontal chest X-ray? ──no──► "Image not accepted"
          (TorchXRayVision view model +
           similarity to training X-rays)
                    │ yes
                    ▼
          5 InceptionV3 fold models ──► average P(TB) ──► compare with threshold ──► result
```

All models are loaded once when the backend starts.

## Layout

```
backend/                 FastAPI app (uv project)
  config.yaml            all settings: model folder, blob storage location, check thresholds, upload limits
  app/config.py          loads config.yaml
  app/model.py           model loading + chest X-ray check + 5-fold prediction
  app/main.py            API: GET /api/health, POST /api/predict, POST /api/predict-batch (ZIP)
  models/                model files (not in git, see ml/README.md)
  tests/
frontend/                static page (HTML/CSS/JS) served by nginx, which proxies /api to the backend
ml/                      Kaggle training notebook + export cell
infra/                   Azure: main.bicep + bootstrap.sh (one-time setup)
.github/workflows/       ci.yml (lint, tests, image builds) · cd.yml (deploy to Azure on push to main)
```

## Run locally

Unzip `tb_export.zip` (from the notebook) into `backend/models/`, then:

```bash
cd backend
uv sync
uv run uvicorn app.main:app --port 8000      # API on http://localhost:8000/api/docs
uv run pytest && uv run ruff check .         # tests + lint
```

Or run the full stack with Docker: `docker compose up --build` → http://localhost:8080

## Deploy (Azure Container Apps)

1. **Bootstrap Azure once:** `GITHUB_REPO=<owner>/<repo> bash infra/bootstrap.sh`, then add the printed
   secrets/variables in GitHub and create an environment called `production`.
2. **Upload the model files** to the storage account (location set in `backend/config.yaml` → `model.blob_url`):
   ```bash
   az storage blob upload-batch --auth-mode login --account-name pratik \
     --destination tb-classi --source ./tb_export --overwrite
   ```
3. **Push to `main`** (or run the **CD** workflow manually). CD runs CI, downloads the model files into the
   backend image, pushes both images, deploys with Bicep and smoke-tests the live URL.

To release a new model, upload the new files (step 2) and re-run CD.
