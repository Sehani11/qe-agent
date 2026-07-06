---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/ux-design-specification.md
  - docs/REQUIREMENT.md
  - docs/UPDATED_SCOPE.md
workflowType: 'architecture'
project_name: 'qe-agent-v2'
user_name: 'Chamath'
date: '2026-03-08'
lastStep: 8
status: 'complete'
completedAt: '2026-03-08'
lastUpdated: '2026-03-15'
changeLog:
  - date: '2026-03-15'
    description: 'Updated for UPDATED_SCOPE.md: added fine-tuned model service, expanded RAG pipeline to multi-source, added MCP server integration layer, added Supabase Storage service, added implementation suggestions to verification output, added Confluence integration, updated project structure'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements — 46 FRs across 9 domains:**

| Domain | FRs | Architectural Implication |
|---|---|---|
| Authentication & User Management | FR1–FR4 | Supabase Auth + session validation middleware on every API route |
| Jira Ticket Ingestion | FR5–FR8 | Async background job: fetch → chunk → embed into Pinecone per session |
| RAG Q&A Chat (Module 1) | FR9–FR12 | Vector similarity search + streaming LLM response; per-session history in DB |
| BDD Generation & Editing (Module 2) | FR13–FR18 | Fine-tuned model or LLM prompt → structured Gherkin output; file upload/download handler |
| GitHub Code Verification (Module 3) | FR19–FR27 | Three distinct GitHub fetch strategies + LLM-as-judge + structured verdict output |
| RAG-Enriched Verification Context | FR35–FR38 | Multi-source RAG retrieval during verification; implementation suggestions in output |
| Project Knowledge Base | FR39–FR41 | Confluence/Jira workspace ingestion into Pinecone; namespaced per user/workspace |
| File Storage | FR42–FR44 | Supabase Storage for reports, uploaded files, and test artifacts |
| Traceability & Reporting | FR28–FR30, FR45–FR46 | Report rendering (PDF/CSV) including RAG context and implementation suggestions |
| Session History & Persistence | FR31–FR34 | Full CRUD on user-scoped sessions; per-user data isolation enforced at DB level |

**Non-Functional Requirements — Critical architecture shapers:**

- **NFR-P1 to P6:** Tight time budgets (30s ingest, 10s chat, 30s BDD gen, 60s verification, 10s RAG retrieval). Means async FastAPI endpoints with visible progress feedback in the frontend; streaming is optional and should be used only where it materially improves UX.
- **NFR-S1 to S8:** Strict secrets management + RLS (Row Level Security) in Supabase + Pinecone namespace isolation per session + project knowledge base namespace isolation per user/workspace.
- **NFR-R4, NFR-R6:** LLM provider abstraction interface + independent swappability of fine-tuned model and general LLM.
- **NFR-R5:** Docker Compose for local dev. The backend must be fully containerised.

**From UX Design — Key architectural touch points:**

- `TerminalProgressLog` component requires clear progress feedback, but not necessarily transport-level streaming. In the current Epic 1 implementation, it is driven by standard request lifecycle state plus spinner/progress affordances.
- `VerificationResultRow` — per-scenario results must be delivered progressively (streamed/chunked), not as a single JSON blob after 60s.
- `BDDEditorPanel` — Monaco or CodeMirror integration on the frontend for Gherkin editing.
- Desktop-First, mobile read-only requiring conditional middleware/page guards.

### Scale & Complexity Assessment

- **Complexity Level:** High (greenfield MVP with dual-model architecture)
- **Primary Domain:** Full-stack AI-powered SaaS (Next.js frontend + Python/FastAPI backend + 4 LLM/vector/storage integrations)
- **Estimated Core Architectural Components:** ~12
  1. Auth layer (Supabase + Next.js middleware)
  2. Next.js frontend (App Router + BFF API routes)
  3. FastAPI backend (Python/uv, Dockerised)
  4. Jira integration module
  5. RAG pipeline (Pinecone + LLM) — single-ticket Q&A
  6. Multi-source RAG pipeline (Pinecone + LLM) — project knowledge base
  7. GitHub integration module (3 modes)
  8. LLM provider abstraction layer (general-purpose)
  9. Fine-tuned model service (BDD generation)
  10. Report generation service (PDF/CSV with RAG context)
  11. Storage service (Supabase Storage)
  12. Confluence integration module

