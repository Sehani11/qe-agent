---
stepsCompleted:
  - step-01-init
  - step-02-discovery
  - step-02b-vision
  - step-02c-executive-summary
  - step-03-success
  - step-04-journeys
  - step-05-domain
  - step-06-innovation
  - step-07-project-type
  - step-08-scoping
  - step-01b-continue
  - step-09-functional
  - step-10-nonfunctional
  - step-11-polish
  - step-12-complete
inputDocuments:
  - docs/REQUIREMENT.md
  - docs/UPDATED_SCOPE.md
workflowType: 'prd'
classification:
  projectType: saas_b2b
  domain: developer_tooling
  complexity: high
  projectContext: greenfield
lastUpdated: '2026-03-15'
changeLog:
  - date: '2026-03-15'
    description: 'Major scope update — incorporated UPDATED_SCOPE.md: fine-tuned model for test generation, expanded RAG with multi-source knowledge base, MCP server integration, storage bucket, implementation suggestions, research evaluation metrics'
---

# Product Requirements Document — QE Verification Agent (qe-agent-v2)

**Author:** Chamath  
**Date:** 2026-03-08  
**Last Updated:** 2026-03-15

## Executive Summary

QE Verification Agent is a B2B SaaS QA intelligence platform that automates the generation of BDD feature files from Jira acceptance criteria and verifies them against actual code on GitHub. It targets developers and QA engineers who bridge the gap between requirements and test coverage manually — a process that is slow, error-prone, and scattered across disconnected tools.

The platform operates as a multi-module pipeline: **(1)** Ingest a Jira ticket and explore it via a RAG-powered chatbot — enabling ticket comprehension without reading it in full; **(2)** Generate traceable Gherkin scenarios from acceptance criteria using a **fine-tuned domain-specific model** trained on real story-to-test-case pairs, producing more accurate and contextually relevant test cases than a general-purpose LLM; **(3)** Verify BDD scenarios against the actual codebase via GitHub integration using a general-purpose LLM's reasoning capabilities, enriched by **RAG-based contextual retrieval** from project knowledge sources (Confluence, related Jira stories, architecture notes); producing per-scenario pass/fail verdicts with code-level justifications, **implementation suggestions for failures**, a traceability report, RAG retrieval context, and export as PDF or CSV. All results are persisted per user for future viewing and audit.

The system also incorporates a **Retrieval-Augmented Generation (RAG)** mechanism that retrieves knowledge from multiple project sources — Jira workspace data, Confluence documentation, and other project-related repositories — indexed in a vector database. During verification, this contextual data enriches the LLM's reasoning, improving accuracy and completeness by grounding analysis in real project knowledge rather than relying solely on the Jira story description.

QE Verification Agent replaces a fragmented multi-tool workflow (Jira → manual BDD editor → spreadsheet traceability → manual code review) with a single pipeline: fetch → understand → generate → verify → report → persist.

## Project Classification

| Attribute | Value |
|---|---|
| **Project Type** | B2B SaaS — full-stack web application with API integrations |
| **Domain** | Developer Tooling / QA Automation |
| **Complexity** | High — fine-tuned model integration, LLM/RAG architecture, vector DB, MCP servers, four third-party API integrations |
| **Context** | Greenfield |
| **Tech Stack** | Next.js (Vercel), Python/FastAPI/uv (Dockerised), Supabase Auth, Supabase PostgreSQL, Supabase Storage, Pinecone, Docker, GitHub Actions |

## Success Criteria

### User Success

- A developer or QA engineer inputs a Jira ticket ID/URL and receives a complete set of editable Gherkin BDD scenarios without touching any other tool
- The RAG chatbot answers questions about the ticket content, grounded in the ingested ticket and project knowledge base — no hallucinations
- Users can edit generated BDD, upload a custom `.feature` file, download the BDD as a `.feature` file, and download a structured CSV test case file
- Users receive a code verification report with per-scenario pass/fail verdicts, natural-language justifications referencing specific code sections, and **implementation suggestions for failed scenarios**
- Verification output includes **RAG retrieval results** showing related project context (related Jira stories, Confluence docs, architecture notes) used during analysis
- All processed tickets and reports are persisted and accessible for future review

### Business Success

