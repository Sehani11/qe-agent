---
stepsCompleted: [1, 2, 3, 4]
status: 'complete'
completedAt: '2026-03-08'
lastUpdated: '2026-08-22'
inputDocuments: 
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/ux-design-specification.md
  - docs/UPDATED_SCOPE.md
changeLog:
  - date: '2026-03-15'
    description: 'Updated for UPDATED_SCOPE.md: added Epic 5 (Project Knowledge Base & RAG Enrichment), added Epic 6 (Fine-Tuned Model Integration), expanded Epic 3 verification stories with implementation suggestions and RAG context, added new FRs (FR35-FR46), updated FR coverage map'
  - date: '2026-08-08'
    description: 'Epic 6 expanded: renamed to Fine-Tuned Model Training & Integration; added Story 6.4 (Training Data Capture, ships early), 6.5 (Fine-Tuning Dataset & Model Training), 6.6 (Training-Data Opt-Out Control) and 6.7 (Manual Training Dataset Upload). Execution order is 6.4 → 6.6 → 6.7 → 6.5 → 6.2 → 6.3, which is not numeric order.'
  - date: '2026-08-22'
    description: 'Epic 6: added Story 6.8 (Product-Domain Training Corpus) and Story 6.9 (Model Comparison UI & Saved Evaluation Runs), both post-hoc. The work shipped 2026-08-16/17 outside the BMAD workflow and was documented retrospectively; their acceptance criteria are a reconstruction of what the implementation had to satisfy, not criteria agreed in advance.'
  - date: '2026-04-05'
    description: 'Reordered epics: Epic 2 is now GitHub Code Verification (no auth, DEV_USER_ID stub), Epic 3 is Auth & Session Persistence, Epic 4 is Knowledge Base & RAG Enrichment, Epic 5 is RAG Chat & Traceability Reporting. Story numbers updated throughout.'
---

# qe-agent-v2 - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for qe-agent-v2, decomposing the requirements from the PRD, UX Design if it exists, and Architecture requirements into implementable stories.

## Requirements Inventory

### Functional Requirements

- FR1: A visitor can register for an account using email and password
- FR2: A visitor can sign up or log in using an OAuth provider
- FR3: An authenticated user can log out of their session
- FR4: An unauthenticated user is denied access to all application features
- FR5: An authenticated user can submit a Jira ticket URL or ticket ID to ingest it
- FR6: The system can fetch ticket content from Jira (Summary, Description, Acceptance Criteria, Labels, Linked Issues) using the user's Jira credentials
- FR7: The system can store and index ticket content as vector embeddings for retrieval
- FR8: The system notifies the user with a clear, actionable message when Jira ingestion fails (invalid ticket, auth failure, API unavailability)
- FR9: An authenticated user can ask natural-language questions about an ingested Jira ticket via a chat interface
- FR10: The system answers user questions using only content retrieved from the ingested ticket — no cross-session data, no external knowledge
- FR11: The system maintains chat history per session, associated with the authenticated user
- FR12: An authenticated user can view past chat history for a previously ingested ticket session
- FR13: An authenticated user can trigger BDD generation from an ingested Jira ticket's acceptance criteria
- FR14: The system produces Gherkin scenarios (Feature / Scenario / Given / When / Then) with each scenario traceable to a specific acceptance criteria clause
- FR15: An authenticated user can view and edit generated BDD content directly in the browser
- FR16: An authenticated user can download the current BDD content as a `.feature` file
- FR17: An authenticated user can download a structured CSV test case file derived from the current BDD content
- FR18: An authenticated user can upload an existing `.feature` file to use as BDD input, bypassing generation
- FR19: An authenticated user can select one of three GitHub source input modes: Exact File Paths, Full Repository, or Pull Request
- FR20: In Exact File Paths mode, the user can specify one or more file paths in a GitHub repository to fetch for verification
- FR21: In Full Repository mode, the user can provide a GitHub repository URL and the system fetches the full file tree and contents
- FR22: In Pull Request mode, the user can provide a PR link and the system fetches the PR diff/patch
- FR23: The system sends fetched code and BDD scenarios to an LLM to evaluate whether each scenario is covered by the code
- FR24: The system displays a per-scenario pass/fail verdict with a natural-language justification referencing specific code sections
- FR25: The system displays an overall pass percentage across all verified scenarios
- FR26: The system notifies the user with a clear, actionable message when a GitHub source fetch fails, preserving session state
- FR27: A failed verification attempt does not destroy BDD content or session state
- FR28: The system produces a traceability report linking each acceptance criteria clause to its BDD scenario and verification outcome
- FR29: An authenticated user can export the verification report as a PDF file
- FR30: An authenticated user can export the verification report as a CSV file
- FR31: The system persists all ticket sessions, chat history, BDD content, and verification results to a database associated with the authenticated user
- FR32: An authenticated user can view a list of their past sessions
- FR33: An authenticated user can retrieve and view a previously saved session, including its BDD content and verification report
- FR34: An authenticated user can only access their own sessions and reports — not those of other users
- FR35: During verification, the system retrieves contextually relevant information from the project knowledge base via RAG
- FR36: The verification output includes the RAG retrieval results alongside the per-scenario verdicts
- FR37: The verification output includes the GitHub repository links and file references used during code analysis
- FR38: For failed scenarios, the system provides implementation suggestions enabling developers to review potential improvements
- FR39: An authenticated user can ingest project knowledge sources (Confluence pages, related Jira stories) into the vector database
- FR40: Ingested project knowledge is indexed and namespaced per user/workspace to prevent cross-user data leakage
- FR41: The system can retrieve contextually relevant knowledge from the project knowledge base during verification
- FR42: Generated reports (PDF/CSV) are stored in a storage bucket and accessible for download
- FR43: Uploaded `.feature` files are stored in a storage bucket linked to the session
- FR44: Test artifacts produced during verification are persisted in a storage bucket
- FR45: The exported report includes RAG retrieval context used during verification
- FR46: The exported report includes implementation suggestions for failed scenarios

### NonFunctional Requirements

- NFR-P1: Jira ticket ingestion completes within 30 seconds for a typical ticket, with a visible progress indicator
- NFR-P2: RAG chatbot returns an answer within 10 seconds under normal operating conditions
- NFR-P3: BDD generation response returned within 30 seconds for a ticket with up to 20 AC clauses
- NFR-P4: GitHub code verification report returned within 60 seconds for up to 15 BDD scenarios vs up to 10k lines of code
- NFR-P5: The application UI remains responsive during all long-running background operations via non-blocking loading and progress states; streaming is optional, not mandatory
- NFR-P6: Project knowledge base RAG retrieval completes within 10 seconds during verification
- NFR-S1: All credentials stored and transmitted exclusively via environment variables or encrypted secrets
- NFR-S2: All data in transit between client, backend, and external APIs encrypted via TLS
- NFR-S3: All data at rest in the database encrypted at rest by Supabase
- NFR-S4: Vector embeddings namespaced per session (`{user_id}:{session_id}`)
- NFR-S5: All authenticated API endpoints validate the user's session JWT before processing
- NFR-S6: Row Level Security enforces that user's session data is inaccessible to other users
- NFR-S7: Internal error details are never exposed in API responses
- NFR-S8: Project knowledge base embeddings namespaced per user/workspace — no cross-user retrieval
- NFR-R1: Provide clear, human-readable error messages for external API failures (no raw error codes)
- NFR-R2: GitHub API rate-limit responses (HTTP 429) handled gracefully with retry logic/messages
- NFR-R3: A failed verification attempt does not destroy or overwrite BDD content or session state
- NFR-R4: LLM provider integration is abstracted behind an LLMProvider interface module
- NFR-R5: Application runs correctly locally via a single `docker compose up` command
- NFR-R6: Fine-tuned model and general LLM are independently swappable

### Additional Requirements

- **Starter Templates for Epic 1**: 
  - Next.js must be scaffolded using `create-next-app` with Tailwind, shadcn, and specific frontend dependencies (`axios`, `@tanstack/react-query`).
  - FastAPI backend must be scaffolded using `uv init` with specific packages (`fastapi`, `sqlalchemy`, `pinecone`, `ruff`, etc.).