### Technical Constraints & Dependencies

- Supabase Auth is the session authority — all FastAPI endpoints must verify JWTs issued by Supabase.
- Pinecone embeddings namespaced `user_id:session_id` for ticket Q&A, and `user_id:workspace` for project knowledge base — enforced at write AND read time.
- LLM provider behind an abstract interface — no `openai.` or `anthropic.` calls directly in business logic.
- Fine-tuned model behind a separate abstraction — allowing swap between fine-tuned model and general LLM for BDD generation.
- All API tokens stored as env vars — frontend must never receive or proxy credentials.
- Full local stack via `docker compose up` — no cloud dependency for development.
- Supabase Storage for file persistence — reports, uploaded .feature files, test artifacts.

### Cross-Cutting Concerns Identified

1. **Progress Feedback for Long Operations:** Required across ingestion, generation, and future verification work. The current Epic 1 implementation uses simple request-state-driven loaders; future features may introduce streaming only if justified.
2. **User-Scoped Data Isolation:** Enforced at Supabase RLS level, Pinecone namespace level, Supabase Storage bucket policies, and FastAPI authorization checks.
3. **Error Taxonomy:** All external API failures (Jira, GitHub, LLM, Confluence) must map to a unified error response format with human-readable messages.
4. **LLM Abstraction:** A provider interface pattern used uniformly across RAG (chat), verification, and (when using general LLM) BDD generation.
5. **Model Selection:** BDD generation may use either a fine-tuned model or the general LLM — configurable via environment variable, with the general LLM as fallback.
6. **Storage Abstraction:** All file operations (upload, download, delete) go through a storage service layer wrapping Supabase Storage.

## Starter Template Evaluation

### Primary Technology Domain

**Full-stack AI SaaS** — dual-project architecture: a Next.js frontend and a FastAPI Python backend running side-by-side via Docker Compose.

### Starter Options Considered

| Project | Option | Verdict |
|---|---|---|
| Frontend | `create-next-app` (official) | ✅ Selected — canonical, maintained, App Router native |
| Backend | Community FastAPI starters | ❌ Too immature — custom scaffold with `uv init` preferred |

### Selected Starter: `create-next-app` (Frontend)

**Initialization Command:**

```bash
npx create-next-app@latest frontend --typescript --tailwind --eslint --app --src-dir --import-alias "@/*" --no-turbopack
npx shadcn@latest init
```

**Architectural Decisions Provided by Starter:**

- **Language & Runtime:** TypeScript (strict mode), Node.js
- **Styling Solution:** Tailwind CSS (`tailwind.config.ts`) + shadcn/ui component library
- **Routing:** Next.js App Router — supports React Server Components, server actions, layouts
- **Code Organization:** `src/` directory with `@/` path aliases
- **Build Tooling:** Webpack (stable, no Turbopack to avoid edge-case incompatibilities at MVP)
- **Development Experience:** ESLint with Next.js config, hot reload, TypeScript strict checking

### Selected Starter: `uv init` (Backend)

**Initialization Command:**

```bash
# Backend
uv init backend --python 3.12
uv add "fastapi[standard]" sqlalchemy asyncpg alembic pinecone python-dotenv httpx pytest ruff supabase

# Frontend additional deps (after create-next-app)
npm install axios @tanstack/react-query @tanstack/react-query-devtools
```

**Architectural Decisions Provided:**

