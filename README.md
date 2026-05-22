# Fraud Detection Console

Dataset-backed banking fraud detection demo with a FastAPI backend, a Next.js operations console, and a three-phase agentic investigation pipeline.

The project runs from the real dataset in `final.csv` and `evaluation/data/processed_seed_data.json`. Primary demo scenarios use real account IDs such as `C8126703807`, `C2972777054`, and `C2006456468`; the UI and main tests no longer rely on synthetic `ACC_*` cases.

## What It Does

- Phase 1: deterministic rule screening with dataset-scale thresholds, whitelist/blacklist, velocity, risk score, shared infrastructure, and balance-drain signals.
- Phase 2: planner/executor/vision/report agents collect and summarize evidence when a transaction needs investigation.
- Phase 3: detective adjudication returns `allow`, `block`, or `escalate`, then updates fallback controls.
- Offline mode: with `DEMO_MODE=true`, cloud services are skipped and local simulators are seeded from the real dataset.
- UI: a banking simulator on the left and a fraud operations console on the right, streaming pipeline events live.

## Project Structure

```text
configs/                     Runtime settings and environment parsing
core/
  agents/                    Planner, Executor, Vision, Report, Detective
  orchestration/pipeline.py   LangGraph pipeline and Phase 1 rules
  schemas/models.py           Pydantic models
infrastructure/
  databases/                  Redis/Neo4j/MongoDB/Chroma clients and simulators
  llm/gemini.py               Gemini wrapper with deterministic fallback
services/
  api/server.py               FastAPI app and CLI demo entry
  ui/                         Next.js fraud operations console
evaluation/
  data/processed_seed_data.json
  build_seed_data.py          Rebuild processed seed data from final.csv
  evaluate_dataset.py         Dataset evaluation metrics
tests/                        Backend/API/pipeline smoke and acceptance tests
final.csv                     Source transaction dataset
main.py                       `python main.py` or `python main.py --serve`
render.yaml                   Render backend deployment blueprint
```

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
python main.py --serve
```

In another terminal:

```powershell
cd services\ui
npm install
npm run dev
```

Open:

```text
Frontend: http://localhost:3000
Backend:  http://localhost:8000
Docs:     http://localhost:8000/docs
```

## Demo Scenarios

The API exposes these dataset-backed scenarios at `GET /api/scenarios`:

| Scenario | Transaction | Expected |
| --- | --- | --- |
| Clean small payment | `C8126703807 -> C1409103719`, amount `144.88` | `allow` |
| Fraud cluster transfer | `C2972777054 -> C8992641070`, amount `27000` | `block` |
| Second fraud cluster | `C2006456468 -> C3259274595`, amount `22000` | `block` |
| High-value legitimate transfer | selected from the largest non-fraud dataset row | `allow` |

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Runtime health and dataset summary |
| `GET` | `/api/scenarios` | Dataset-backed demo scenarios |
| `POST` | `/api/login` | Demo login by real account ID |
| `POST` | `/api/fraud-detection` | SSE streaming fraud pipeline for the UI |
| `POST` | `/transaction` | JSON transaction processing |
| `POST` | `/demo/{n}` | Run a scenario by number |

## Verification

```powershell
python -m compileall -q main.py configs core infrastructure services\api evaluation
pytest -q

cd services\ui
npx.cmd tsc --noEmit
npm.cmd run build
npm.cmd run lint
```

Current backend test coverage includes:

- import/startup safety for `main` and FastAPI app creation
- dataset summary and real demo scenario IDs
- `/health`, `/api/scenarios`, `/api/login`, bad amount handling, and SSE completion
- acceptance decisions for the main clean/fraud/high-value scenarios
- regression coverage for low-risk balance-drain history false positives

This is strong enough for an MVP/demo. It is not yet a bank-grade test suite: add browser E2E, load tests, auth/security tests, and cloud-service integration tests before treating it as production software.

## Benchmark

Run the full dataset benchmark:

```powershell
python evaluation\evaluate_dataset.py --limit 0
```

Latest local full-dataset result on `final.csv`:

| Model | Rows | TP | TN | FP | FN | Precision | Recall | F1 | Accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Final pipeline | 701 | 202 | 498 | 1 | 0 | 0.9951 | 1.0000 | 0.9975 | 0.9986 |
| Rule-only baseline | 701 | 202 | 498 | 1 | 0 | 0.9951 | 1.0000 | 0.9975 | 0.9986 |

Interpretation: fraud recall is saturated on this dataset, so the pipeline matches the rule-only recall rather than exceeding it. The extra value of the pipeline is explainability, evidence capture, SSE visibility, and final adjudication. The output is written to `evaluation/results/dataset_metrics.json`, which is ignored by git.

## Online Hosting

Recommended split for the current monorepo:

- Backend: deploy the repo root to Render or Railway.
- Frontend: deploy `services/ui` to Vercel.
- Keep the folder structure as-is. The old `frontend/` and `backend/` names are not required; modern hosts let you set a root directory per service.

### Backend On Render

This repo includes `render.yaml`, so Render can create the API service from the root.

Manual settings if you do not use the blueprint:

```text
Root directory: .
Build command: pip install -r requirements.txt
Start command: python -m uvicorn services.api.server:app --host 0.0.0.0 --port $PORT
Health check: /health
```

Minimum environment variables:

```text
PYTHON_VERSION=3.12.8
DEMO_MODE=true
API_HOST=0.0.0.0
FRONTEND_URL=https://your-vercel-app.vercel.app
```

Render injects `PORT`; the app reads it automatically. Keep `DEMO_MODE=true` for a reliable online demo without Redis/Neo4j/MongoDB/Chroma/Gemini credentials. Set `DEMO_MODE=false` only after adding the cloud credentials from `.env.example`.

### Frontend On Vercel

Create a Vercel project from the same Git repo and set:

```text
Root directory: services/ui
Install command: npm ci
Build command: npm run build
Output directory: .next
```

Environment variable:

```text
BACKEND_URL=https://your-render-api.onrender.com
```

The UI calls local `/api/*` routes; Next.js rewrites them to `BACKEND_URL`. That keeps browser calls same-origin and reduces CORS issues. After Vercel gives you the final URL, put that URL in Render's `FRONTEND_URL`.

### Railway Alternative

Create two services from the same repo:

- API service: root `.`, start command `python -m uvicorn services.api.server:app --host 0.0.0.0 --port $PORT`
- UI service: root `services/ui`, build `npm run build`, start `npm run start`

Railway and Vercel both support monorepo root-directory settings:

- Vercel monorepos: https://vercel.com/docs/monorepos
- Render web services and port binding: https://render.com/docs/web-services
- Render Python versions: https://render.com/docs/python-version
- Railway monorepos: https://docs.railway.com/deployments/monorepo

## Environment

Use `.env.example` as the template. The default `DEMO_MODE=true` is recommended for local demo, online demo, and testing because it skips network/cloud initialization and seeds simulators from local dataset files.

Set `DEMO_MODE=false` only when cloud credentials are ready for Redis, Neo4j, MongoDB, ChromaDB, and Gemini. Missing or failing cloud services fall back to local simulators where possible.

## Notes

- Python 3.11 or 3.12 is recommended. Python 3.14 currently emits compatibility warnings from LangChain/Pydantic v1.
- The current Gemini package `google-generativeai` emits a deprecation warning; migrating to `google.genai` is the next cleanup task.
- `services/ui/package.json` uses `npm run lint` as a strict TypeScript check so the project works with Next 16 without the removed legacy Next lint command.