- **Auth Pattern**: Login, logout, sign up, and session persistence must use Next.js Server Actions.
- **Frontend API Pattern**: Data fetching from the Python API must use TanStack Query hooks + axios.
- **Progress UI**: Must implement clear loading and progress feedback for long operations; Epic 1 no longer requires SSE hooks for Jira ingestion.
- **Editor UI**: Must use a robust code editor like Monaco Editor or CodeMirror for BDD output editing.
- **Data Persistence Strategy**: Supabase PostgreSQL single source of truth; no Redis caching at MVP. Supabase Storage for file artifacts.
- **Layout Requirements**: Responsive desktop-first layout; mobile view will be restricted or read-only at MVP.
- **Model Architecture**: BDD generation supports both fine-tuned model and general LLM, configurable via `BDD_MODEL_PROVIDER` env var.
- **Storage**: All file operations (reports, .feature files, artifacts) go through `StorageService` wrapping Supabase Storage.

### FR Coverage Map

- FR1: Epic 3 - Email/password registration via Supabase Auth Server Action
- FR2: Epic 3 - OAuth login via Supabase Auth Server Action
- FR3: Epic 3 - Logout via Supabase Auth Server Action
- FR4: Epic 3 - Next.js middleware redirects unauthenticated users; FastAPI returns HTTP 401
- FR5: Epic 1 - Jira ticket URL/ID submission form and API endpoint
- FR6: Epic 1 - FastAPI Jira service fetches ticket content via Jira REST API v3
- FR7: Epic 1 - Pinecone embedding and indexing via RAG service (namespaced with DEV_USER_ID stub in Epic 1)
- FR8: Epic 1 - Jira ingestion error handling with actionable user-facing messages
- FR9: Epic 5 - RAG Q&A chat interface and FastAPI chat endpoint
- FR10: Epic 5 - Pinecone-scoped retrieval constrained to single session namespace
- FR11: Epic 5 - Chat history persisted per session in DB
- FR12: Epic 5 - Past chat history retrieval from session view
- FR13: Epic 1 - BDD generation trigger; FastAPI BDD service endpoint (story before editor)
- FR14: Epic 1 - LLM-generated Gherkin with AC clause traceability
- FR15: Epic 1 - Monaco/CodeMirror BDD editor panel (story after BDD API story)
- FR16: Epic 1 - `.feature` file download from BDD editor
- FR17: Epic 1 - CSV test case file download from BDD editor
- FR18: Epic 1 - `.feature` file upload bypassing generation
- FR19: Epic 2 - GitHub source mode selector (Exact Files / Full Repo / Pull Request)
- FR20: Epic 2 - Exact File Paths mode: user specifies paths; FastAPI fetches files
- FR21: Epic 2 - Full Repository mode: repo URL input; FastAPI fetches full file tree
- FR22: Epic 2 - Pull Request mode: PR link input; FastAPI fetches PR diff
- FR23: Epic 2 - LLM verification of BDD scenarios against fetched code
- FR24: Epic 2 - Per-scenario pass/fail verdict with code-level justification
- FR25: Epic 2 - Overall pass percentage display
- FR26: Epic 2 - GitHub fetch error handling; session state preserved on failure
- FR27: Epic 2 - Failed verification does not destroy BDD content or session state
- FR28: Epic 5 - Traceability report linking AC → BDD → verification outcome (independent story track)
- FR29: Epic 5 - PDF report export (independent story track)
- FR30: Epic 5 - CSV report export (independent story track)
- FR31: Epic 3 - All session data persisted to Supabase PostgreSQL per user_id
- FR32: Epic 3 - Session history dashboard listing past sessions
- FR33: Epic 3 - Session retrieval view with BDD content and report
- FR34: Epic 3 - RLS enforcement: users access only their own sessions
- FR35: Epic 4 - RAG retrieval from project knowledge base during verification
- FR36: Epic 4 - RAG retrieval results included in verification output
- FR37: Epic 2 - GitHub repo links and file references included in verification output
- FR38: Epic 2 - Implementation suggestions for failed scenarios
- FR39: Epic 4 - Project knowledge source ingestion (Confluence, Jira workspace)
- FR40: Epic 4 - Knowledge base namespace isolation per user/workspace
- FR41: Epic 4 - Contextual knowledge retrieval during verification
- FR42: Epic 3 - Reports stored in Supabase Storage bucket
- FR43: Epic 3 - Uploaded .feature files stored in Supabase Storage
- FR44: Epic 3 - Test artifacts persisted in Supabase Storage
- FR45: Epic 5 - Exported report includes RAG retrieval context
- FR46: Epic 5 - Exported report includes implementation suggestions

## Epic List

### Epic 1: Core Platform & BDD Generation Pipeline
Establish the technical foundation of the monorepo app, implement Jira ticket ingestion with Pinecone vectorisation, and deliver working BDD generation, editing, and export — using a `DEV_USER_ID` environment stub in place of real auth. This epic must be internally ordered so that the FastAPI BDD generation service is built and tested before the frontend BDD editor story begins.
**FRs covered:** FR5, FR6, FR7, FR8, FR13, FR14, FR15, FR16, FR17, FR18
**Architecture requirements:** Next.js scaffold (`create-next-app`), FastAPI scaffold (`uv init`), Docker Compose wiring, `DEV_USER_ID=dev-stub` env var placeholder for Pinecone namespacing
**Status:** ✅ Complete

### Epic 2: GitHub Code Verification (No Auth)
Enable users to select a GitHub source (specific files, full repository, or pull request diff), trigger LLM-based per-scenario BDD verification against fetched BDD scenarios, view pass/fail verdicts with code-level justifications and implementation suggestions — all using the `DEV_USER_ID` stub from Epic 1. No auth or RAG in this epic.
**FRs covered:** FR19, FR20, FR21, FR22, FR23, FR24, FR25, FR26, FR27, FR37, FR38

### Epic 3: User Authentication & Session Persistence
Secure the platform with Supabase Auth (email/password and OAuth), apply Next.js middleware route protection, wire FastAPI JWT validation, persist all user sessions, BDD content, and verification reports to Supabase PostgreSQL with RLS data isolation. Set up Supabase Storage buckets for file persistence.
**FRs covered:** FR1, FR2, FR3, FR4, FR31, FR32, FR33, FR34, FR42, FR43, FR44

### Epic 4: Project Knowledge Base & RAG Enrichment
Enable ingestion of project knowledge sources (Confluence documentation, related Jira stories, architecture notes) into the vector database, and integrate contextual RAG retrieval into the verification pipeline to enrich LLM analysis with real project knowledge.
**FRs covered:** FR35, FR36, FR39, FR40, FR41
**Dependencies:** Epic 2 (verification pipeline must exist) and Epic 3 (auth required for user-namespaced knowledge base)

### Epic 5: Ticket Comprehension (RAG Q&A) & Traceability Reporting
Allow users to interrogate ingested Jira tickets through a RAG-powered chatbot (grounded exclusively in the ticket content), and produce downloadable traceability reports linking AC clauses → BDD scenarios → verification outcomes as PDF and CSV — including RAG retrieval context and implementation suggestions.
**FRs covered:** FR9, FR10, FR11, FR12, FR28, FR29, FR30, FR45, FR46
**Note:** This epic contains two independent story tracks (RAG chatbot and Report generation) that can be developed in parallel — they share no code.

### Epic 6: Fine-Tuned Model Training & Integration (Phase 2)
Capture real story-to-test-case training pairs, train a fine-tuned domain-specific model for BDD test case generation, and integrate it behind a provider abstraction that allows seamless switching between the fine-tuned model and the general-purpose LLM. Includes evaluation pipeline for systematic comparison.
**FRs covered:** FR13, FR14 (enhanced)
**Dependencies:** Epic 1 (BDD generation API must exist). Model training runs on external GPU compute (Kaggle/Colab); all datasets, configs and scripts are versioned in this repo.
**Note:** This epic is Phase 2 scope and the general LLM continues to serve BDD generation in MVP — **except Story 6.4, which should ship early**. Training pairs cannot be captured retroactively, so every release without it permanently loses data. The `BDDModelProvider` abstraction (6.1) is already in place.
**Execution order:** 6.4 → 6.6 → 6.7 → 6.5 → 6.2 → 6.3 → 6.9 → 6.8 (not numeric order; see sprint-status.yaml). 6.8 and 6.9 were added post-hoc on 2026-08-22 for work that shipped 2026-08-16/17 outside the BMAD workflow.

---

## Epic 1: Core Platform & BDD Generation Pipeline

