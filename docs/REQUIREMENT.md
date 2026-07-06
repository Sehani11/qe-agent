# BDD-AutoGen — Requirements

## Project Overview
Automated generation of BDD feature files from Jira acceptance criteria with GitHub-based code verification for early defect detection.

**Primary Users:** Developer, QA Engineer

---

## Core Modules

### Module 1: Jira Ingestion & RAG Q&A

- User inputs a Jira ticket ID or URL
- System fetches ticket via Jira REST API (API token auth)
- Extracted fields: Summary, Description, Acceptance Criteria, Labels, Linked Issues
- Content is chunked and embedded into a vector database (**Pinecone**)
- A chat interface allows the user to ask natural-language questions about the ticket (RAG-powered)
- LLM answers are grounded only in the retrieved ticket chunks
- Chat history is maintained per session and stored per user account

---

### Module 2: BDD Feature File Generation & Editor

- User triggers BDD generation from the ingested Jira ticket
- LLM generates Gherkin scenarios (Feature / Scenario / Given / When / Then) per acceptance criterion
- Each scenario is traceable to the specific AC clause it covers
- Generated BDD is displayed in an editable textarea in the UI
- User can freely edit the content before proceeding

**Actions available from the editor:**
- **Proceed to Verification** — passes current BDD to Module 3
- **Save Feature File** — downloads as `.feature` file
- **Generate CSV** — converts BDD scenarios into a structured CSV (Scenario, Given, When, Then, Expected Result) and downloads
- **Upload Feature File** — user uploads an existing `.feature` file to skip generation and go directly to verification

---

### Module 3: GitHub-Based Code Verification

**Step 1 — GitHub Source Selection**

User selects one of three input modes:

| Mode | Input | Fetch Method |
|---|---|---|
| Exact File Paths | Specific file paths relevant to the feature | GitHub Contents API per file |
| Full Repository | Full GitHub repo URL | GitHub Trees API + Contents API |
| Pull Request | PR link for the feature branch | GitHub PR Files API (diff/patch) |

> Note: GitHub REST API vs GitHub MCP server will be decided during implementation based on feasibility testing.

**Step 2 — BDD Verification**

- Fetched code + BDD feature file are sent to the LLM for analysis
- Primary: LLM judges whether each BDD scenario is covered by the code
- Secondary (if feasible): attempt BDD test execution in a sandboxed environment via Cucumber

**Step 3 — Results & Reporting**

- Overall pass percentage displayed (e.g., 7/10 = 70%)
- Per-scenario results showing:
  - Pass / Fail verdict
  - Justification — natural-language explanation referencing specific code sections
  - Relevant code references (file, function/class)
- Traceability report: AC clause → BDD scenario → verification outcome
- Failure clustering by root cause category (missing implementation, logic mismatch, incomplete endpoint, etc.)
- Full report exportable as PDF or CSV

---

## Authentication

- User authentication via **Supabase Auth** (email/password or OAuth)
- All authenticated users have a single role: `user`
- No admin roles or RBAC required for MVP
- All session history and results are scoped per user account

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Frontend | Next.js | Hosted on Vercel |
| Backend | Python / FastAPI / uv | Dockerised, hosting TBD |
| Auth | Supabase Auth | Email/password or OAuth |
| Relational DB | Supabase PostgreSQL | Sessions, history, user data |
| Vector DB | Pinecone | RAG embeddings for Jira ticket chunks |
| ORM | SQLAlchemy | |
| LLM | Claude / OpenAI API (TBD) | Abstracted behind provider interface |
| BDD Framework | Cucumber (optional, Growth tier) | Sandboxed execution |
| GitHub Integration | GitHub REST API / GitHub MCP (TBD) | |
| Jira Integration | Jira REST API | API token auth |
| Containerisation | Docker | Backend containerised |
| CI/CD | GitHub Actions | Runs on every PR |

---

## Open Questions (Decide During Implementation)

1. GitHub data fetching: REST API vs GitHub MCP server?
2. LLM provider: Claude API vs OpenAI? (evaluate context window, cost, Gherkin quality)
3. BDD sandboxed execution feasibility — attempt only if LLM analysis accuracy is insufficient (Growth tier)
4. Vector DB chunking strategy: sentence-level vs paragraph-level? (Pinecone index configuration)
5. Async vs sync DB operations for FastAPI backend
6. Backend hosting: where to deploy the Dockerised FastAPI server? (TBD — Railway, Render, AWS, GCP, etc.)