- Platform used end-to-end on real Jira tickets from ingestion through to verification report
- Report output exportable (PDF/CSV) for external stakeholders
- Session history enables longitudinal tracking of BDD quality across tickets
- **Fine-tuned model demonstrates measurable improvement** over general LLM baseline for test case generation

### Technical Success

- All three GitHub source modes (Exact File Paths, Full Repository, Pull Request) supported
- LLM-based verification achieves ≥85% agreement with human reviewer judgment
- **Fine-tuned model produces test cases with higher relevance and accuracy** than the general LLM baseline
- RAG Q&A retrieves relevant ticket chunks and project knowledge with no hallucinated content
- Jira API errors, GitHub API rate limits, and LLM failures handled gracefully with actionable messages
- Full local deployment via single `docker compose up`
- GitHub Actions CI/CD runs on every PR

### Measurable Outcomes

- ✅ BDD generated for any Jira ticket with valid acceptance criteria
- ✅ ≥85% LLM verification accuracy vs human reviewer baseline
- ✅ Fine-tuned model outperforms general LLM baseline on test coverage, AC relevance, and correctness metrics
- ✅ Export works: `.feature` download, CSV test case download, PDF/CSV report download
- ✅ All results persisted and retrievable per user
- ✅ Zero cross-session/cross-user data leakage
- ✅ RAG retrieval enriches verification with relevant project context

## User Journeys

### Journey 1 — Kai (QA Engineer) — Core Happy Path

**Persona:** Kai is a QA Engineer assigned to write test cases for a new Jira ticket — a 40-line AC document he's never seen. Sprint ends in two days.

**Opening Scene:** Kai opens QE Verification Agent and pastes the Jira ticket URL. The system fetches the ticket (summary, description, AC, labels, linked issues). Instead of reading it top to bottom, he asks the chatbot: *"What are the main edge cases in this ticket?"* The chatbot responds instantly, grounded entirely in the ticket — no hallucinations.

**Rising Action:** Satisfied he understands the ticket, Kai clicks **Generate BDD**. The fine-tuned model produces 8 Gherkin scenarios, each traceable to a specific AC clause. He scans them in the editor — 7 look correct, one needs tweaking. He edits it directly.

**Climax:** Kai selects 3 relevant source files from GitHub and clicks **Verify**. The system retrieves related project context from Confluence and linked Jira stories via RAG, enriching the LLM's analysis. A report appears: *6/8 scenarios pass. Scenario 4 FAILS — the validation logic in `auth_service.py:L142` doesn't handle the null-token edge case the AC specifies.* The report includes an implementation suggestion: *"Add a null-check guard in `validate_token()` before line 142."*

**Resolution:** Kai downloads the report as PDF — which includes the RAG retrieval context (related Confluence docs, linked stories) — and shares it with the developer. A defect that would have been caught in UAT is caught in planning. He downloads the `.feature` file and CSV test case matrix, and moves to the next ticket.

---

### Journey 2 — Priya (Developer) — PR Verification Path

**Persona:** Priya is a Developer who has just opened a PR for a feature she spent three days implementing. Her team uses BDD, but she's unsure if her code covers all scenarios.

**Opening Scene:** Priya opens QE Verification Agent, pastes the Jira ticket ID, and uploads an existing `.feature` file from the repo — skipping BDD generation. She selects **Pull Request** mode and pastes her PR link.

**Rising Action:** The system fetches the PR diff, retrieves related project knowledge (architecture notes, related stories) via RAG, and sends everything to the LLM alongside the BDD.

**Climax:** Report: *9/11 scenarios pass. 2 FAIL — "Given the user has an expired token" and "When the request body is missing required fields".* Justification: *"No handler found in `routes/auth.py` for 401 responses on expired tokens. The `@require_auth` decorator in `middleware/auth.py:L67` only handles missing tokens."* Implementation suggestion: *"Add an `is_token_expired()` check in the `@require_auth` decorator."*

**Resolution:** Priya fixes the two gaps before review. Her PR is approved first time. She exports the traceability report — complete with RAG context and implementation suggestions — and attaches it to the PR as evidence of coverage.

---

### Journey 3 — Kai (QA Engineer) — Error Recovery Path

**Persona:** Same Kai — but he enters GitHub file paths from memory and one is wrong.

**Opening Scene:** Kai types in file paths and clicks Verify. One path doesn't exist.

