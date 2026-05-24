# Fraud Detection Console

Local-first fraud detection console using real dataset accounts, a FastAPI API, a Next.js operations UI, cloud databases, and Gemini-backed investigation agents.

The main runtime target is `DEMO_MODE=false`: Redis Cloud, Neo4j AuraDB, MongoDB Atlas, ChromaDB Cloud, and Gemini are used when their credentials are present. `DEMO_MODE=true` is kept only for offline fallback and automated tests.

## What It Does

- Phase 1 screens transactions with dataset-scale rules, Redis risk scores, whitelists, blacklists, velocity, shared infrastructure, and balance-drain signals.
- Phase 2 runs Planner, Executor, Vision, and Report agents to collect evidence from MongoDB, Neo4j, ChromaDB, Redis, and the current transaction.
- Phase 3 uses the Detective agent to return `allow`, `block`, or `escalate`, then writes audit/risk updates back through the pipeline.
- The UI is a two-column banking and fraud operations console with live SSE pipeline events.

## Project Structure

```text
configs/                      Runtime settings and env parsing
core/
  agents/                     Planner, Executor, Vision, Report, Detective
  orchestration/pipeline.py    LangGraph pipeline and Phase 1 rules
  schemas/models.py            Pydantic models
infrastructure/
  databases/                   Redis, Neo4j, MongoDB, ChromaDB, simulators
  llm/gemini.py                Gemini wrapper with quota-safe fallback
services/
  api/server.py                FastAPI app and CLI entry
  ui/                          Next.js fraud operations console
evaluation/
  data/processed_seed_data.json
  build_seed_data.py           Rebuild seed data from final.csv
  push_seed_data.py            Push full dataset seed to real cloud DBs
  evaluate_honest.py           Leakage-controlled benchmark
tests/                         Backend/API/pipeline tests
final.csv                      Source transaction dataset
main.py                        `python main.py` or `python main.py --serve`
```

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
```

Fill `.env` with your real Redis, Neo4j, MongoDB, ChromaDB, and Gemini credentials. For the main workflow:

```text
DEMO_MODE=false
AUTO_SEED_ON_STARTUP=false
BACKEND_URL=http://localhost:8000
FRONTEND_URL=http://localhost:3000
```

Seed the cloud databases when the dataset or credentials change:

```powershell
python evaluation\push_seed_data.py
```

Start the API:

```powershell
python main.py --serve
```

Start the UI in another terminal:

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
Health:   http://localhost:8000/health
```

`GET /health` should show `mode: real_services` and service statuses such as `redis: connected`, `neo4j: connected`, `mongodb: connected`, `chromadb: connected`, and `gemini: configured`. If a service says `simulator`, that credential or connection is failing and the runtime has fallen back for that component.

## Dataset Scenarios

The demo scenarios use real IDs from `final.csv`, not synthetic `ACC_*` accounts.

| Scenario | Transaction | Expected |
| --- | --- | --- |
| Clean small payment | `C8126703807 -> C1409103719`, amount `144.88` | `allow` |
| Fraud cluster transfer | `C2972777054 -> C8992641070`, amount `27000` | `block` |
| Second fraud cluster | `C2006456468 -> C3259274595`, amount `22000` | `block` |
| High-value legitimate transfer | largest non-fraud dataset sample | `allow` |

API:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Runtime mode, real-service status, dataset summary |
| `GET` | `/api/scenarios` | Dataset-backed demo scenarios |
| `POST` | `/api/login` | Login by real dataset account ID |
| `POST` | `/api/fraud-detection` | SSE streaming pipeline for the UI |
| `POST` | `/transaction` | JSON transaction processing |
| `POST` | `/demo/{n}` | Run a dataset scenario by number |

## Verification

```powershell
python -m compileall -q main.py configs core infrastructure services\api evaluation
pytest -q

cd services\ui
npx.cmd tsc --noEmit
npm.cmd run build
npm.cmd run lint
```

Backend tests force `DEMO_MODE=true` in `tests/conftest.py` so they are deterministic and do not spend cloud quota. Use `/health`, `push_seed_data.py`, and a small manual transaction run to verify the real cloud path.

## Benchmark

Do not use the old full-dataset benchmark as a performance claim. It seeds profiles, graph edges, and fraud ratios from the same rows being evaluated, so it is useful only as a smoke test.

Use the leakage-controlled temporal holdout benchmark:

```powershell
python evaluation\evaluate_honest.py --train-frac 0.7 --limit 0
```

Latest local honest result on `final.csv`:

| Model | Rows | TP | TN | FP | FN | Precision | Recall | F1 | Accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Final pipeline | 211 | 59 | 151 | 1 | 0 | 0.9833 | 1.0000 | 0.9916 | 0.9953 |
| Rule-only baseline | 211 | 30 | 151 | 1 | 29 | 0.9677 | 0.5085 | 0.6667 | 0.8578 |

Hard cases where Phase 1 returns `yellow`:

| Model | Rows | TP | TN | FP | FN | Precision | Recall | F1 | Accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Final pipeline | 177 | 29 | 148 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Rule-only baseline | 177 | 0 | 148 | 0 | 29 | 0.0000 | 0.0000 | 0.0000 | 0.8362 |

For a real-DB benchmark without spending Gemini quota:

```powershell
python evaluation\evaluate_honest.py --use-cloud-db --push-cloud-train --max-yellow-pipeline -1
```

For a Gemini-backed sample, cap yellow cases:

```powershell
python evaluation\evaluate_honest.py --use-cloud-db --use-gemini --max-yellow-pipeline 10
```

`--push-cloud-train` reseeds Redis, Neo4j, and MongoDB with the train split only. Use `python evaluation\push_seed_data.py` afterward if you want the full local demo dataset back in the cloud DBs.

## Real-Service Notes

- `AUTO_SEED_ON_STARTUP=false` is recommended for daily local work. It prevents the API server from wiping/reseeding cloud DBs every time it starts.
- `python evaluation\push_seed_data.py` is the full-dataset seed path for the local UI demo.
- `DEMO_MODE=false` still has graceful fallback per service. If one cloud service fails, the API can continue, but `/health` will reveal which component fell back.
- Fallback caches are hydrated from the real dataset at startup, so a temporary cloud failure still uses `C...` dataset IDs instead of old `ACC_*` demo data.
- MongoDB Atlas `SSL handshake failed` / `TLSV1_ALERT_INTERNAL_ERROR` usually means the current public IP is not allowed in Atlas Network Access. Add your machine's IP to Atlas, then restart `python main.py --serve`.
- Gemini calls are serialized and retried to reduce free-tier quota pressure. Benchmarks should use `--max-yellow-pipeline` when `--use-gemini` is enabled.
- Python 3.11 or 3.12 is recommended. Python 3.14 currently emits compatibility warnings from LangChain/Pydantic v1.