- **Language & Runtime:** Python 3.12, `uv` for dependency management and virtualenvs
- **Server:** Uvicorn (ASGI) — async-native, suitable for long-running requests and any future streaming needs
- **ORM & Migrations:** SQLAlchemy (async) + Alembic
- **Config Management:** Pydantic `BaseSettings` — validates all env vars at startup
- **Code Quality:** Ruff (replaces Black + Flake8 in a single tool)
- **Testing:** Pytest
- **Containerisation:** Dockerfile per service; `docker-compose.yml` at repo root

**Note:** Project initialization using both commands above should be the first two implementation stories (Epic 1, Stories 1 and 2).

## Core Architectural Decisions

### Data Architecture

- **Session Strategy:** SQLAlchemy `AsyncSession` via `get_db()` FastAPI dependency — session-per-request pattern. Pool size configured via `DB_POOL_SIZE` env var.
- **Pinecone Namespace Convention (Ticket Q&A):** `f"{user_id}:{session_id}"` — enforced at both write and query time in the RAG service. No cross-session or cross-user reads possible.
- **Pinecone Namespace Convention (Project Knowledge Base):** `f"{user_id}:knowledge"` — user-scoped knowledge base for Confluence docs, related stories, and project artifacts.
- **Storage Strategy:** Supabase Storage with bucket policies scoped per `user_id`. Buckets: `reports` (PDF/CSV), `feature-files` (uploaded .feature files), `artifacts` (test artifacts).
- **Caching:** No caching layer at MVP. Supabase PostgreSQL is the single source of truth. Deferred to Phase 2.

### Authentication & Security

- **JWT Validation:** Reusable `get_current_user` FastAPI dependency decodes Supabase-issued JWTs using Supabase JWKS. Applied via `Depends(get_current_user)` on all protected routes. Unauthenticated requests → HTTP 401.
- **Credential Storage (Jira/GitHub PATs):** Stored encrypted in Supabase `user_credentials` table (encrypted at rest by Supabase managed platform). Fetched server-side only — never returned to the frontend in any API response.
- **Supabase RLS:** Row Level Security policies enforce that every SELECT/INSERT/UPDATE/DELETE on user data tables is scoped to the authenticated `user_id`.
- **Supabase Storage Policies:** Bucket-level policies enforce per-`user_id` access to uploaded and generated files.

### API & Communication Patterns

- **Current Epic 1 API Pattern:** Standard REST/JSON responses for ingestion and BDD generation. Frontend loader, spinner, and progress-bar states are driven from request lifecycle state rather than transport streaming.
- **API Design:** RESTful, versioned at `/api/v1/`. Auto-documented via FastAPI's built-in OpenAPI (disabled in production via `DISABLE_DOCS=true` env flag).
- **Error Response Envelope:** All domain errors mapped to a consistent JSON structure via a global FastAPI exception handler:
  ```json
  { "error": "JIRA_FETCH_FAILED", "message": "Ticket PRJ-123 not found.", "code": 404 }
  ```

### Frontend Architecture

- **State Management:** React Context (`SessionContext`) for in-flight pipeline state (current step, BDD content, verification results). No external state library at MVP.
- **Auth Operations → Next.js Server Actions:** Login, logout, signup, and session refresh are implemented as Next.js Server Actions (`"use server"`) in `src/app/actions/auth.ts`. Supabase Auth SDK is used server-side only — credentials and session tokens are never exposed to client JavaScript.
- **FastAPI Data Calls → axios + TanStack React Query:** All calls to the Python backend go through typed `axios` instances configured in `src/lib/api/axiosClient.ts`. Data fetching and mutations are wrapped in TanStack React Query hooks in `src/lib/hooks/` (e.g. `useSession()`, `useBDDGeneration()`, `useVerificationResults()`). This provides automatic caching, background refetching, and loading/error states.
- **Progress State Management:** For the current Epic 1 flow, long-running operations use standard axios requests plus component/context loading state. Streaming remains a future option for chat or verification if progressive output becomes necessary.
- **No raw fetch/axios calls in components:** Components consume hooks only — never call API endpoints directly.