Establish the technical foundation of the monorepo app, implement Jira ticket ingestion with Pinecone vectorisation, and deliver working BDD generation, editing, and export — using a `DEV_USER_ID` environment stub in place of real auth.

**Status:** ✅ Complete — All 6 stories done.

### Story 1.1: Next.js Frontend Scaffold ✅ Done

As a **developer**,
I want the Next.js frontend project bootstrapped with the full agreed toolchain,
So that all future frontend stories have a consistent, production-ready foundation.

**Acceptance Criteria:**

**Given** the monorepo root directory exists
**When** the scaffold command is run (`npx create-next-app@latest frontend --typescript --tailwind --eslint --app --src-dir --import-alias "@/*" --no-turbopack`)
**Then** a `frontend/` directory is created with TypeScript, Tailwind CSS, ESLint, and App Router configured

**And** `axios`, `@tanstack/react-query`, and `@tanstack/react-query-devtools` are installed
**And** `npx shadcn@latest init` is run to initialize the shadcn/ui component library
**And** a `Dockerfile` for the frontend dev server is created
**And** a `.env.local.example` with `NEXT_PUBLIC_API_URL` and Supabase anon key placeholders exists
**And** `npm run dev` starts the Next.js dev server on port 3000 without errors

---

### Story 1.2: FastAPI Backend Scaffold ✅ Done

As a **developer**,
I want the FastAPI Python backend scaffolded with the full agreed toolchain and wired into Docker Compose,
So that all future backend stories have a working, containerized service to build on.

**Acceptance Criteria:**

**Given** `uv` is installed
**When** `uv init backend --python 3.12` is run and all required packages are added (`fastapi[standard]`, `sqlalchemy`, `asyncpg`, `alembic`, `pinecone`, `python-dotenv`, `httpx`, `pytest`, `ruff`)
**Then** a `backend/` directory exists with `pyproject.toml`, `uv.lock`, and the full `app/` directory structure (`api/v1/`, `services/`, `models/`, `schemas/`, `core/`)

**And** `backend/app/main.py` initializes a FastAPI app with a `/health` GET endpoint returning `{"status": "ok"}`
**And** `backend/app/core/config.py` contains a Pydantic `BaseSettings` class reading all required env vars (including `DEV_USER_ID`)
**And** `backend/app/services/llm/provider.py` contains the `LLMProvider` abstract base class and `factory.py` reads the `LLM_PROVIDER` env var
**And** a `Dockerfile` for the FastAPI service and a `docker-compose.yml` at the repo root exist
**And** `docker compose up` starts both services without errors and `/health` returns 200

---

### Story 1.3: BDD Generation API ✅ Done

As an **authenticated user** (stubbed as `DEV_USER_ID` in this epic),
I want a backend API endpoint that accepts Jira acceptance criteria and returns Gherkin BDD scenarios,
So that the LLM-powered BDD generation capability is functional and testable independently of the UI.

**Acceptance Criteria:**

**Given** a POST request to `/api/v1/bdd/generate` with a JSON body containing `session_id` and `acceptance_criteria` text
**When** the FastAPI BDD service sends the AC to the LLM provider via the `LLMProvider` interface
**Then** the response returns a valid JSON object containing a list of Gherkin scenarios (Feature / Scenario / Given / When / Then format)

**And** each scenario includes a `source_ac_clause` field tracing it to a specific AC clause (FR14)
**And** the LLM is called via the `LLMProvider` ABC — never directly via `openai.` or `anthropic.` SDK
**And** a Pytest test in `backend/tests/test_bdd.py` confirms the endpoint returns a valid Gherkin structure against a mocked LLM response
**And** if the LLM call fails the endpoint returns `{"error": "BDD_GENERATION_FAILED", "message": "...", "code": 500}` with no raw exception details (NFR-S7, NFR-R1)
**And** BDD generation completes within 30 seconds for a ticket with up to 20 AC clauses (NFR-P3)

---

### Story 1.4: Jira Ticket Ingestion ✅ Done

As an **authenticated user** (stubbed as `DEV_USER_ID`),
I want to submit a Jira ticket URL or ID and have the ticket content fetched, chunked, and embedded into Pinecone,
So that the ticket is indexed and ready for BDD generation and future RAG Q&A.

**Acceptance Criteria:**

**Given** a POST request to `/api/v1/ingestion/ingest` with a valid Jira ticket URL or ID
**When** the FastAPI Jira service fetches the ticket content (Summary, Description, Acceptance Criteria, Labels, Linked Issues) using the user's Jira credentials
**Then** the ticket content is chunked and embedded into Pinecone under namespace `DEV_USER_ID:{session_id}` (NFR-S4)

**And** the endpoint returns a standard JSON response containing `session_id`, `jira_ticket_id`, `acceptance_criteria`, and a ready-for-BDD status on success (NFR-P5)
**And** a `sessions` table row is created in the database with `session_id` and `jira_ticket_id`
**And** if Jira fetch fails (invalid ticket, auth failure, API error) the endpoint returns a structured JSON error with a human-readable message — no raw error codes (FR8, NFR-R1, NFR-S7)
**And** ingestion completes within 30 seconds for a typical ticket up to 5,000 words (NFR-P1)

---

### Story 1.5: BDD Editor Panel ✅ Done

As an **authenticated user** (stubbed as `DEV_USER_ID`),
I want to view and edit the generated BDD content in an in-browser code editor,
So that I can review, adjust, and correct any Gherkin scenarios before downloading or verifying.

**Acceptance Criteria:**

**Given** the BDD generation API (Story 1.3) has returned Gherkin scenarios for a session
**When** the user navigates to the pipeline workspace page (`/session/[sessionId]`)
**Then** the `BDDEditorPanel` component renders the Gherkin content in Monaco Editor or CodeMirror with Gherkin syntax highlighting (FR15)

**And** the user can edit any line of the BDD content directly in the editor
**And** changes are captured in local React state via `SessionContext` and reflected immediately
**And** the editor is read-only on mobile viewports (desktop-first requirement)
**And** the session page's request lifecycle state feeds simplified ingestion and generation progress into the adjacent `TerminalProgressLog` component (NFR-P5)

---

### Story 1.6: BDD File Downloads & Upload ✅ Done

As an **authenticated user** (stubbed as `DEV_USER_ID`),
I want to download the BDD content as a `.feature` file or CSV, and upload an existing `.feature` file to replace generated BDD,
So that I can integrate BDD-AutoGen's output into my existing test tooling or bypass AI generation entirely.

**Acceptance Criteria:**

**Given** BDD content exists in the `BDDEditorPanel`
**When** the user clicks "Download .feature"
**Then** the current editor content is downloaded as a UTF-8 `.feature` file named `{ticket_id}.feature` (FR16)

**And given** the user clicks "Download CSV"
**When** the BDD content is parsed into scenario rows
**Then** a CSV file downloads with columns: Scenario, Given, When, Then, Expected Result (FR17)

**And given** the user clicks "Upload .feature"
**When** a valid `.feature` file is selected and uploaded
**Then** the BDD editor content is replaced with the uploaded file's content, bypassing LLM generation entirely (FR18)

---

## Epic 3: User Authentication & Session Persistence

Secure the platform with Supabase Auth (email/password and OAuth), apply Next.js middleware route protection, wire FastAPI JWT validation, persist all user sessions, BDD content, and verification reports to Supabase PostgreSQL with RLS data isolation. Set up Supabase Storage buckets for file persistence.

> **Note:** Epic 2 (GitHub Code Verification) must be complete before this epic begins, so that the DEV_USER_ID stub can be replaced with real JWT-based user_id across all services.

### Story 3.1: Supabase Auth — Login & Registration

As a **visitor**,
I want to register with email/password or sign in via OAuth,
So that I can access the platform and have all my data associated with my account.

**Acceptance Criteria:**

**Given** the user visits `/login`
**When** they complete the email/password registration form and submit
**Then** a new account is created via the `signUp` Next.js Server Action in `src/app/actions/auth.ts` using the Supabase Auth SDK server-side (FR1)

**And given** the user clicks the OAuth provider button (e.g. Google)
**When** the OAuth flow completes
**Then** the user is authenticated via the `signInWithOAuth` Server Action and redirected to the dashboard (FR2)

**And given** an authenticated user clicks "Log out"
**When** the `signOut` Server Action is called
**Then** the session is terminated and the user is redirected to `/login` (FR3)

