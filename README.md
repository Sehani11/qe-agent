# qe-agent-v2

QE Verification Agent — FastAPI backend + Next.js frontend.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm
- A reachable Postgres (the repo is configured against hosted Supabase; see `backend/.env`)

## Setup

Environment files are already present:
- `backend/.env` — backend config (DB URL, LLM keys, etc.). Template: `.env.example`.
- `frontend/.env.local` — frontend config (API URL, Supabase keys). Template: `frontend/.env.example`.

If missing, copy from the example files and fill in values.

## Run locally (two terminals)

### Terminal 1 — Backend (port 8000)

```powershell
cd backend
pip install uv
uv sync --extra dev                 # one-time: install deps
uv run alembic upgrade head         # apply DB migrations
uv run uvicorn app.main:app --reload --port 8000
```

- API: http://localhost:8000
- Health: http://localhost:8000/health
- Docs: http://localhost:8000/docs

**If you will use the Fine tune page's "Train now" button**, add one more
one-time install:

```powershell
cd backend
uv pip install kaggle
```

`uv pip install`, not `uv add`: the Train button shells out to
[`training/kaggle_run.py`](training/kaggle_run.py) using the backend venv's
interpreter, so `kaggle` has to be importable there — but it is deliberately
kept out of `pyproject.toml`, because the backend Dockerfile ends with
`COPY . .` and anything in the dependency set would ship in the production
image ([training/requirements.txt](training/requirements.txt) spells this out).
Installing it into the venv without declaring it keeps both true.

Without it, a run fails at the push step with `No module named 'kaggle'` — which
the UI currently reports as "Kaggle rejected the run… needs the account to be
phone-verified", so check the run's log before believing that message.

### Terminal 2 — Frontend (port 3000)

```powershell
cd frontend
npm install                         # one-time
npm run dev
```

- App: http://localhost:3000

The frontend talks to the backend via `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`); CORS is preconfigured for `http://localhost:3000`.

### Terminal 3 — Fine-tuned model shim (port 9000, optional)

Only needed to generate BDD with the **fine-tuned** model; the app runs on the
general LLM without it. The shim is a separate process that implements the HTTP
contract the backend's `FineTunedModelProvider` speaks and forwards to Ollama, so
the backend needs no change to use a local fine-tune.

#### Setting up Ollama (once per machine)

Ollama is the runtime that actually holds the weights. **None of this is needed
to run the app** — it is only for the fine-tuned model. Nothing here is in git
either (`training/outputs/`, `llama.cpp/` and `convert-venv/` are all ignored),
so a new machine starts from scratch.

**1. Install Ollama and pull the base model.**

Download from [ollama.com](https://ollama.com/download), then:

```powershell
ollama pull qwen2.5:1.5b-instruct
ollama list                         # confirm it is there
```

It must be the **same base the adapter was trained on** — a LoRA applied to a
different base produces erratic output rather than an error. The adapter names
its base in `adapter_config.json`, and the mapping from that name to the tag to
pull is the `BASE_MODELS` table in
[`model_serving_service.py`](backend/app/services/model_serving_service.py).
Current runs train on `unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit`, whose tag is the
one above.

**2. Get an adapter onto the machine.** Either train one (Fine tune → Train now,
which runs on Kaggle's GPU) or copy a `training/outputs/run-*` folder across.

**3. Register it with Ollama.** Two ways:

- **From the app (easiest).** Start the shim below, then press **Serve this
  run** on a completed run. It converts the adapter, registers it, and repoints
  the shim — no commands. It needs the conversion toolchain on this machine
  (`llama.cpp` clone + an isolated `convert-venv`, set up as described in
  [training/serve/README.md](training/serve/README.md)); without it the button
  is hidden and the Training panel says why.
- **By hand.** Convert the adapter to GGUF and `ollama create` — the full
  sequence, and the reasons each step is the way it is, are in
  [training/serve/README.md](training/serve/README.md).

> ⚠️ The Modelfile must **not** carry a `TEMPLATE` line. `chat_template.jinja`
> is Jinja and Ollama Modelfiles take Go templates; pasting one in breaks the
> prompt format. The base's own ChatML template is inherited instead — which is
> why step 1's base has to be right.

The database is shared between machines, so a run row can say "Serving
bdd-lora-1.5b" while this machine's Ollama has never heard of it. The app checks
the runtime rather than trusting the row, so the toggle will say *"… is no
longer loaded in the model runtime"*. That is the expected message, not a fault
— register the adapter here and it clears.

#### Running the shim

Ollama must already be running with the model registered (above). Run from the
**repo root**, not `backend/`:

```powershell
uv run --project backend python -m uvicorn app:app --app-dir training/serve --port 9000
```

- `--project backend` borrows the backend's venv — the shim has no venv of its own.
- `--app-dir training/serve` is what makes `app:app` resolve to [`training/serve/app.py`](training/serve/app.py).

Its settings are read from `backend/.env` (a real environment variable still
wins), so they live in the same file as the backend they serve — no `export`
lines per start:

| Variable | Default | Purpose |
|---|---|---|
| `FINE_TUNED_OLLAMA_MODEL` | `bdd-lora` | The name used in `ollama create` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama host |
| `FINE_TUNED_ADAPTER_DIR` | `training/outputs/outputs/bdd-lora` | Where `chat_template.jinja` lives — point this at your run directory |
| `SHIM_TIMEOUT_SECONDS` | `10` | Total budget. Must stay under the backend's 12 s in production; local CPU runs need far more |

Then check it is serving the model as trained — a chat-template mismatch degrades
output silently rather than failing:

```powershell
curl.exe -s localhost:9000/health
```

`chat_template.status` must be `match`. `mismatch` means rebuild the Ollama
model; `unknown` means one side could not be read (Ollama down, or
`FINE_TUNED_ADAPTER_DIR` pointing at the wrong run) and is **not** a pass.

Finally point the backend at it, in `backend/.env`:

```ini
BDD_MODEL_PROVIDER=fine_tuned
FINE_TUNED_MODEL_ENDPOINT=http://localhost:9000/
```

The **Use fine-tuned model** toggle in the app is gated on both the shim
answering and Ollama really having that model loaded; when it is disabled, its
subtitle says which of the two is missing. Full walkthrough — training the
adapter, getting it into Ollama, verifying end to end — is in
[docs/finetune.md](docs/finetune.md); a copy-paste run sheet is in
[docs/commands.md](docs/commands.md).

## Run with Docker (all services)

```powershell
docker compose up
```

Starts frontend, backend, and a local Postgres (`db` service). See `docker-compose.yml`.

## Tests

```powershell
# Backend
cd backend
uv run pytest

# Frontend
cd frontend
npx vitest
```

## Project layout

- `backend/` — FastAPI app (`app/main.py`), Alembic migrations, pytest suite
- `frontend/` — Next.js 16 app (App Router), Tailwind, vitest
- `training/` — dataset builder, fine-tune scripts, and the serving shim (`training/serve/`)
- `docker-compose.yml` — local all-in-Docker setup
- `cfn/`, `HOW_TO_DEPLOY.md` — deployment artifacts