### Model Architecture

- **General-Purpose LLM:** Used for verification (code analysis + reasoning), RAG Q&A chat responses, and as a fallback for BDD generation. Accessed via `LLMProvider` abstract interface.
- **Fine-Tuned Model (BDD Generation):** A domain-specific model trained on story-to-test-case pairs. Accessed via a separate `BDDModelProvider` interface. When unavailable, the system falls back to the general-purpose LLM via the `LLMProvider` interface.
- **Model Selection:** Configured via `BDD_MODEL_PROVIDER` env var (`fine_tuned` | `general_llm`). The `bdd_service.py` reads this and routes to the appropriate provider.

### Infrastructure & Deployment

- **Local Development:** Single `docker compose up` orchestrates: Next.js dev server, FastAPI + Uvicorn, Supabase local emulator. Shared `.env` file at repo root.
- **CI/CD:** GitHub Actions — `lint-and-test.yml` runs on every PR: Ruff + Pytest (backend), ESLint + TypeScript type-check (frontend). Deployment pipeline deferred to Phase 2.
- **LLM Provider Abstraction:** `LLMProvider` abstract base class in `app/services/llm/provider.py`. Concrete implementations: `ClaudeProvider`, `OpenAIProvider`. Instantiated by factory function reading `LLM_PROVIDER` env var. No direct SDK calls in business logic ever.
- **BDD Model Provider Abstraction:** `BDDModelProvider` abstract base class in `app/services/bdd_model/provider.py`. Concrete implementations: `FineTunedModelProvider`, `GeneralLLMFallbackProvider`. Instantiated by factory function reading `BDD_MODEL_PROVIDER` env var.
- **Storage Service:** `StorageService` in `app/services/storage_service.py` wrapping Supabase Storage SDK. All file upload/download/delete operations go through this service.

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**
- Supabase Auth JWT validation pattern on FastAPI
- progress feedback architecture for long-running operations
- LLM provider abstraction interface
- BDD model provider abstraction interface
- Pinecone namespace isolation scheme (dual-namespace: ticket + knowledge base)
- Supabase Storage bucket structure and policies

**Deferred Decisions (Post-MVP):**
- Redis caching layer
- Deployment pipeline and hosting finalization
- Async DB operations at scale (connection pooling hardening)
- MCP server integration (Phase 2 — use direct REST API for MVP)
- Fine-tuned model training pipeline infrastructure

## Implementation Patterns & Consistency Rules

### Naming Patterns

**Database (PostgreSQL via SQLAlchemy):**
- Tables: `snake_case` plural — e.g. `sessions`, `chat_messages`, `verification_results`, `knowledge_sources`
- Columns: `snake_case` — e.g. `user_id`, `created_at`, `bdd_content`
- Foreign keys: `{table_singular}_id` — e.g. `session_id`, `user_id`
- Primary keys: always `id` (UUID type, server-generated)

**API Endpoints:**
- Resources: `snake_case` plural nouns — e.g. `/api/v1/sessions`, `/api/v1/sessions/{session_id}/messages`
- Query params: `snake_case` — e.g. `?page_size=`, `?user_id=`
- All IDs in URLs: UUID strings — never integers

**Backend Code (Python):**
- Files/modules: `snake_case` — e.g. `jira_service.py`, `llm_provider.py`, `storage_service.py`
- Classes: `PascalCase` — e.g. `JiraService`, `LLMProvider`, `StorageService`
- Functions/variables: `snake_case` — e.g. `fetch_ticket()`, `session_id`
- Constants: `UPPER_SNAKE_CASE` — e.g. `DEFAULT_CHUNK_SIZE`