**Climax:** The system returns a clear error: *"File `src/auth/validator.py` not found in repository. Check the path and try again."* — not a crash, not a silent failure, not a lost session.

**Resolution:** Kai corrects the path and reruns. BDD content and session state are fully preserved.

---

### Journey Requirements Summary

| Journey | Key Capabilities |
|---|---|
| Kai — Happy Path | Jira fetch, RAG Q&A, fine-tuned BDD generation, in-browser editor, exact file path mode, LLM verification with RAG enrichment, per-scenario results with justification + implementation suggestions, PDF/CSV export, DB persistence |
| Priya — PR Verification | Upload custom `.feature` file, PR mode verification, RAG-enriched analysis, traceability report with context, session persistence |
| Kai — Error Recovery | Graceful GitHub API error handling, clear user-facing error messages, session state preserved on failure |

## Domain-Specific Requirements

### Compliance & Regulatory

- No industry-specific regulatory compliance required (no HIPAA, PCI-DSS, GDPR obligations for MVP)
- Standard API usage compliance: Jira REST API terms of service, GitHub API terms of service
- Ticket and code content is sent to an external LLM API — users must be informed their data leaves the system boundary

### Technical Constraints

- Jira API tokens and GitHub PATs must never be logged, stored in plaintext, or exposed in API responses — handled as environment secrets only
- Ticket embeddings in Pinecone must be namespaced per session — no cross-session data leakage
- Project knowledge base embeddings must be namespaced per user/workspace — no cross-user data leakage
- GitHub API 429 rate-limit responses must be handled with retry logic or clear user-facing messaging
- LLM API failures must produce actionable error messages; no silent failures
- Fine-tuned model must be versioned and its performance tracked against the general LLM baseline

## Innovation & Novel Patterns

### Core Innovation Areas

- **Fine-Tuned Model for Test Case Generation:** A domain-specific model trained on real story-to-test-case pairs for BDD generation. Test case generation is a well-bounded task with consistent inputs (Jira descriptions + AC) and structured outputs (BDD scenarios), making it particularly suitable for supervised fine-tuning. This introduces a clear research contribution with measurable evaluation metrics.
- **AI-Powered End-to-End QA Pipeline:** Automates the full requirements-to-verification chain — Jira AC ingestion → Gherkin generation → code-level verification — in a single platform. This collapses three traditionally siloed activities (requirements management, test design, code review) into one workflow.
- **LLM-as-Code-Verifier with RAG Enrichment:** Using a general-purpose LLM to judge per-scenario BDD coverage against actual source code, enriched by project-wide knowledge retrieved via RAG — replacing the manual effort of checking whether implementations match acceptance criteria.
- **Multi-Source RAG for Project Context:** RAG retrieval spanning Jira workspace, Confluence documentation, architecture notes, and project artifacts — grounding the AI reasoning in real project knowledge rather than relying solely on the story description.
- **Scoped RAG for Ticket Comprehension:** RAG constrained to a single Jira ticket gives users precise, hallucination-free understanding of a specific work item.

### Competitive Landscape

AI test generators (e.g. Copilot) generate tests from code, not from AC. BDD tools (Behave, Cucumber) execute tests but don't generate or verify them. Jira AI summarises tickets but doesn't bridge to code verification. QE Verification Agent uniquely delivers the AC→BDD→Code traceability loop with fine-tuned generation and RAG-enriched verification in a single workflow.

### Innovation Validation

- Fine-tuned model evaluation: compare test coverage, AC relevance, and correctness against a general LLM baseline across 20+ real Jira tickets
- RAG accuracy: manually compare chatbot answers against ticket content for a sample of real tickets
- BDD generation quality: measure human-edit percentage across 20+ real Jira tickets
- LLM verification accuracy: compare verdicts against human reviewer consensus (target ≥85% agreement)

### Innovation Risks

- **Fine-tuned model accuracy insufficient** → Fallback: continue using general LLM with enhanced prompting
- **LLM verification accuracy insufficient** → Fallback: sandboxed Cucumber execution (Growth tier)
- **RAG hallucination** → Prompt-level grounding constraint; tested against known ticket content
- **Multi-source RAG noise** → Retrieval ranking and relevance filtering to prevent irrelevant context from degrading LLM performance
- **LLM provider reliability** → Provider interface abstracts Claude ↔ OpenAI; swappable without code changes