**And** the Supabase Auth SDK is **only** called inside Server Actions — never in client components
**And** no auth tokens or session secrets are ever exposed to client-side JavaScript

---

### Story 3.2: FastAPI JWT Validation & Route Protection

As an **authenticated user**,
I want all FastAPI API endpoints to validate my session token,
So that unauthenticated users cannot access any backend data or operations.

**Acceptance Criteria:**

**Given** the `get_current_user` dependency in `backend/app/core/auth.py` is applied to all protected routes
**When** a request arrives with a valid Supabase-issued JWT in the `Authorization: Bearer` header
**Then** the dependency decodes the JWT using Supabase's JWKS and returns the authenticated `user_id` (NFR-S5)

**And** when a request arrives without a token or with an invalid/expired token
**Then** the endpoint returns HTTP 401 with `{"error": "UNAUTHORIZED", "message": "Authentication required.", "code": 401}` (FR4)

**And** the `DEV_USER_ID` stub from Epic 1 is replaced by the real `user_id` extracted from the validated JWT across all services
**And** Next.js `middleware.ts` redirects unauthenticated users to `/login` before any page renders (FR4)
**And** a Pytest test verifies that a request without a token to any protected route returns HTTP 401

---

### Story 3.3: Session Persistence & Database Schema

As an **authenticated user**,
I want all my sessions, BDD content, and verification results automatically saved,
So that my work is never lost and I can return to it at any time.

**Acceptance Criteria:**

**Given** a user completes Jira ingestion
**When** the session is created in the backend
**Then** a row is inserted into the `sessions` table with `id`, `user_id`, `jira_ticket_id`, `created_at` (FR31)

**And** when BDD content is generated or uploaded, it is persisted to the `bdd_files` table linked to `session_id` and `user_id`
**And** Supabase RLS policies are applied to `sessions`, `bdd_files`, `chat_messages`, and `verification_results` tables so all operations are scoped to `auth.uid() = user_id` (FR34, NFR-S6)
**And** Alembic migration files are created for all tables
**And** a user cannot read another user's session data even if the `session_id` is known

---

### Story 3.4: Supabase Storage Setup

As an **authenticated user**,
I want my generated reports, uploaded feature files, and test artifacts to be stored persistently,
So that I can download them at any time and they're not lost between sessions.

**Acceptance Criteria:**

**Given** the Supabase Storage service is configured
**When** the backend `StorageService` in `app/services/storage_service.py` is implemented
**Then** it wraps all Supabase Storage SDK operations (upload, download, delete, list) behind a clean interface

**And** three storage buckets are created: `reports`, `feature-files`, `artifacts`
**And** bucket-level policies enforce per-`user_id` access — a user can only read/write their own files (FR42, FR43, FR44)
**And** uploaded `.feature` files are stored in the `feature-files` bucket linked to `session_id`
**And** generated PDF/CSV reports are stored in the `reports` bucket linked to `session_id`
**And** all file operations in route handlers and services go through `StorageService` — never direct SDK calls

---

### Story 3.5: Session History Dashboard

As an **authenticated user**,
I want to view a list of my past sessions and open any of them to review their BDD and results,
So that I can audit, reference, and continue work on previously processed tickets.

**Acceptance Criteria:**

**Given** the user navigates to `/` (dashboard)
**When** the `useSession()` TanStack Query hook fetches `GET /api/v1/sessions`
**Then** a list of the user's past sessions is displayed, each showing Jira ticket ID, creation date, and BDD generation status (FR32)

**And given** the user clicks a past session row
**When** the session detail page (`/session/[sessionId]`) loads
**Then** the BDD editor is populated with the saved BDD content from that session (FR33)

**And** if a verification report exists for the session it is also rendered in the results panel
**And** sessions belonging to other users are never returned by the API — enforced by FastAPI `user_id` check + Supabase RLS (FR34)
**And** TanStack Query handles loading and error states — no blank screens without feedback

---

## Epic 2: GitHub Code Verification (No Auth)

Enable users to select a GitHub source (specific files, full repository, or pull request diff), trigger LLM-based per-scenario BDD verification against fetched BDD scenarios, view pass/fail verdicts with code-level justifications and implementation suggestions for failures — all using the `DEV_USER_ID` stub from Epic 1. No auth or RAG enrichment in this epic.

> **Pattern:** Same DEV_USER_ID stub approach as Epic 1. Authentication is wired in Epic 3.

### Story 2.1: GitHub Source Selection UI

As a **user** (stubbed as `DEV_USER_ID` in this epic),
I want to select how I'll provide code for verification (specific files, full repo, or PR),
So that I can point the system at the right code before running scenario verification.

**Acceptance Criteria:**

**Given** the user is on the pipeline workspace page with BDD content present
**When** they reach the verification step
**Then** a mode selector is displayed with three options: "Exact File Paths", "Full Repository", and "Pull Request" (FR19)

**And** selecting "Exact File Paths" reveals a multi-line text input for one or more file paths
**And** selecting "Full Repository" reveals a single repo URL input
**And** selecting "Pull Request" reveals a PR link input
**And** the selected mode and input values are stored in `SessionContext`
**And** the "Verify" button remains disabled until a mode is selected and the required input is non-empty

---

### Story 2.2: GitHub Code Fetching (All 3 Modes)

As a **user** (stubbed as `DEV_USER_ID` in this epic),
I want the system to fetch the relevant code from GitHub based on my selected mode,
So that the correct code is available for LLM-based verification without me having to manually copy-paste it.

**Acceptance Criteria:**

**Given** the user has selected "Exact File Paths" and entered one or more valid file paths
**When** the verify request is submitted to `POST /api/v1/verification/fetch`
**Then** the FastAPI GitHub service fetches each specified file's content from the GitHub API (FR20)

**And given** "Full Repository" mode is selected with a valid repo URL
**When** the verification request is submitted
**Then** the GitHub service fetches the full file tree and file contents of the repository (FR21)

**And given** "Pull Request" mode is selected with a valid PR URL
**When** the verification request is submitted
**Then** the GitHub service fetches the PR diff/patch from the GitHub API (FR22)

**And** if any GitHub fetch fails (file not found, invalid path, rate limit, API error)
**Then** the SSE stream emits `data: {"type": "error", "message": "Human-readable message", "code": "GITHUB_FETCH_FAILED"}` and session state and BDD content are fully preserved (FR26, FR27, NFR-R1, NFR-S7)
**And** GitHub API 429 responses trigger automatic retry with exponential backoff before surfacing a user-facing message (NFR-R2)

---

### Story 2.3: LLM Verification Service & API

As a **user** (stubbed as `DEV_USER_ID` in this epic),
I want the system to evaluate each of my BDD scenarios against the fetched code using an LLM,
So that I get an objective per-scenario assessment of whether the code implements the acceptance criteria.

**Acceptance Criteria:**

**Given** fetched GitHub code and BDD scenarios are available for a session
**When** the FastAPI verification service sends them to the LLM via the `LLMProvider` interface
**Then** the LLM evaluates each scenario independently and returns a structured verdict per scenario (FR23)

**And** each verdict contains: `scenario_id`, `status` (`pass` or `fail`), `justification` (natural language), `code_reference` (file + function/class name + line number) (FR24)
**And** each verdict includes `github_links` referencing the repository files/lines used during analysis (FR37)
**And** no `rag_context` field is included in this epic — RAG enrichment is added in Epic 4
**And** for failed scenarios, the verdict includes an `implementation_suggestion` with actionable improvement guidance (FR38)
**And** verdicts stream progressively via SSE: `data: {"type": "verdict", "scenario_id": "...", "status": "pass", "justification": "...", "code_reference": "...", "implementation_suggestion": "..."}` (NFR-P5)
**And** the LLM is called via `LLMProvider` ABC — never directly via SDK (NFR-R4)
**And** verification results are persisted to the `verification_results` table linked to `session_id` and `DEV_USER_ID`
**And** if the LLM call fails, an error is streamed and BDD content and session state are preserved (FR27, NFR-R3)
**And** full verification completes within 60 seconds for up to 15 scenarios vs up to 10,000 lines of code (NFR-P4)

---

### Story 2.4: Verification Results Display

As a **user** (stubbed as `DEV_USER_ID` in this epic),
I want to see a clear per-scenario pass/fail report with code-level justifications, implementation suggestions, and an overall pass rate,
So that I can quickly identify gaps in code coverage and share findings with my team.

**Acceptance Criteria:**