**Frontend Code (TypeScript):**
- Component files: `PascalCase.tsx` — e.g. `VerificationResultRow.tsx`
- Hook files: `camelCase.ts` with `use` prefix — e.g. `useBDD.ts`, `useSession.ts`
- Non-component files: `camelCase.ts` — e.g. `axiosClient.ts`
- Types/Interfaces: `PascalCase`, no `I` prefix — e.g. `Session`, `VerificationResult`
- Variables/functions: `camelCase` — e.g. `fetchSession()`, `sessionId`

### Structure Patterns

**Backend (`backend/app/`):**
```
api/v1/           → Route handlers only — no business logic here
services/         → All business logic (jira/, github/, llm/, rag/, confluence/)
services/llm/     → LLMProvider abstract class + ClaudeProvider, OpenAIProvider
services/bdd_model/ → BDDModelProvider abstract class + FineTunedModelProvider, GeneralLLMFallbackProvider
services/storage_service.py → Supabase Storage wrapper
services/confluence_service.py → Confluence API integration
services/knowledge_service.py → Project knowledge base ingestion and retrieval
models/           → SQLAlchemy ORM table definitions
schemas/          → Pydantic request/response schemas
core/             → config.py (BaseSettings), database.py (engine), auth.py (dependency)
tests/            → Pytest tests, mirroring app/ folder structure
```

**Frontend (`frontend/src/`):**
```
app/              → Next.js App Router pages, layouts, loading.tsx, error.tsx
app/actions/      → Next.js Server Actions (auth.ts only)
components/       → Reusable UI components (shadcn base + custom)
lib/api/          → axios client config (axiosClient.ts)
lib/hooks/        → TanStack Query hooks + workflow-specific request helpers
lib/types/        → Shared TypeScript types matching backend schemas
context/          → React Context providers (SessionContext.tsx)
```

### Format Patterns

**API Responses:**
- Success: Direct Pydantic model serialization — no outer wrapper
- Error: `{ "error": "ERROR_CODE", "message": "Human-readable.", "code": 404 }`
- Dates: ISO 8601 UTC strings — e.g. `"2026-03-08T09:41:00Z"`
- JSON fields: `snake_case` from backend; frontend TanStack Query hooks transform to `camelCase` at the axios response interceptor level

**Ingestion Response Format:**
```json
{
  "session_id": "uuid",
  "jira_ticket_id": "PRJ-123",
  "acceptance_criteria": "...",
  "status": "ready_for_bdd"
}
```

**Verification Result Format (Updated):**
```json
{
  "scenario_id": "uuid",
  "status": "fail",
  "justification": "No handler found for expired tokens...",
  "code_reference": {"file": "routes/auth.py", "function": "require_auth", "line": 67},
  "implementation_suggestion": "Add an is_token_expired() check in the @require_auth decorator.",
  "rag_context": [
    {"source": "confluence", "title": "Auth Architecture", "snippet": "..."},
    {"source": "jira", "ticket_id": "PRJ-100", "title": "Token refresh flow", "snippet": "..."}
  ]
}
```

### Mandatory Rules — All AI Agents MUST Follow

1. **Never put business logic in route handlers** — only in `services/`
2. **Never call LLM SDKs directly** — always via `LLMProvider` interface
3. **Never call fine-tuned model SDK directly** — always via `BDDModelProvider` interface
4. **Never return user data without checking `user_id == current_user.id`**
5. **Auth in Next.js = Server Actions only** — never client-side Supabase Auth SDK calls
6. **FastAPI data calls = TanStack React Query hooks only** — never raw fetch/axios in components
7. **Long-running operations must expose clear progress state** — prefer simple request/loader patterns unless progressive server output is genuinely needed
8. **Never use `any` type in TypeScript** — strict types enforced
9. **Never leave a long-running action without user feedback** — provide a loader, progress indicator, or status text while the request is in flight
10. **All file operations go through `StorageService`** — never direct Supabase Storage SDK calls in route handlers or other services

## Project Structure & Boundaries

