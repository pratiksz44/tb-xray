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
APP_PASSWORD=<password> uv run uvicorn app.main:app --port 8000   # API on http://localhost:8000/api/docs
uv run pytest && uv run ruff check .                              # tests + lint
```

Or run the full stack with Docker: `APP_PASSWORD=<password> docker compose up --build` → http://localhost:8080

**Login:** one user. The username is `auth.username` in `backend/config.yaml` (`pratik`). The password is the
`APP_PASSWORD` environment variable (the `APP_PASSWORD` GitHub secret in production), so it's never in the repo.
The prediction endpoints return 401 until you sign in.

## Branching

```
feature/<name>, sprint/<n>, fix/<name> ──PR──► dev ──PR (release)──► main ──► CD deploys to Azure
hotfix/<name> (from main) ──PR──► main, then merge main back into dev
```

| Branch | Purpose | Rules |
|---|---|---|
| `main` | production; every merge deploys | no direct pushes; PRs only from `dev` or `hotfix/*`; CI must pass |
| `dev` | integration; next release | no direct pushes; PRs from feature/sprint branches; CI must pass |
| `feature/*`, `sprint/*`, `fix/*` | day-to-day work, branched from `dev` | free to push; CI runs on every push |
| `hotfix/*` | urgent production fix, branched from `main` | after merging, also merge `main` into `dev` |

CI (`ci.yml`) runs on every push and PR. CD (`cd.yml`) runs only on `main`. `branch-policy.yml` blocks PRs
into `main` from any other branch.

## Deploy (Azure Container Instances)

CD (`.github/workflows/cd.yml`) runs on every push to `main`: tests → download model files from Blob Storage →
build + push both images to `ghcr.io` → update the ACI container group in place (nginx on port 8080 proxying to the
backend on localhost) → smoke test. The app is served at `http://<ACI_NAME>-app.<region>.azurecontainer.io:8080`.

One-time GitHub setup (Settings → Secrets and variables → Actions, plus an environment named `production`):

| Name | Kind | Value |
|---|---|---|
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | secrets | Entra app with a federated credential for this repo's `production` environment |
| `GHCR_TOKEN` | secret | classic GitHub PAT with `read:packages` (ACI uses it to pull the private images) |
| `APP_PASSWORD` | secret | the login password for user `pratik` |
| `AZURE_RESOURCE_GROUP` | variable | resource group of the container instance |
| `ACI_NAME` | variable | container group name (DNS label is `<ACI_NAME>-app`) |

The Entra app needs **Contributor** on the resource group and **Storage Blob Data Reader** on the model storage
account. Model files go in the container from `backend/config.yaml` → `model.blob_url`:

```bash
az storage blob upload-batch --auth-mode login --account-name pratik \
  --destination tb-classi --source ./tb_export --overwrite
```