## B2B SaaS Platform Requirements

### Authentication & User Model

- **Auth provider:** Supabase Auth (email/password + OAuth)
- **User role:** Single role — `user` — for all authenticated accounts; no RBAC
- **Session scoping:** All Jira sessions, BDD content, and verification reports stored and retrieved per `user_id`
- **Access control:** All application features require authentication; no public or anonymous access

### Data Model & Persistence

- **Relational DB:** Supabase PostgreSQL — sessions, chat history, BDD files, verification results, all linked to `user_id`
- **Vector DB:** Pinecone — RAG embeddings for Jira ticket chunks + project knowledge base, namespaced per session/workspace
- **Storage:** Supabase Storage — generated reports (PDF/CSV), test artifacts, uploaded `.feature` files
- **ORM:** SQLAlchemy (relational layer)
- **Data isolation:** Users access only their own sessions, reports, and knowledge base

### Subscription & Pricing

- No subscription tiers for MVP — all features available to all authenticated users

### External Integrations (MVP)

| Integration | Method | Auth |
|---|---|---|
| Jira REST API v3 | HTTP REST / MCP Server | API Token (per user, env/secret) |
| GitHub REST API | HTTP REST / MCP Server | PAT or OAuth (TBD) |
| LLM Provider API (Verification/RAG) | HTTP REST | API Key (Claude or OpenAI, TBD) |
| Fine-Tuned Model API (BDD Generation) | HTTP REST | API Key / Model endpoint |
| Pinecone | REST / SDK | API Key |
| Supabase | SDK / REST | Service role key (backend) + anon key (frontend) |
| Confluence REST API | HTTP REST | API Token |

### Technical Architecture

- **Frontend:** Next.js on Vercel — Next.js API routes used as lightweight BFF where needed
- **Backend:** FastAPI/Python/uv — fully Dockerised, stateless, cloud-agnostic (hosting TBD: Railway, Render, AWS, GCP)
- **LLM abstraction:** Provider interface module enables Claude ↔ OpenAI swapping without code changes; separate abstraction for fine-tuned model
- **MCP Integration:** Model Context Protocol servers for secure AI-tool interaction with GitHub and Jira APIs
- **Storage:** Supabase Storage for reports, test artifacts, and uploaded files
- **CI/CD:** GitHub Actions — lint and tests on every PR; deployment pipeline TBD per hosting choice
- **Local development:** Docker Compose orchestrates Next.js dev server, FastAPI server, and Supabase local emulator
- **Secrets:** All API tokens and keys managed via environment variables — never hardcoded

## Project Scoping & Phased Development

### MVP Strategy

**Approach:** Problem-Solving MVP — deliver the complete pipeline end-to-end (Jira → RAG Q&A → BDD generation → GitHub source selection → LLM verification with RAG enrichment → report with implementation suggestions → persist), proving the full workflow works reliably before adding advanced analysis features.

**Resource Requirements:** Solo developer or small team (2–3 people); estimated 3–4 months.

### Phase 1 — MVP

**User Journeys Supported:** Kai (Happy Path), Priya (PR Verification), Kai (Error Recovery)

**Must-Have Capabilities:**
- Supabase Auth: email/password + OAuth login/signup
- Module 1: Jira ticket ingestion + Pinecone embedding + RAG Q&A chatbot (ticket comprehension)
- Module 2: BDD generation (initially using general LLM, with fine-tuned model integration as available), in-browser editor, upload custom `.feature`, download `.feature`, download CSV test cases
- Module 3: All three GitHub source input modes + LLM-based per-scenario pass/fail with justification + implementation suggestions + overall pass %
- RAG Enrichment: Multi-source project knowledge retrieval (Jira workspace, Confluence) enriching verification analysis
- Traceability: AC clause → BDD scenario → verification outcome
- Verification Output: RAG retrieval results (related stories, Confluence docs, project context) + GitHub repo links used
- Export: PDF report + CSV report (including RAG context and implementation suggestions)
- Storage: Supabase Storage for reports and test artifacts
- User-scoped session history
- Docker Compose for local development

### Phase 2 — Growth