### Complete Project Directory Structure

```
qe-agent-v2/                          ← Monorepo root
├── .github/
│   └── workflows/
│       └── lint-and-test.yml         ← PR quality gate (Ruff, Pytest, ESLint, tsc)
├── docker-compose.yml                ← Orchestrates frontend, backend, supabase emulator
├── .env.example                      ← Template for all required env vars
├── README.md
│
├── frontend/                         ← Next.js App (create-next-app output)
│   ├── next.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── package.json
│   ├── .env.local                    ← NEXT_PUBLIC_API_URL, Supabase anon key
│   ├── Dockerfile
│   └── src/
│       ├── app/
│       │   ├── layout.tsx            ← Root layout, QueryClientProvider, SessionContext
│       │   ├── page.tsx              ← Dashboard / session history
│       │   ├── login/
│       │   │   └── page.tsx
│       │   ├── session/
│       │   │   └── [sessionId]/
│       │   │       ├── page.tsx      ← Pipeline workspace
│       │   │       └── loading.tsx
│       │   └── actions/
│       │       └── auth.ts           ← Server Actions: signIn, signOut, signUp
│       ├── components/
│       │   ├── ui/                   ← shadcn/ui base components
│       │   ├── pipeline/
│       │   │   ├── PipelineStepper.tsx
│       │   │   ├── TerminalProgressLog.tsx
│       │   │   ├── BDDEditorPanel.tsx
│       │   │   ├── VerificationResultRow.tsx
│       │   │   └── RAGContextPanel.tsx  ← NEW: displays RAG retrieval context
│       │   └── layout/
│       │       ├── Sidebar.tsx
│       │       └── Header.tsx
│       ├── lib/
│       │   ├── api/
│       │   │   └── axiosClient.ts    ← axios instance + snake_case→camelCase interceptor
│       │   ├── hooks/
│       │   │   ├── useSession.ts     ← TanStack Query: fetch/create sessions
│       │   │   ├── useBDD.ts         ← TanStack Query: generate, upload, download BDD
│       │   │   ├── useVerification.ts← TanStack Query: trigger & fetch results
│       │   │   ├── useKnowledge.ts   ← NEW: TanStack Query: knowledge base management
│       │   │   └── useSSEStream.ts   ← Legacy hook from earlier streaming approach (not used by current Epic 1 ingest flow)
│       │   └── types/
│       │       ├── session.ts
│       │       ├── verification.ts
│       │       ├── bdd.ts
│       │       └── knowledge.ts      ← NEW: knowledge base types
│       ├── context/
│       │   └── SessionContext.tsx    ← In-flight pipeline state
│       └── middleware.ts             ← Auth guard: redirect unauthenticated users
│
└── backend/                          ← FastAPI Python service (uv init output)
    ├── pyproject.toml
    ├── uv.lock
    ├── Dockerfile
    ├── alembic.ini
    ├── alembic/
    │   └── versions/                 ← DB migration files
    └── app/
        ├── main.py                   ← FastAPI app init, lifespan, global exception handler
        ├── api/
        │   └── v1/
        │       ├── sessions.py       ← Session CRUD routes
        │       ├── ingestion.py      ← Jira fetch + Pinecone embed (standard JSON response)
        │       ├── chat.py           ← RAG Q&A routes
        │       ├── bdd.py            ← BDD generate/upload/download routes
        │       ├── verification.py   ← GitHub fetch + LLM verify (SSE stream)
        │       ├── reports.py        ← PDF/CSV export routes
        │       └── knowledge.py      ← NEW: Knowledge base ingestion routes
        ├── services/
        │   ├── jira_service.py
        │   ├── github_service.py
        │   ├── rag_service.py
        │   ├── bdd_service.py
        │   ├── verification_service.py
        │   ├── report_service.py
        │   ├── storage_service.py    ← NEW: Supabase Storage wrapper
        │   ├── confluence_service.py ← NEW: Confluence API integration
        │   ├── knowledge_service.py  ← NEW: Project knowledge base service
        │   ├── llm/
        │   │   ├── provider.py       ← LLMProvider ABC
        │   │   ├── claude_provider.py
        │   │   ├── openai_provider.py
        │   │   └── factory.py        ← Reads LLM_PROVIDER env var
        │   └── bdd_model/            ← NEW: Fine-tuned model abstraction
        │       ├── provider.py       ← BDDModelProvider ABC
        │       ├── fine_tuned_provider.py
        │       ├── general_llm_fallback.py
        │       └── factory.py        ← Reads BDD_MODEL_PROVIDER env var
        ├── models/
        │   ├── session.py
        │   ├── chat_message.py
        │   ├── bdd_file.py
        │   ├── verification_result.py
        │   ├── user_credential.py
        │   └── knowledge_source.py   ← NEW: Knowledge base source tracking
        ├── schemas/
        │   ├── session.py
        │   ├── bdd.py
        │   ├── verification.py
        │   └── knowledge.py          ← NEW: Knowledge base schemas
        ├── core/
        │   ├── config.py             ← Pydantic BaseSettings
        │   ├── database.py           ← AsyncEngine, get_db() dependency
        │   └── auth.py               ← get_current_user() dependency (JWT decode)
        └── tests/
            ├── test_sessions.py
            ├── test_ingestion.py
            ├── test_bdd.py
            ├── test_verification.py
            └── test_knowledge.py     ← NEW: Knowledge base tests
```