**Given** verification is complete for a session
**When** the results panel renders
**Then** each BDD scenario is displayed as a `VerificationResultRow` with its verdict badge (✅ PASS / ❌ FAIL) and natural-language justification referencing specific code sections (FR24)

**And** failed scenarios auto-expand to show the `implementation_suggestion` with actionable guidance (FR38)
**And** each result row includes a GitHub link to the relevant code file/line used during analysis (FR37)
**And** an overall pass percentage is displayed prominently at the top of the results panel (FR25)
**And** results appear progressively in real time as the SSE stream emits per-scenario verdicts — no waiting for all results at once
**And** the display is read-only on mobile viewports (desktop-first requirement)
**And** no RAG context panel is shown in this epic — that is added in Epic 4

---

## Epic 5: Ticket Comprehension (RAG Q&A) & Traceability Reporting

Allow users to interrogate ingested Jira tickets through a RAG-powered chatbot (grounded exclusively in the ticket content), and produce downloadable traceability reports linking AC clauses → BDD scenarios → verification outcomes as PDF and CSV — including RAG retrieval context and implementation suggestions.

> **Note:** Stories 5.1–5.2 (RAG track) and Stories 5.3–5.4 (Reports track) are independent and can be developed in parallel — they share no code.

### Story 5.1: RAG Chat API

As an **authenticated user**,
I want a backend API endpoint that answers my natural-language questions about an ingested Jira ticket,
So that I can quickly understand the ticket's edge cases and requirements without reading it top to bottom.

**Acceptance Criteria:**

**Given** a POST request to `/api/v1/chat/message` with `session_id` and `question` text
**When** the FastAPI RAG service queries Pinecone using the question embedding
**Then** it retrieves the top-K most relevant chunks from the ticket's Pinecone namespace `{user_id}:{session_id}` (FR10, NFR-S4)

**And** the retrieved chunks are sent to the LLM via the `LLMProvider` interface with a strict grounding prompt that forbids answers outside the retrieved context (FR10)
**And** the LLM response streams as SSE: `data: {"type": "token", "content": "..."}` followed by `data: {"type": "complete"}` (NFR-P5)
**And** the chat message pair (question + answer) is persisted to the `chat_messages` table linked to `session_id` and `user_id` (FR11)
**And** the response is grounded exclusively in the ingested ticket — no cross-session or external knowledge leakage (FR10)
**And** the RAG response returns within 10 seconds under normal conditions (NFR-P2)
**And** a Pytest test verifies the endpoint never returns content from outside its session namespace

---

### Story 5.2: RAG Chat UI

As an **authenticated user**,
I want a chat interface on the pipeline workspace where I can ask questions about my Jira ticket,
So that I can comprehend complex tickets interactively before generating BDD.

**Acceptance Criteria:**

**Given** the user has a Jira ticket ingested for a session
**When** they type a question into the chat input and press send
**Then** the message is sent via `useSSEStream()` to the RAG chat API and streamed response tokens appear progressively in the chat UI (FR9, NFR-P5)

**And** all previous questions and answers in this session are displayed above the input as chat history (FR11)
**And** on revisiting a session, past chat history is fetched via `useSession()` TanStack Query hook and re-displayed (FR12)
**And** a loading indicator is shown while awaiting the first streamed token from the LLM
**And** the chat interface is responsive but read-only on mobile viewports

---

### Story 5.3: Traceability Report Generation API

As an **authenticated user**,
I want the system to produce a traceability report that links each AC clause to its BDD scenario and verification outcome,
So that I have a single auditable artifact connecting requirements to test results.

**Acceptance Criteria:**

**Given** a session has BDD content and verification results persisted in the database
**When** a GET request is made to `/api/v1/reports/{session_id}/traceability`
**Then** the FastAPI report service returns a structured JSON object linking each AC clause → its BDD scenario → its verification verdict (FR28)

**And** each row in the report contains: `ac_clause`, `scenario_title`, `scenario_status` (`pass`/`fail`), `justification`, `code_reference`, `implementation_suggestion` (for failed scenarios) (FR46)
**And** the report includes any RAG retrieval context used during verification (related stories, Confluence docs, project knowledge) (FR45)
**And** the traceability data is assembled from the `verification_results` and `bdd_files` tables — no additional LLM call required
**And** only the authenticated owner of the session can retrieve the traceability report (enforced by `user_id` check + Supabase RLS)

---

### Story 5.4: PDF & CSV Report Export

As an **authenticated user**,
I want to download my traceability report as a PDF or CSV file,
So that I can share verification evidence with stakeholders and attach it to PRs or Jira tickets.

**Acceptance Criteria:**

**Given** verification results and traceability data exist for a session
**When** the user clicks "Download PDF"
**Then** the FastAPI report service generates a formatted PDF from the traceability report and the browser downloads it as `{ticket_id}_report.pdf` (FR29)

**And** the PDF includes RAG retrieval context and implementation suggestions alongside verdicts (FR45, FR46)
**And** the generated PDF is stored in the `reports` Supabase Storage bucket via `StorageService` (FR42)

**And given** the user clicks "Download CSV"
**When** the CSV is generated from the traceability data
**Then** the file downloads with columns: AC Clause, Scenario, Status, Justification, Code Reference, Implementation Suggestion, RAG Context (FR30, FR45, FR46)

**And** both exports are triggered via `useVerification()` TanStack Query mutation hooks
**And** the export buttons are only enabled when verification results exist for the session
**And** no LLM is called during export — data is assembled entirely from the existing `verification_results` table

---

### Story 5.5: Project Knowledge Base Q&A (General RAG Chat)

> **Context (added post-hoc):** Stories 5.1/5.2 built a chat scoped to a single ingested Jira ticket (`{user_id}:{session_id}`). This story adds a **project-wide** chat over the Epic 4 knowledge base (`{user_id}:knowledge`) so users can ask questions across all their ingested Confluence pages, Jira workspace tickets, and uploaded documents — not just one ticket. Reuses the Epic 4 retrieval (`query_knowledge_base`) and the 5.1 streaming pattern.

As an **authenticated user**,
I want to ask natural-language questions about my whole project and get answers grounded in my ingested knowledge base,
So that I can quickly understand project context (architecture, related work, docs) without opening a specific ticket.

**Acceptance Criteria:**

**Given** an authenticated user has ingested project knowledge (Confluence/Jira/documents) into `{user_id}:knowledge`
**When** a POST request is made to `/api/v1/chat/knowledge` with a `question`
**Then** the FastAPI service retrieves the top-K relevant chunks from the user's knowledge namespace via `knowledge_service.query_knowledge_base` and grounds the LLM strictly in them (no ticket/session scoping)

**And** the answer streams as SSE `data: {"type": "token", ...}`, followed by a `data: {"type": "sources", ...}` event listing the knowledge sources used, then `data: {"type": "complete"}`
**And** the LLM is called only via the `LLMProvider` interface, at low temperature, with a grounding prompt that forbids answers outside the retrieved context (graceful "not in the knowledge base" when empty)
**And** the endpoint requires authentication and is **not** scoped to a session; it is stateless (no chat history persisted, since there is no session to anchor to)
**And** retrieval degrades gracefully (empty context) when Pinecone is unconfigured or times out — the endpoint never 500s on retrieval failure

**And given** the user opens the Knowledge Base screen (`/knowledge`)
**When** they type a question into the "Ask about your project" chat and send
**Then** a `KnowledgeChatPanel` streams the answer token-by-token via `useKnowledgeChat` (`useSSEStream`) and renders the cited sources as clickable chips (Confluence/Jira links open in a new tab)
**And** the chat is read-only on mobile viewports (desktop-first, mirroring the ticket chat)

---

## Epic 4: Project Knowledge Base & RAG Enrichment

Enable ingestion of project knowledge sources into the vector database, and integrate contextual RAG retrieval into the verification pipeline to enrich LLM analysis with real project knowledge.

> **Dependencies:** Epic 2 (verification pipeline) and Epic 3 (auth) must be complete before RAG enrichment can be integrated.

### Story 4.1: Knowledge Base Service & Confluence Integration

As an **authenticated user**,
I want to connect my Confluence workspace and ingest project documentation into the knowledge base,
So that the system has access to my project's architectural decisions, guidelines, and related documentation.

**Acceptance Criteria:**