- Fine-tuned model training pipeline and integration for BDD generation
- Fine-tuned model evaluation: systematic comparison vs general LLM baseline
- MCP server integration for GitHub and Jira APIs
- Confluence deep integration for project knowledge base
- Failure clustering by root cause category (missing implementation, logic mismatch, incomplete endpoint)
- Sandboxed Cucumber test execution (if LLM accuracy insufficient)
- Configurable Pinecone chunking strategy (sentence-level vs paragraph-level)
- CI/CD deployment pipeline (backend hosting finalised)
- Async DB operations (if sync insufficient at scale)

### Phase 3 — Expansion

- Multi-user/team workspaces with shared session history, dashboards, and shared knowledge base
- Historical analytics across tickets and sprints
- CI/CD plugin (trigger verification from GitHub Actions)
- Per-user LLM provider selection UI (Claude vs OpenAI)
- Model performance dashboard (fine-tuned vs general LLM metrics over time)

### Risk Mitigation

- **Technical:** LLM verification accuracy — target ≥85% vs human baseline; sandboxed Cucumber as Phase 2 fallback. Pinecone chunking — test 2–3 strategies early. Fine-tuned model — general LLM serves as baseline fallback.
- **Market:** Single-user MVP keeps feedback loop tight; validate with real tickets before multi-user features.
- **Resource:** Modular architecture — each module built and validated independently. MVP can ship with general LLM for BDD generation if fine-tuned model is not ready.

## Functional Requirements

### Authentication & User Management

- FR1: A visitor can register for an account using email and password
- FR2: A visitor can sign up or log in using an OAuth provider
- FR3: An authenticated user can log out of their session
- FR4: An unauthenticated user is denied access to all application features

### Jira Ticket Ingestion

- FR5: An authenticated user can submit a Jira ticket URL or ticket ID to ingest it
- FR6: The system can fetch ticket content from Jira (Summary, Description, Acceptance Criteria, Labels, Linked Issues) using the user's Jira credentials
- FR7: The system can store and index ticket content as vector embeddings for retrieval
- FR8: The system notifies the user with a clear, actionable message when Jira ingestion fails (invalid ticket, auth failure, API unavailability)

### RAG-Powered Ticket Q&A (Module 1)

- FR9: An authenticated user can ask natural-language questions about an ingested Jira ticket via a chat interface
- FR10: The system answers user questions using only content retrieved from the ingested ticket — no cross-session data, no external knowledge
- FR11: The system maintains chat history per session, associated with the authenticated user
- FR12: An authenticated user can view past chat history for a previously ingested ticket session

### BDD Generation & Editing (Module 2)

- FR13: An authenticated user can trigger BDD generation from an ingested Jira ticket's acceptance criteria
- FR14: The system produces Gherkin scenarios (Feature / Scenario / Given / When / Then) with each scenario traceable to a specific acceptance criteria clause
- FR15: An authenticated user can view and edit generated BDD content directly in the browser
- FR16: An authenticated user can download the current BDD content as a `.feature` file
- FR17: An authenticated user can download a structured CSV test case file derived from the current BDD content (columns: Scenario, Given, When, Then, Expected Result)
- FR18: An authenticated user can upload an existing `.feature` file to use as BDD input, bypassing generation

### GitHub-Based Code Verification (Module 3)

- FR19: An authenticated user can select one of three GitHub source input modes: Exact File Paths, Full Repository, or Pull Request
- FR20: In Exact File Paths mode, the user can specify one or more file paths in a GitHub repository to fetch for verification
- FR21: In Full Repository mode, the user can provide a GitHub repository URL and the system fetches the full file tree and contents
- FR22: In Pull Request mode, the user can provide a PR link and the system fetches the PR diff/patch
- FR23: The system sends fetched code and BDD scenarios to an LLM to evaluate whether each scenario is covered by the code
- FR24: The system displays a per-scenario pass/fail verdict with a natural-language justification referencing specific code sections (file, function/class)
- FR25: The system displays an overall pass percentage across all verified scenarios
- FR26: The system notifies the user with a clear, actionable message when a GitHub source fetch fails (file not found, invalid path, rate limit, API unavailability), preserving session state
- FR27: A failed verification attempt does not destroy BDD content or session state — the user can correct input and retry without data loss

### RAG-Enriched Verification Context (NEW)