### Requirements to Structure Mapping

| FR Domain | Backend Location | Frontend Location |
|---|---|---|
| Auth (FR1–4) | `core/auth.py` + Supabase JWT | `app/actions/auth.ts` + `middleware.ts` |
| Jira Ingestion (FR5–8) | `api/v1/ingestion.py` + `services/jira_service.py` | `app/session/[sessionId]/page.tsx` + `TerminalProgressLog` |
| RAG Q&A (FR9–12) | `api/v1/chat.py` + `services/rag_service.py` | `useSession.ts` |
| BDD Gen & Edit (FR13–18) | `api/v1/bdd.py` + `services/bdd_service.py` + `services/bdd_model/` | `useBDD.ts` + `BDDEditorPanel` |
| GitHub Verification (FR19–27) | `api/v1/verification.py` + `services/verification_service.py` | `useVerification.ts` + `VerificationResultRow` |
| RAG-Enriched Verification (FR35–38) | `services/knowledge_service.py` + `services/verification_service.py` | `RAGContextPanel` + `VerificationResultRow` |
| Project Knowledge Base (FR39–41) | `api/v1/knowledge.py` + `services/knowledge_service.py` + `services/confluence_service.py` | `useKnowledge.ts` |
| File Storage (FR42–44) | `services/storage_service.py` | Download triggers in hooks |
| Reports (FR28–30, FR45–46) | `api/v1/reports.py` + `services/report_service.py` | `useVerification.ts` (download trigger) |
| Session History (FR31–34) | `api/v1/sessions.py` + `models/session.py` | `useSession.ts` + Dashboard `page.tsx` |

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:**
All technology choices are mutually compatible:
- Next.js App Router ↔ Server Actions ↔ TanStack React Query — standard Next.js pattern with no conflicts
- FastAPI + Uvicorn (ASGI) ↔ standard REST/JSON patterns — simple, stable, and fully aligned with current Epic 1 needs
- Supabase Auth JWT ↔ FastAPI JWKS decode — well-established integration pattern
- SQLAlchemy AsyncSession ↔ asyncpg ↔ Supabase PostgreSQL — fully async chain, no blocking calls
- Pinecone SDK ↔ Python 3.12 — fully supported
- Supabase Storage SDK ↔ Python — fully supported
- Dual-model architecture (fine-tuned + general LLM) ↔ provider abstraction pattern — clean separation

