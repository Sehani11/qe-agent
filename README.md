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

### Terminal 2 — Frontend (port 3000)

```powershell
cd frontend
npm install                         # one-time
npm run dev
```

- App: http://localhost:3000

The frontend talks to the backend via `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`); CORS is preconfigured for `http://localhost:3000`.

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
- `docker-compose.yml` — local all-in-Docker setup
- `cfn/`, `HOW_TO_DEPLOY.md` — deployment artifacts