**Given** an authenticated user provides Confluence API credentials (base URL + API token)
**When** a POST request is made to `/api/v1/knowledge/ingest/confluence` with workspace and page/space identifiers
**Then** the FastAPI `knowledge_service.py` fetches Confluence pages via `confluence_service.py`, chunks the content, and embeds it into Pinecone under namespace `{user_id}:knowledge` (FR39, FR40, NFR-S8)

**And** a `knowledge_sources` table row is created tracking the ingested source type, URL, and ingestion timestamp
**And** the ingestion streams SSE progress events showing pages being processed
**And** if Confluence API fetch fails, a clear error is emitted via SSE with actionable messaging (NFR-R1)
**And** a Pytest test verifies that knowledge embeddings are namespaced per user and not accessible cross-user

---

### Story 4.2: Jira Workspace Knowledge Ingestion

As an **authenticated user**,
I want to ingest related Jira stories and project metadata into the knowledge base,
So that the verification process can reference historical context and related work.

**Acceptance Criteria:**

**Given** an authenticated user has Jira credentials configured
**When** a POST request is made to `/api/v1/knowledge/ingest/jira` with project key and optional filters (sprint, label, etc.)
**Then** the FastAPI `knowledge_service.py` fetches related Jira stories via `jira_service.py`, chunks them, and embeds into Pinecone under namespace `{user_id}:knowledge` (FR39, FR40)

**And** each ingested story is tracked in the `knowledge_sources` table with source type `jira` and the ticket ID
**And** the ingestion streams SSE progress events showing tickets being processed
**And** knowledge source data is namespaced per user — no cross-user leakage (NFR-S8)

---

### Story 4.3: RAG-Enriched Verification Integration

As an **authenticated user**,
I want the verification process to automatically retrieve relevant project context from my knowledge base,
So that the LLM's code analysis is grounded in real project knowledge and produces more accurate verdicts.

**Acceptance Criteria:**

**Given** a user has ingested project knowledge (Confluence docs, related Jira stories) in Pinecone namespace `{user_id}:knowledge`
**When** verification is triggered for a session
**Then** the `verification_service.py` queries the knowledge base via `knowledge_service.py` to retrieve top-K relevant knowledge chunks based on the BDD scenarios and Jira ticket context (FR35, FR41)

**And** the retrieved knowledge chunks are included in the LLM verification prompt alongside the code and BDD scenarios
**And** each verification result includes a `rag_context` field listing the retrieved knowledge sources (source type, title, snippet) (FR36)
**And** RAG retrieval completes within 10 seconds during verification (NFR-P6)
**And** if no knowledge base exists for the user, verification proceeds without RAG enrichment (graceful degradation)

---

### Story 4.4: RAG Context Display Panel

As an **authenticated user**,
I want to see what project knowledge was used during verification,
So that I can understand and trust the context behind the LLM's analysis.

**Acceptance Criteria:**

**Given** verification is complete and RAG retrieval results exist for the session
**When** the verification results panel renders
**Then** a `RAGContextPanel` component displays the retrieved knowledge sources (Confluence page titles, related Jira story IDs, project artifacts) with expandable snippets

**And** each knowledge source is clickable — Confluence links open in a new tab; Jira stories show ticket ID and title
**And** if no RAG context was retrieved, the panel shows "No additional project context available" — not an error
**And** the RAG context is persisted and displayed on session revisit via the verification results API

---

### Story 4.5: Knowledge Base Ingestion UI

As an **authenticated user**,
I want a UI to connect and ingest Confluence pages and Jira workspace tickets into my knowledge base,
So that I can populate the RAG knowledge base from the app without calling the API manually.

> **Context:** Stories 4.1/4.2 built the ingestion endpoints (`POST /api/v1/knowledge/ingest/confluence` and `/jira`) and the frontend hooks (`useIngestConfluence`, `useIngestJira`) API-only — no screen was ever specced. This story surfaces that capability in the UI.

**Acceptance Criteria:**

**Given** an authenticated user opens the Knowledge Base ingestion UI (a panel/section reachable from the app)
**When** they submit the Confluence form (space key *or* page ID) or the Jira form (project key + optional sprint/label filters)
**Then** the existing `useIngestConfluence` / `useIngestJira` hooks POST to the SSE ingest endpoints and stream live progress (pages/tickets processed, current/total) using the established progress-log pattern