**Pattern Consistency:**
- Naming conventions are consistent across Python (`snake_case`) and TypeScript (`camelCase`) with transformation at the axios interceptor boundary — clean separation
- Current Epic 1 uses a uniform request-state-driven loader pattern for ingestion and generation; future streaming remains optional for later epics
- LLM abstraction applied to all LLM use cases — no direct SDK calls leaked
- BDD model abstraction applied cleanly with fallback to general LLM
- Storage abstraction applied to all file operations

**Structure Alignment:**
The project structure fully supports these patterns, clearly delineating route handlers from services in the Python backend, and Server Actions from component hooks in the Next.js frontend.

### Requirements Coverage Validation ✅

**Functional Requirements Coverage:**
- All 46 FRs are successfully mapped to specific physical files in both frontend and backend
- All 9 domains have explicitly defined technical homes in the repository

**Non-Functional Requirements Coverage:**
- Tightly constrained time budgets (NFR-P1–P6) are currently addressed through concise request/response APIs paired with clear loading feedback in the UI
- Stringent security requirements (NFR-S1–S8) handled via Next.js Server Actions, stateless JWT passing to FastAPI, row-level security, and storage bucket policies
- Future flexibility (NFR-R4, NFR-R6) guaranteed by the dual provider abstraction (`LLMProvider` + `BDDModelProvider`)

### Implementation Readiness Validation ✅

**Decision Completeness:**
All critical decisions documented with clear rationale. The foundational `npm` and `uv` commands are predefined and ready for the dev agent.

**Structure Completeness:**
The complete directory tree for both projects is provided down to the file level, leaving no structural ambiguity.

**Pattern Completeness:**
10 mandatory implementation rules have been captured directly impacting the prompt context for any implementation agent.

### Gap Analysis Results

**Non-blocking Deferred Decisions:**
- Database schema column definitions — Best deferred to Epic 1 database migration implementation stories
- Specific code editor component selection — E.g. Monaco vs CodeMirror; deferred to BDD Generation implementation story
- Fine-tuned model hosting and serving infrastructure — Deferred to Phase 2 training pipeline epic
- MCP server integration specifics — Deferred to Phase 2; direct REST API used for MVP
- Confluence API authentication method — Deferred to knowledge base epic implementation

### Architecture Completeness Checklist

**✅ Requirements Analysis**
- [x] Project context thoroughly analyzed
- [x] Scale and complexity assessed
- [x] Technical constraints identified
- [x] Cross-cutting concerns mapped

**✅ Architectural Decisions**
- [x] Critical decisions documented with rationale
- [x] Technology stack fully specified
- [x] Integration patterns defined
- [x] Scale and performance considerations addressed
- [x] Dual-model architecture (fine-tuned + general LLM) defined
- [x] Multi-source RAG architecture defined
- [x] Storage service architecture defined

**✅ Implementation Patterns**
- [x] Naming conventions established
- [x] Structure patterns defined
- [x] Communication patterns specified
- [x] Mandatory agent rules documented (10 rules)

**✅ Project Structure**
- [x] Complete directory structure defined
- [x] Component boundaries established
- [x] Integration points mapped
- [x] Requirements to structure mapping complete

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** HIGH

**Key Strengths:**
- Extremely precise project structure — zero ambiguity for dev agents
- Clean auth boundary preventing common Next.js security anti-patterns
- A clear long-running-operation feedback pattern was established before coding, with simple JSON APIs now preferred for Epic 1
- Robust dual-provider abstraction (LLM + BDD model)
- Multi-source RAG architecture with clear namespace isolation
- Storage service abstraction for all file operations

### Implementation Handoff

**AI Agent Guidelines:**
- Follow all 10 mandatory architectural decisions exactly as documented
- Use the defined implementation patterns strictly
- Respect project structure boundaries and naming standards
- Refer back to this artifact whenever structurally unsure