- FR35: During verification, the system retrieves contextually relevant information from the project knowledge base (related Jira stories, Confluence docs, architecture notes) via RAG
- FR36: The verification output includes the RAG retrieval results (related project context) alongside the per-scenario verdicts
- FR37: The verification output includes the GitHub repository links and file references used during code analysis
- FR38: For failed scenarios, the system provides implementation suggestions enabling developers to review potential improvements

### Project Knowledge Base (NEW)

- FR39: An authenticated user can ingest project knowledge sources (Confluence pages, related Jira stories) into the vector database for RAG retrieval
- FR40: Ingested project knowledge is indexed and namespaced per user/workspace to prevent cross-user data leakage
- FR41: The system can retrieve contextually relevant knowledge from the project knowledge base during verification

### File Storage (NEW)

- FR42: Generated reports (PDF/CSV) are stored in a storage bucket and accessible for download
- FR43: Uploaded `.feature` files are stored in a storage bucket linked to the session
- FR44: Test artifacts produced during verification are persisted in a storage bucket

### Traceability & Reporting

- FR28: The system produces a traceability report linking each acceptance criteria clause to its BDD scenario and verification outcome
- FR29: An authenticated user can export the verification report as a PDF file
- FR30: An authenticated user can export the verification report as a CSV file
- FR45: The exported report includes RAG retrieval context (related stories, Confluence docs, project knowledge) used during verification
- FR46: The exported report includes implementation suggestions for failed scenarios

### Session History & Persistence

- FR31: The system persists all ticket sessions, chat history, BDD content, and verification results to a database associated with the authenticated user
- FR32: An authenticated user can view a list of their past sessions (previously processed tickets)
- FR33: An authenticated user can retrieve and view a previously saved session, including its BDD content and verification report
- FR34: An authenticated user can only access their own sessions and reports — not those of other users

## Non-Functional Requirements

### Performance

- NFR-P1: Jira ticket ingestion (fetch → chunk → embed) completes within 30 seconds for a typical ticket (up to 5,000 words), with a visible progress indicator during processing
- NFR-P2: RAG chatbot returns an answer within 10 seconds under normal operating conditions
- NFR-P3: BDD generation response returned within 30 seconds for a ticket with up to 20 acceptance criteria clauses
- NFR-P4: GitHub code verification report returned within 60 seconds for up to 15 BDD scenarios verified against up to 10,000 lines of code
- NFR-P5: The application UI remains responsive during all long-running background operations — the user never sees a frozen interface
- NFR-P6: Project knowledge base RAG retrieval completes within 10 seconds during verification

### Security

- NFR-S1: All credentials (Jira API tokens, GitHub PATs, LLM API keys) stored and transmitted exclusively via environment variables or encrypted secrets — never hardcoded, logged, or included in API responses
- NFR-S2: All data in transit between client, backend, and external APIs encrypted via TLS (HTTPS)
- NFR-S3: All data at rest in the database encrypted at rest by the managed database platform
- NFR-S4: Vector embeddings namespaced per session — no cross-user or cross-session retrieval permitted under any condition
- NFR-S5: All authenticated API endpoints validate the user's session token before processing — unauthenticated requests receive HTTP 401
- NFR-S6: A user's session data, BDD content, and verification reports are inaccessible to other users, even if the session identifier is known
- NFR-S7: Internal error details (stack traces, DB query errors, file paths) are never exposed in API responses
- NFR-S8: Project knowledge base embeddings are namespaced per user/workspace — no cross-user project knowledge retrieval

### Reliability & Error Handling

- NFR-R1: When any external API (Jira, GitHub, LLM, Confluence) is unavailable or returns an error, the system surfaces a clear, human-readable error message — no silent failures, no raw error codes
- NFR-R2: GitHub API rate-limit responses (HTTP 429) handled gracefully with automatic retry logic or a clear user-facing retry message
- NFR-R3: A failed verification attempt does not destroy or overwrite BDD content or session state
- NFR-R4: The LLM provider integration is abstracted behind a provider interface — switching between LLM providers (Claude ↔ OpenAI) requires no changes to calling application code
- NFR-R5: The application runs correctly in local development via a single `docker compose up` command, requiring only environment variable configuration
- NFR-R6: The fine-tuned model and general LLM are independently swappable — failure of one does not block the other

### Scalability

> MVP targets a single authenticated user or very small team (1–5 users). Scalability hardening (connection pooling, async DB operations, horizontal scaling, caching) is deferred to Phase 2.