**And** the final `complete` event shows the ingested count, and `error` events surface an actionable message (never a silent failure) (NFR-R1)
**And** a list of previously ingested knowledge sources for the user is displayed (source type, title, URL, ingestion status, date) — backed by a `GET /api/v1/knowledge/sources` endpoint returning `KnowledgeSourceResponse` rows scoped to the current user (NFR-S8)
**And** when Pinecone or the relevant credentials are not configured, the UI shows a clear informational state rather than appearing broken
**And** form inputs are validated client-side (e.g. Jira `project_key` matches the backend's uppercase-alphanumeric rule) before submitting

---

### Story 4.6: Document Upload Knowledge Ingestion (PDF / DOCX)

As an **authenticated user**,
I want to upload PDF and DOCX documents into my knowledge base (alongside Confluence and Jira),
So that project docs that don't live in Confluence/Jira can still enrich RAG verification.

> **Context:** Extends the knowledge base (4.1 Confluence, 4.2 Jira, 4.5 UI) with a third ingestion source: direct file upload. Reuses the same chunk → embed → Pinecone `{user_id}:knowledge` pipeline and the `knowledge_sources` tracking table.

**Acceptance Criteria:**

**Given** an authenticated user uploads a `.pdf` or `.docx` file via a new "Documents" option in the Knowledge Base UI
**When** a multipart POST is made to `/api/v1/knowledge/ingest/document`
**Then** the backend extracts text (PDF via `pypdf`, DOCX via `python-docx`), chunks it with the existing `chunk_text`, embeds via `_embed_chunks`, and upserts into Pinecone namespace `{user_id}:knowledge` with `source="document"` and `title=<filename>` (FR39, FR40, NFR-S8)

**And** a `knowledge_sources` row is recorded with `source_type="document"`, `title=<filename>`, `source_url=null`, and the chunk/page count
**And** unsupported file types are rejected with a clear 422, and oversize files with a 413 (reuse the BDD-upload size guard pattern)
**And** the ingested document appears in the existing "Ingested sources" list and its chunks are retrievable during verification (the RAG context panel shows it as a `document` source)
**And** the UI validates file type (`.pdf`/`.docx`) and size client-side before uploading, and shows progress + success/error states consistent with the Confluence/Jira forms

---

### Story 4.8: Per-Scenario RAG Retrieval & Verification Toggle

> **Context (added post-hoc):** Story 4.3 enriches verification with knowledge-base context, but retrieved it **once** using the whole BDD blob and shared those chunks across every scenario. This story makes retrieval **per-scenario** (each scenario queries the KB with its own Gherkin + AC text, in parallel) for more relevant context and more accurate verdicts, and adds an env toggle to disable KB enrichment entirely.

As an **authenticated user**,
I want each BDD scenario verified against the knowledge-base context most relevant to *that* scenario, and the ability to turn knowledge-base enrichment off,
So that verdicts are more accurate, and I can verify against the ticket/code alone when I don't want the knowledge base to influence results.

**Acceptance Criteria:**

**Given** verification runs for a session with multiple BDD scenarios and a populated knowledge base
**When** the verification service assembles each scenario's prompt
**Then** it retrieves the top-K relevant knowledge chunks **per scenario** (querying with that scenario's own Gherkin/AC text), running the retrievals in parallel, and includes each scenario's own chunks in its LLM prompt (FR35, FR41)

**And** each verdict's persisted `rag_context` reflects the chunks retrieved for **that** scenario (no longer a single shared payload) — surfaced per-row in the RAG context panel and the traceability report
**And** retrieval stays within the NFR-P6 budget (parallelized) and degrades gracefully to no-enrichment when the knowledge base is empty or Pinecone is unavailable

**And given** the `VERIFICATION_RAG_ENABLED` env var is set to `false`
**When** verification runs
**Then** the knowledge base is **not** queried at all, no `PROJECT CONTEXT` is added to prompts, and every verdict's `rag_context` is `null` (verify against ticket/code only)
**And** the toggle defaults to `true` (enrichment on) when unset

---

### Story 4.9: Knowledge-Base Opt-In for Verification (Agentic RAG + Checkbox)

> **Context (added post-hoc):** Stories 4.3/4.8 added knowledge-base RAG to `verification_service.run_verification` (the `/run` endpoint), but the UI verifies via `/run-agentic` (`agentic_verification_service`), which had **no** RAG — so the knowledge base never influenced real verifications. This story adds per-scenario RAG to the agentic path and replaces the deploy-wide `VERIFICATION_RAG_ENABLED` env var with a **per-run user choice** (a checkbox at verify time, default off).

As an **authenticated user**,
I want a checkbox at verification time to decide whether to enrich the run with my project knowledge base,
So that I control—per run—whether Confluence/Jira/document context influences the verdicts, defaulting to code-only verification.

**Acceptance Criteria:**

**Given** the user is on the verification step
**When** the verification controls render
**Then** a "Use project knowledge base" checkbox is shown, **unchecked by default**, and its value is sent as `use_knowledge_base` on the verification request

**And** when the box is checked, the agentic verification retrieves per-scenario knowledge-base context (reusing the Story 4.8 batched retrieval) and includes it in each scenario's LLM prompt, and each verdict's `rag_context` is populated and persisted (surfaced in the RAG context panel and the traceability report)
**And** when the box is unchecked (default), the knowledge base is **not** queried, no context is added to prompts, and every verdict's `rag_context` is `null`
**And** the gating is a per-request flag on both `/run` and `/run-agentic` — the `VERIFICATION_RAG_ENABLED` env var is removed
**And** retrieval degrades gracefully (empty context) when the KB is empty or Pinecone is unavailable, and stays within the NFR-P6 budget

---

### Story 4.7: Delete Ingested Knowledge Sources

> **Context (added post-hoc):** Stories 4.1/4.2/4.6 ingest Confluence/Jira/document sources into Pinecone (`{user_id}:knowledge`) and track each in `knowledge_sources`, and 4.5 lists them — but there was no way to remove one. This story adds deletion of both the DB row and the source's vectors, so stale/incorrect knowledge stops influencing RAG verification and the project chat (Story 5.5).

As an **authenticated user**,
I want to delete a knowledge source I previously ingested,
So that outdated or incorrect project knowledge no longer influences verification or project Q&A.

**Acceptance Criteria:**

**Given** an authenticated user has ingested knowledge sources listed in the Knowledge Base UI
**When** they click delete on a source and confirm
**Then** a `DELETE /api/v1/knowledge/sources/{id}` request removes the source's vectors from the `{user_id}:knowledge` Pinecone namespace **and** deletes the `knowledge_sources` row, returning 204

**And** only the owner can delete a source — 404 for a missing source, 403 for another user's source (NFR-S8)
**And** vector deletion targets exactly that source's chunks (via a persisted `source_ref` = the Pinecone vector-id prefix: page id / ticket id / document uuid), stored on ingest for Confluence, Jira, and document sources
**And** a vector-store failure is logged but does not block removing the DB row (best-effort; the row always disappears from the UI so the user isn't stuck)
**And** the ingested-sources list updates immediately after deletion (the `["knowledge","sources"]` query is invalidated), with a per-row pending state and a confirmation prompt before deleting

---

## Epic 6: Fine-Tuned Model Training & Integration (Phase 2)


Capture real story-to-test-case training pairs, train a fine-tuned domain-specific model for BDD test case generation, and integrate it behind a provider abstraction that allows seamless switching between the fine-tuned model and the general-purpose LLM.

> **Note:** This is Phase 2 scope. The `BDDModelProvider` abstraction interface is set up early (Story 6.1) so that the fine-tuned model can be plugged in when the trained model is available. The general LLM continues to serve BDD generation in MVP via `GeneralLLMFallbackProvider`.
>
> **Story 6.4 is the exception to "Phase 2".** It captures the training pairs that Stories 6.5 and 6.3 depend on, and that data cannot be recovered after the fact — acceptance criteria are not persisted and editor corrections are discarded. Ship it with MVP even though the model itself is Phase 2.
>
> **Serving is an open decision, and Story 6.2 owns all of it.** Production is `t3.small` (no GPU) and cannot host a 7B model. Story 6.5 produces a LoRA adapter and a contract-complete shim; **converting that adapter into a servable artifact and proving an end-to-end request belongs to 6.2**, along with the production hosting decision. Story 6.3 evaluates locally via Ollama against the artifact 6.2 produces — it needs the conversion, not the production decision. *(Boundary corrected 2026-08-09 — see sprint-change-proposal-2026-08-09.md.)*
>
> **Execution order:** 6.4 → 6.6 → 6.7 → 6.5 → 6.2 → 6.3 → 6.9 → 6.8 (not numeric order; see sprint-status.yaml). Governance (6.6) lands before more data accumulates, and the manual uploader (6.7) supplies the corpus that training (6.5) consumes — the database currently holds only 2 human-authored rows.
>
> **6.8 and 6.9 were added post-hoc on 2026-08-22**, for work that shipped 2026-08-16/17 outside the BMAD workflow. They follow 6.3 because both build on what it measured: 6.9 exposes its runner and metrics over HTTP, and 6.8 acts on its central finding — *fine-tuning worked; it learned the wrong corpus* — by adding hand-authored product-domain pairs. Their acceptance criteria are a reconstruction, not criteria agreed in advance.

### Story 6.1: BDD Model Provider Abstraction

As a **developer**,
I want a provider abstraction for BDD model selection that allows switching between a fine-tuned model and the general LLM,
So that the fine-tuned model can be integrated without modifying existing BDD generation code.

**Acceptance Criteria:**

**Given** the `BDDModelProvider` abstract base class exists in `backend/app/services/bdd_model/provider.py`
**When** `BDD_MODEL_PROVIDER` env var is set to `general_llm` (default)
**Then** the `bdd_model/factory.py` instantiates `GeneralLLMFallbackProvider` which delegates to the existing `LLMProvider` interface

**And** when `BDD_MODEL_PROVIDER` is set to `fine_tuned`
**Then** the factory instantiates `FineTunedModelProvider` which calls the fine-tuned model endpoint
**And** the `bdd_service.py` routes BDD generation through `BDDModelProvider` — never calling LLM or fine-tuned model directly (NFR-R6)
**And** a Pytest test verifies that the factory returns the correct provider based on the env var

---

### Story 6.2: Fine-Tuned Model Integration

As a **developer**,
I want the fine-tuned model endpoint integrated into the BDD generation pipeline,
So that BDD scenarios are generated by a domain-specific model trained on real story-to-test-case pairs.

**Acceptance Criteria:**

**Given** Story 6.5's LoRA adapter exists at `training/outputs/outputs/bdd-lora/`
**When** it is converted to a servable artifact and exposed over HTTP with `BDD_MODEL_PROVIDER=fine_tuned`
**Then** the `FineTunedModelProvider` sends the acceptance criteria to that endpoint and parses the response into Gherkin scenarios

**And** the conversion from PEFT adapter to servable artifact is documented and reproducible — Story 6.3 consumes the same artifact for local evaluation
**And** the shim reports `chat_template.status == "match"` on `/health`, proving serving applies the template the model was trained with
**And** if the fine-tuned model endpoint is unavailable, the system falls back to `GeneralLLMFallbackProvider` with a warning log
**And** a BDD generation request served by the fine-tuned model returns a valid `BDDGenerateResponse` within `FINE_TUNED_MODEL_TIMEOUT_SECONDS` — **12s**, per `backend/app/core/config.py:43`, not the 30s previously written here; 12s is a deliberate sub-budget so the general-LLM fallback still completes inside NFR-P3's 30s
**And** the response format is identical to the general LLM output — no frontend changes required

---

### Story 6.3: Model Evaluation Pipeline

As a **researcher**,
I want to systematically compare BDD output from the fine-tuned model against the general LLM baseline,
So that I can measure and validate the fine-tuning improvement with quantitative metrics.

**Acceptance Criteria:**

**Given** a set of 20+ real Jira tickets with known acceptance criteria
**When** BDD generation is run for each ticket using both `fine_tuned` and `general_llm` providers
**Then** the evaluation pipeline produces comparative metrics: test coverage, AC relevance score, correctness, and human-edit percentage

**And** results are stored in the database linked to model version and ticket ID for longitudinal tracking
**And** the evaluation can be triggered via a management command (`python -m app.evaluate_models`)
**And** a summary report is generated comparing the two model outputs side-by-side

---

### Story 6.4: Training Data Capture

As a **researcher**,
I want every BDD generation to persist its input acceptance criteria and any human corrections,
So that a dataset of real story-to-test-case pairs accumulates from normal usage.

**Acceptance Criteria:**

**Given** a BDD generation request succeeds
**When** the resulting `bdd_files` row is written
**Then** the acceptance criteria text that produced it is persisted and linked to that row, forming a retrievable input→output pair

**And** when a user edits generated BDD in the editor and saves, a row is persisted with `source='edited'` that references the originating generated row, preserving both versions
**And** existing `generated` and `uploaded` behaviour is unchanged — no regression to Epic 1 flows
**And** an Alembic migration adds the new column(s) with RLS and per-user isolation preserved (NFR-S6)
**And** Pytest tests verify AC persistence, that an edited save creates a distinct row, and that the original generated content survives

---

### Story 6.5: Fine-Tuning Dataset & Model Training

As a **researcher**,
I want a reproducible dataset build and training run,
So that a fine-tuned BDD model exists for Story 6.2 to integrate and Story 6.3 to evaluate.

**Acceptance Criteria:**

**Given** a corpus of `.feature` files and/or captured `bdd_files` rows
**When** `training/build_dataset.py` runs
**Then** it emits `train.jsonl` / `holdout.jsonl` in chat format, every target validated against `BDDGenerateResponse`, empty scenario arrays rejected, and the split taken by origin file so no scenario appears on both sides

**And** the training configuration is committed under `training/` and completes on free-tier GPU (Kaggle/Colab), producing a LoRA adapter
**And** training dependencies are NOT added to `backend/pyproject.toml` — they would ship in the production image via the Dockerfile's `COPY . .`
**And** the run is documented (base model, hyperparameters, dataset size, eval loss) so it can be reproduced
**And** a serving shim exists under `training/serve/` implementing the exact contract `FineTunedModelProvider` sends — `{acceptance_criteria, system_prompt, response_format}` in, `BDDGenerateResponse`-shaped JSON out — validated before responding and bounded inside `FINE_TUNED_MODEL_TIMEOUT_SECONDS`, with tests proving the contract and the output guarantee
**And** the chat template the run trained with is persisted beside the adapter so that serving can be verified against it — *end-to-end serving of the trained model is Story 6.2's, which owns the serving decision*

---

### Story 6.6: Training-Data Opt-Out Control

As an **operator**,
I want an environment switch that marks captured data as excluded from model training,
So that a deployment can keep its session history for debugging without that content ever feeding a fine-tune.

**Acceptance Criteria:**

**Given** `TRAINING_DATA_OPT_IN` is set to `false`
**When** a BDD generation or an editor correction is persisted
**Then** the row is still written in full (history and debugging are unaffected) but is flagged as excluded from training

**And** when `TRAINING_DATA_OPT_IN` is `true` (the default, preserving today's behaviour) rows are flagged as usable for training
**And** the flag is stamped **at write time**, not evaluated at build time — consent belongs to the moment of capture, so flipping the env var later must never retroactively change the status of existing rows
**And** `training/build_dataset.py --from-db` excludes flagged-out rows, and reports how many it skipped so a silently empty corpus is never mistaken for a missing database
**And** an Alembic migration adds the column, defaulting existing rows to opted-in (they were captured under the current always-on behaviour)
**And** the env var is wired into all four surfaces: `.env.example`, `docker-compose.yml`, and `BACKEND_ENV_KEYS` in both `cfn/setup-infra.sh` and `cfn/deploy-backend.sh`
**And** Pytest tests verify both settings produce the correct flag, and that the dataset builder filters on it

---

### Story 6.7: Manual Training Dataset Upload

As a **researcher**,
I want to upload my own `.feature` files or JSONL datasets through the app,
So that a fine-tune can be trained now rather than waiting months for captured corrections to accumulate.

**Acceptance Criteria:**

**Given** an authenticated user on the training-data page
**When** they upload one or more `.feature` files or a `.jsonl` dataset
**Then** each file is validated, stored in Supabase Storage under a dedicated training-data folder via the existing `StorageService`, and recorded in the database scoped to that user

**And** uploaded `.feature` files are parsed and rejected with a clear message if they contain no usable scenario (Feature + at least one Given/When/Then), so a corpus of unusable files cannot silently accumulate
**And** uploaded `.jsonl` files are validated line by line against the training-pair shape, reporting the first offending line number rather than a generic failure
**And** the page lists what has been uploaded (filename, type, scenario/pair count, upload date) and supports deleting an entry, removing both the database row and the stored object — mirroring the ingested-sources list from Story 4.7
**And** `training/build_dataset.py` can read this uploaded corpus as a source alongside `--features-dir` and `--from-db`
**And** uploads are user-scoped: a user can only list and delete their own, enforced in the route layer
**And** backend Pytest tests cover accept, reject-invalid, list, delete and cross-user 403; frontend tests cover upload, list render, and delete

---

> **Stories 6.8 and 6.9 were added post-hoc on 2026-08-22.** Both shipped on 2026-08-16/17 outside the BMAD workflow. The acceptance criteria below are a **reconstruction** of what the implemented work had to satisfy, recovered from `training/RUN_LOG.md`, the shipped code and the commit history — they were not agreed before implementation, and should not be read as if they were. Detail lives in the story files.

### Story 6.8: Product-Domain Training Corpus

As a **researcher**,
I want a training corpus written in the product's own domain rather than testing-framework Gherkin,
So that the fine-tune learns to reason about behaviour instead of learning the shape of `rspec` invocations.

**Acceptance Criteria:**

**Given** `RUN_LOG.md` has recorded since Run 1 that 84% of the corpus is Gherkin about running CLI commands, and Run 2 established that more data of the same kind changes nothing
**When** product-domain pairs are authored
**Then** both sides of every pair — the acceptance criteria and the Gherkin — are hand-written in reviewable YAML under `training/corpus-product/` with **no LLM involved**, because an LLM-written target would train the fine-tune to imitate the general model it is meant to beat

**And** `training/build_product_pairs.py` compiles that YAML into the exact JSONL shape `build_dataset.py --pairs-dir` consumes, validating every target against `BDDGenerateResponse` and embedding the same `BDD_SYSTEM_PROMPT` the serving path sends
**And** `build_dataset.py --pairs-dir` merges an existing dataset without paying for back-generation twice, with the pre-merge dataset preserved at `training/data-v1/`
**And** a `--check` mode validates the YAML without writing, so an authoring error is caught before a training run
**And** the merged dataset is trained and recorded in `RUN_LOG.md` with the **holdout split by domain group** — an aggregate over a holdout that is still ~85% legacy corpus hides the effect of the new data entirely
**And** the sample size is stated plainly: three product-domain holdout items is a signal, not a result

### Story 6.9: Model Comparison UI & Saved Evaluation Runs

As a **researcher**,
I want to run and review fine-tuned vs. general-LLM comparisons from inside the app,
So that the evaluation is reproducible by someone who does not run the CLI, and a run can be re-read after the terminal session that produced it is gone.

**Acceptance Criteria:**

**Given** an authenticated user supplies a Jira ticket ID or pasted acceptance criteria
**When** `POST /api/v1/evaluation/compare` is called
**Then** both providers generate for that input and each result is scored with **the same `evaluation_metrics` functions the batch CLI uses**, so the API and `app.evaluate_models` cannot drift into measuring different things

**And** every result carries `configured_provider`, `effective_provider`, `fallback_reason` and `model_identifier`, so a silently degraded generation is visible in the UI rather than merely counted
**And** the comparison route passes `allow_fallback=False` explicitly — this caller is measuring, and an explicit argument outranks `FINE_TUNED_ALLOW_FALLBACK`, so no configuration change can re-enable a silent fallback underneath it
**And** `POST /evaluation/runs` batches several tickets under a caller-supplied `run_id` and returns a per-item outcome, so one unresolvable ticket does not discard the batch
**And** `GET /evaluation/runs`, `GET /evaluation/report` and `DELETE /evaluation/runs/{run_id}` list, summarise and delete saved runs, each scoped to `user_id` in the query itself
**And** the report excludes degraded rows from every average and counts them separately; omitting `run_id` pools every saved run
**And** the general LLM runs before the fine-tuned model, so the slow column is the only thing left pending in the UI
**And** `/comparison` hosts `ModelComparisonPanel` (single side-by-side comparison) and `SavedRunsPanel` (run list, report, delete)
