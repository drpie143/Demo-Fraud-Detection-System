# Fraud Detection Console

A local-first fraud operations console using real dataset accounts, FastAPI, Next.js, cloud databases, and Gemini-backed investigation agents.

## Live Demo

| Surface | URL |
| --- | --- |
| Web console | https://fraud-detection-console.vercel.app |
| Backend API | https://fraud-detection-api-nijg.onrender.com |
| Health check | https://fraud-detection-api-nijg.onrender.com/health |

Hosted services may take a moment to wake up on free tiers.

## Runtime Model

The main workflow is `DEMO_MODE=false`:

- Redis Cloud powers Phase 1 risk scores, velocity, whitelist, and blacklist checks.
- Neo4j AuraDB stores account, device, IP, and transfer graph evidence.
- MongoDB Atlas stores customer profiles and transaction history.
- ChromaDB Cloud stores fraud knowledge and prior investigation patterns.
- Gemini drives Planner, Executor, Vision, Report, and Detective agents.

`DEMO_MODE=true` remains only for deterministic tests and offline fallback behavior.

## What It Does

- Phase 1 screens transactions with dataset-scale rules, Redis signals, shared infrastructure, and balance-drain checks.
- Phase 2 investigates yellow cases through agent-planned DB queries and evidence analysis.
- Phase 3 returns `allow`, `block`, or `escalate`, then writes audit/risk updates.
- The UI presents a two-column banking simulator and fraud operations console with live SSE pipeline events.

## Project Structure

```text
configs/                      Runtime settings and env parsing
core/
  agents/                     Planner, Executor, Vision, Report, Detective
  orchestration/pipeline.py    LangGraph pipeline and Phase 1 rules
  schemas/models.py            Pydantic models
infrastructure/
  databases/                   Redis, Neo4j, MongoDB, ChromaDB, simulators
  llm/gemini.py                Gemini wrapper with strict benchmark mode
services/
  api/server.py                FastAPI app and CLI entry
  ui/                          Next.js fraud operations console
evaluation/
  data/processed_seed_data.json
  benchmark_seed.py            Build train-split seed data for real benchmark
  build_seed_data.py           Rebuild processed seed data from final.csv
  push_seed_data.py            Push full dataset seed to real cloud DBs
  evaluate_real_resume.py      Resume-safe real DB + Gemini benchmark
  summarize_real_benchmark.py  Validate/summarize real benchmark checkpoint
tests/                         Backend/API/pipeline tests
final.csv                      Source transaction dataset
main.py                        `python main.py` or `python main.py --serve`
```

## Local Setup

Python 3.12 is recommended.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

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

Open locally:

```text
Frontend: http://localhost:3000
Backend:  http://localhost:8000
Docs:     http://localhost:8000/docs
Health:   http://localhost:8000/health
```

`GET /health` should show `mode: real_services` and all core services as connected/configured. If a service says `simulator`, that credential or connection is failing and the runtime has fallen back for that component.

## Dataset Scenarios

The demo scenarios use real IDs from `final.csv`, not synthetic `ACC_*` accounts.

| Scenario | Transaction | Expected |
| --- | --- | --- |
| Clean small payment | `C8126703807 -> C1409103719`, amount `144.88` | `allow` |
| Fraud cluster transfer | `C2972777054 -> C8992641070`, amount `27000` | `block` |
| Second fraud cluster | `C2006456468 -> C3259274595`, amount `22000` | `block` |
| High-value legitimate transfer | largest non-fraud dataset sample | `allow` |

## API Surface

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Runtime mode, service status, dataset summary |
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
npm.cmd run lint
npm.cmd run build
```

Backend tests force `DEMO_MODE=true` in `tests/conftest.py` so they are deterministic and do not spend cloud quota. Use `/health` and a small manual transaction run to verify the real cloud path.

## Benchmark

The benchmark that matters is the temporal holdout: the first 70% of `final.csv` is used as historical memory, and the last 30% is evaluated as future transactions.

### Real DB + Gemini Result

Latest completed real-service checkpoint: `evaluation/results/real_llm_resume.json`.

- Rows: `211/211` completed
- Cloud services: Redis, Neo4j, MongoDB, and ChromaDB connected
- LLM: Gemini enabled with strict error mode, so missing keys/quota/API errors stop instead of falling back silently
- Seed policy: Redis, Neo4j, and MongoDB were reset to the train split only; ChromaDB knowledge collection was reused

| Model | Rows | TP | TN | FP | FN | Precision | Recall | F1 | Accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Real Gemini pipeline | 211 | 53 | 144 | 8 | 6 | 0.8689 | 0.8983 | 0.8833 | 0.9336 |
| Rule-only baseline | 211 | 30 | 151 | 1 | 29 | 0.9677 | 0.5085 | 0.6667 | 0.8578 |

Yellow hard cases where Phase 1 returns `yellow`:

| Model | Rows | TP | TN | FP | FN | Precision | Recall | F1 | Accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Real Gemini pipeline | 179 | 23 | 143 | 7 | 6 | 0.7667 | 0.7931 | 0.7797 | 0.9274 |
| Rule-only baseline | 179 | 0 | 150 | 0 | 29 | 0.0000 | 0.0000 | 0.0000 | 0.8380 |

Summarize or validate the checkpoint without calling cloud APIs:

```powershell
python evaluation\summarize_real_benchmark.py
python evaluation\summarize_real_benchmark.py --json
```

Run or resume the real benchmark:

```powershell
# First run only: reset cloud DBs to the train split and start a fresh checkpoint.
python evaluation\evaluate_real_resume.py --push-cloud-train --reset-progress --max-new-yellow 5

# Later runs: resume from the checkpoint. Do not reseed cloud DBs.
python evaluation\evaluate_real_resume.py --max-new-yellow 5
```

`evaluate_real_resume.py` writes `evaluation/results/real_llm_resume.json` after every successful transaction. Metrics are emitted only when `completed_rows == holdout_rows`.

## Real-Service Notes

- `AUTO_SEED_ON_STARTUP=false` is recommended for daily local work. It prevents the API server from wiping/reseeding cloud DBs every time it starts.
- `python evaluation\push_seed_data.py` is the full-dataset seed path for the local UI demo.
- `DEMO_MODE=false` still has graceful fallback per service. If one cloud service fails, the API can continue, but `/health` will reveal which component fell back.
- Fallback caches are hydrated from the real dataset at startup, so a temporary cloud failure still uses `C...` dataset IDs instead of old `ACC_*` demo data.
- MongoDB Atlas `SSL handshake failed` / `TLSV1_ALERT_INTERNAL_ERROR` usually means the current public IP is not allowed in Atlas Network Access.
- Normal Gemini calls are serialized and retried to reduce free-tier quota pressure. The resumable real benchmark uses strict mode, so quota/API errors stop the current run and keep the last saved checkpoint.
- The project currently uses the deprecated `google-generativeai` SDK. It still works, but migrating to `google.genai` is the next maintenance step.
