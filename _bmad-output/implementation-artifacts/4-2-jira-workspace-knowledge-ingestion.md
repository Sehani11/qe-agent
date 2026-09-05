# Story 4.2: Jira Workspace Knowledge Ingestion

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-projects-and-credential-scoping.md)): Jira credentials come from the active project when it has them, resolved once per ingestion run so a batch cannot span two tenants. Tickets are embedded into the project's namespace.


Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-ingest-by-url.md)): `JiraIngestRequest.project_key` is now OPTIONAL, paired with a new `ticket_refs` (issue URLs and/or bare keys) and a model validator requiring one of the two — a body with neither is still a 422. The ticket path reuses `fetch_ticket_content`, which already accepted a URL or a key, and deduplicates by resolved ticket key.

## Story

As an **authenticated user**,
I want to ingest related Jira stories and project metadata into the knowledge base,
So that the verification process can reference historical context and related work.

## Acceptance Criteria

1. **Given** an authenticated user has Jira credentials configured (JIRA_BASE_URL, JIRA_API_TOKEN, JIRA_USER_EMAIL)
   **When** a POST request is made to `/api/v1/knowledge/ingest/jira` with a `project_key` and optional `sprint`/`label` filters
   **Then** the FastAPI `knowledge_service.py` fetches matching Jira tickets via a new `fetch_tickets_from_project()` method in `jira_service.py`, chunks the ticket text, and embeds it into Pinecone under namespace `{user_id}:knowledge` (FR39, FR40)

2. **And** each successfully ingested ticket is tracked in the `knowledge_sources` table with `source_type="jira"`, `source_url` set to the Jira browse URL, `title` set to the ticket summary, and `ingestion_status="completed"`

3. **And** the ingestion streams SSE progress events (`type: progress`) for each ticket being processed and a final `type: complete` event with `ingested_count`

4. **And** if Jira credentials are not configured, the SSE stream yields a `type: error` event with `error: "JIRA_NOT_CONFIGURED"` instead of crashing

5. **And** knowledge source data is namespaced per user — the Pinecone namespace is always `f"{user_id}:knowledge"` derived from the JWT, never `settings.dev_user_id` or another user's ID (NFR-S8)

## Tasks / Subtasks

- [x] Task 1: Add `fetch_tickets_from_project()` to `jira_service.py` (AC: 1, 4)
  - [x] Add `_check_jira_credentials()` helper that raises `JiraServiceError(code="JIRA_NOT_CONFIGURED")` if `jira_base_url` or `jira_api_token` is empty
  - [x] Implement `fetch_tickets_from_project(project_key, sprint=None, label=None)` using Jira Search API v3 (`GET /rest/api/3/search`)
  - [x] Build JQL string: base = `project={key} ORDER BY created ASC`, append `AND sprint="{sprint}"` and/or `AND labels="{label}"` when provided
  - [x] Paginate with `startAt` / `maxResults=50` until `startAt + len(results) >= total`
  - [x] For each result, call existing `_extract_text_from_jira_adf()` for description and construct a `JiraTicketContent` object using existing helpers
  - [x] Use `httpx.BasicAuth(settings.jira_user_email, settings.jira_api_token)` (consistent with `confluence_service.py`)

- [x] Task 2: Add `JiraIngestRequest` schema to `backend/app/schemas/knowledge.py` (AC: 1)
  - [x] Add `JiraIngestRequest(BaseModel)` with fields: `project_key: str`, `sprint: str | None = None`, `label: str | None = None`

- [x] Task 3: Add `ingest_jira()` async generator to `knowledge_service.py` (AC: 1, 2, 3, 4, 5)
  - [x] Reuse existing `_sse()`, `_embed_chunks()`, `_upsert_to_pinecone()` helpers (do not duplicate)
  - [x] Namespace = `f"{user_id}:knowledge"` (identical to `ingest_confluence`)
  - [x] Text body for each ticket: `f"Summary: {t.summary}\n\nDescription: {t.description}\n\nAcceptance Criteria: {t.acceptance_criteria}"`
  - [x] Vector ID format: `f"jira_{ticket.ticket_id}_chunk_{i}"`
  - [x] Per-ticket try/except: on embed failure, yield progress error event + persist `KnowledgeSource(ingestion_status="failed")`
  - [x] On empty result list, yield `type: complete, ingested_count: 0` (no DB rows)

- [x] Task 4: Add `POST /ingest/jira` route to `backend/app/api/v1/knowledge.py` (AC: 1)
  - [x] Import `JiraIngestRequest` from `app.schemas.knowledge`
  - [x] Validate `request.project_key` is non-empty → 422 if missing
  - [x] Return `StreamingResponse(knowledge_service.ingest_jira(...), media_type="text/event-stream")`

- [x] Task 5: Frontend — extend `types/knowledge.ts` and `hooks/useKnowledge.ts` (AC: 3)
  - [x] Add `JiraIngestRequest` interface to `frontend/src/lib/types/knowledge.ts`
  - [x] Add `useIngestJira(options?)` hook to `frontend/src/lib/hooks/useKnowledge.ts` following the exact same pattern as `useIngestConfluence` (SSE fetch + Supabase auth header)

- [x] Task 6: Tests — add `backend/tests/test_jira_knowledge.py` (AC: 1–5)
  - [x] Route: 200 with valid `project_key`, 422 without `project_key`, 401 without auth
  - [x] Service: progress+complete events, namespace isolation, `JiraServiceError` → SSE error, DB row on success, empty result set, cross-user isolation, embed failure → failed DB row

## Dev Notes

### Critical Architecture Rules (must not violate)

1. **Namespace isolation**: Pinecone namespace = `f"{user_id}:knowledge"` derived exclusively from `get_current_user()` JWT — never `settings.dev_user_id`
2. **No business logic in routes**: All Jira API calls go through `jira_service.py` — the route handler only validates `project_key` and returns `StreamingResponse`
3. **Reuse existing helpers**: `_sse()`, `_embed_chunks()`, `_upsert_to_pinecone()` are already in `knowledge_service.py` — import them, do not duplicate
4. **Reuse existing model**: `KnowledgeSource` in `models/knowledge_source.py` — no new model or migration needed

### Existing Code to Reuse — Do Not Reinvent

| What | Location | How it's used |
|------|----------|---------------|
| `JiraTicketContent` | `app/services/jira_service.py:11` | Already the return type; `fetch_tickets_from_project()` should return `list[JiraTicketContent]` |
| `JiraServiceError` | `app/services/jira_service.py:22` | Already has `message` + `code` attrs — same pattern as `ConfluenceServiceError` |
| `_extract_text_from_jira_adf()` | `app/services/jira_service.py:174` | Reuse for description extraction in new batch method |
| `_extract_acceptance_criteria()` | `app/services/jira_service.py:195` | Reuse for AC extraction per ticket |
| `chunk_text()` | `app/services/vector_service.py` | Same import as `ingest_confluence` |
| `_sse()`, `_embed_chunks()`, `_upsert_to_pinecone()` | `app/services/knowledge_service.py` | Already module-level functions — call directly in `ingest_jira()` |
| `KnowledgeSource` ORM model | `app/models/knowledge_source.py` | `source_type="jira"` differentiates from `"confluence"` |

### jira_service.py — New Method to Add

```python
async def fetch_tickets_from_project(
    project_key: str,
    sprint: str | None = None,
    label: str | None = None,
) -> list[JiraTicketContent]:
    """Fetch all Jira tickets from a project using the Search API v3."""
    _check_jira_credentials()

    base = settings.jira_base_url.rstrip("/")
    auth = httpx.BasicAuth(settings.jira_user_email, settings.jira_api_token)

    jql_parts = [f"project={project_key}"]
    if sprint:
        jql_parts.append(f'sprint="{sprint}"')
    if label:
        jql_parts.append(f'labels="{label}"')
    jql_parts.append("ORDER BY created ASC")
    jql = " AND ".join(jql_parts[:-1]) + " " + jql_parts[-1]
    # becomes: project=PROJ AND sprint="Sprint 1" AND labels="qa" ORDER BY created ASC

    url = f"{base}/rest/api/3/search"
    tickets: list[JiraTicketContent] = []
    start_at = 0
    max_results = 50

    async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
        while True:
            try:
                response = await client.get(url, params={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": max_results,
                    "fields": "summary,description,labels,issuelinks",
                    "expand": "names",
                })
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise JiraServiceError(
                    f"Jira API returned {exc.response.status_code} for project '{project_key}'."
                ) from exc
            except httpx.RequestError as exc:
                raise JiraServiceError(f"Failed to connect to Jira: {exc}") from exc

            data = response.json()
            issues = data.get("issues", [])
            field_names = data.get("names", {})

            for issue in issues:
                fields = issue.get("fields", {})
                summary = fields.get("summary", "")
                description = _extract_text_from_jira_adf(fields.get("description"))
                labels = fields.get("labels", [])
                linked_issues = []
                for link in fields.get("issuelinks", []):
                    if "outwardIssue" in link:
                        linked_issues.append(link["outwardIssue"].get("key"))
                    elif "inwardIssue" in link:
                        linked_issues.append(link["inwardIssue"].get("key"))
                ac_text = _extract_acceptance_criteria(fields, field_names, description, summary)
                tickets.append(JiraTicketContent(
                    ticket_id=issue["key"],
                    summary=summary,
                    description=description,
                    acceptance_criteria=ac_text,
                    labels=labels,
                    linked_issues=[li for li in linked_issues if li],
                ))

            total = data.get("total", 0)
            start_at += len(issues)
            if start_at >= total or not issues:
                break

    return tickets


def _check_jira_credentials() -> None:
    if not settings.jira_base_url or not settings.jira_api_token:
        raise JiraServiceError(
            "Jira credentials not configured. Set JIRA_BASE_URL and JIRA_API_TOKEN.",
            code="JIRA_NOT_CONFIGURED",
        )
```

**Important:** Add `_check_jira_credentials()` before `fetch_ticket_content()` in the file (order matters for readability; place after `_mock_fetch_ticket`).

### knowledge_service.py — New Function to Add

```python
async def ingest_jira(
    user_id: str,
    request: JiraIngestRequest,
    db: AsyncSession,
) -> AsyncGenerator[str, None]:
    """Stream SSE events while ingesting Jira project tickets into the knowledge base."""
    namespace = f"{user_id}:knowledge"

    yield _sse({"type": "progress", "message": "Connecting to Jira..."})

    try:
        tickets = await jira_service.fetch_tickets_from_project(
            request.project_key,
            sprint=request.sprint,
            label=request.label,
        )
    except JiraServiceError as exc:
        yield _sse({"type": "error", "error": exc.code, "message": exc.message})
        return
    except Exception:
        yield _sse({
            "type": "error",
            "error": "KNOWLEDGE_INGEST_FAILED",
            "message": "Failed to fetch from Jira. Please try again.",
        })
        return

    if not tickets:
        yield _sse({"type": "complete", "ingested_count": 0})
        return

    total = len(tickets)
    ingested_count = 0

    for i, ticket in enumerate(tickets, start=1):
        yield _sse({
            "type": "progress",
            "message": f"Processing: {ticket.ticket_id} — {ticket.summary}",
            "current": i,
            "total": total,
        })

        try:
            body = (
                f"Summary: {ticket.summary}\n\n"
                f"Description: {ticket.description}\n\n"
                f"Acceptance Criteria: {ticket.acceptance_criteria}"
            )
            chunks = chunk_text(body)
            if not chunks:
                yield _sse({
                    "type": "progress",
                    "message": f"Skipped (no content): {ticket.ticket_id}",
                    "current": i,
                    "total": total,
                })
                continue

            embeddings = await _embed_chunks(chunks)
            await _upsert_to_pinecone(ticket.ticket_id, chunks, embeddings, namespace)

            source_url = (
                f"{settings.jira_base_url.rstrip('/')}/browse/{ticket.ticket_id}"
            )
            db.add(KnowledgeSource(
                user_id=user_id,
                source_type="jira",
                source_url=source_url,
                title=ticket.summary,
                page_count=1,
                ingestion_status="completed",
            ))
            await db.commit()
            ingested_count += 1

        except Exception:
            logger.exception("Failed to embed/persist ticket '%s'", ticket.ticket_id)
            yield _sse({
                "type": "progress",
                "message": f"Failed to process: {ticket.ticket_id}",
                "current": i,
                "total": total,
            })
            db.add(KnowledgeSource(
                user_id=user_id,
                source_type="jira",
                source_url=f"{settings.jira_base_url.rstrip('/')}/browse/{ticket.ticket_id}",
                title=ticket.summary,
                page_count=0,
                ingestion_status="failed",
            ))
            await db.commit()

    yield _sse({"type": "complete", "ingested_count": ingested_count})
```

**Imports to add in `knowledge_service.py`:**
- `from app.schemas.knowledge import ConfluenceIngestRequest, JiraIngestRequest`
- `from app.services import jira_service` (note: `jira_service` is not yet imported in `knowledge_service.py` — add it)
- `from app.services.jira_service import JiraServiceError`

### knowledge.py (API router) — New Route

```python
@router.post("/ingest/jira")
async def ingest_jira(
    request: JiraIngestRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Ingest Jira project tickets into the user's knowledge base."""
    if not request.project_key:
        return JSONResponse(
            status_code=422,
            content={
                "error": "INVALID_REQUEST",
                "message": "project_key is required.",
                "code": 422,
            },
        )

    stream = knowledge_service.ingest_jira(
        user_id=current_user,
        request=request,
        db=db,
    )
    return StreamingResponse(stream, media_type="text/event-stream")
```

**Import to add in `knowledge.py`:** `from app.schemas.knowledge import ConfluenceIngestRequest, JiraIngestRequest`

### Frontend — types/knowledge.ts addition

```typescript
export interface JiraIngestRequest {
    project_key: string;
    sprint?: string;
    label?: string;
}
```

### Frontend — hooks/useKnowledge.ts addition

Add `useIngestJira` with **identical structure** to `useIngestConfluence` — same Supabase auth header, same SSE parsing, same state shape. Only the endpoint and request type differ:

```typescript
export function useIngestJira(
    options?: UseIngestConfluenceOptions  // reuse same options interface
): UseIngestConfluenceResult {
    // ...identical body to useIngestConfluence...
    // endpoint: `${API_BASE_URL}/api/v1/knowledge/ingest/jira`
    // body: JSON.stringify(request as JiraIngestRequest)
}
```

Consider extracting a shared `_useSSEIngest` internal helper if both functions exceed 30 lines each, but only if it doesn't add complexity.

### Testing Strategy

Follow the exact same test structure as `test_knowledge.py`. Create `backend/tests/test_jira_knowledge.py`:

**Route tests (`TestIngestJiraRoute`):**
- `test_valid_project_key_returns_200` — mock `fetch_tickets_from_project`, `_embed_chunks`, `_upsert_to_pinecone`
- `test_missing_project_key_returns_422`
- `test_missing_auth_returns_401` — clear `app.dependency_overrides`

**Service tests (`TestIngestJiraService`):**
- `test_success_yields_progress_and_complete_events`
- `test_namespace_isolation_uses_user_id_knowledge` — capture `namespace` arg to `_upsert_to_pinecone`
- `test_jira_service_error_yields_sse_error_event` — `side_effect=JiraServiceError("msg", code="JIRA_NOT_CONFIGURED")`
- `test_knowledge_source_row_created_on_success` — assert `db.add.call_args[0][0].source_type == "jira"`
- `test_empty_ticket_list_yields_complete_with_zero`
- `test_cross_user_namespace_isolation`
- `test_embed_failure_yields_progress_and_creates_failed_record`

**Mock targets** (exact paths for patching):
- `"app.services.knowledge_service.jira_service.fetch_tickets_from_project"`
- `"app.services.knowledge_service._embed_chunks"`
- `"app.services.knowledge_service._upsert_to_pinecone"`

### Project Structure Notes

Files to create or modify:

**Modify (add to):**
- `backend/app/services/jira_service.py` — add `_check_jira_credentials()` + `fetch_tickets_from_project()`
- `backend/app/schemas/knowledge.py` — add `JiraIngestRequest`
- `backend/app/services/knowledge_service.py` — add `ingest_jira()` + new imports
- `backend/app/api/v1/knowledge.py` — add `POST /ingest/jira` route
- `frontend/src/lib/types/knowledge.ts` — add `JiraIngestRequest` interface
- `frontend/src/lib/hooks/useKnowledge.ts` — add `useIngestJira()` hook

**Create new:**
- `backend/tests/test_jira_knowledge.py` — full test suite

**No migration needed** — `knowledge_sources` table from Story 4.1 supports `source_type="jira"` natively.

### Jira API Reference

- **Search endpoint**: `GET {jira_base_url}/rest/api/3/search`
- **Key params**: `jql`, `startAt`, `maxResults`, `fields`, `expand=names`
- **Auth**: `httpx.BasicAuth(settings.jira_user_email, settings.jira_api_token)`
- **Pagination**: Response has `total`, `startAt`, `maxResults`, `issues[]`. Continue while `startAt < total`.
- **JQL filter operators**: `AND sprint="Sprint Name"`, `AND labels="my-label"` — quotes required for values with spaces

### References

- [Source: `backend/app/services/jira_service.py`] — existing helpers, `JiraTicketContent`, `JiraServiceError`
- [Source: `backend/app/services/confluence_service.py`] — `_check_credentials()` pattern to mirror
- [Source: `backend/app/services/knowledge_service.py`] — `ingest_confluence()`, `_sse()`, `_embed_chunks()`, `_upsert_to_pinecone()` to reuse
- [Source: `backend/app/api/v1/knowledge.py`] — `ingest_confluence` route to mirror exactly
- [Source: `backend/app/schemas/knowledge.py`] — `ConfluenceIngestRequest` to mirror for `JiraIngestRequest`
- [Source: `backend/app/core/config.py:42-44`] — `jira_base_url`, `jira_api_token`, `jira_user_email` already present
- [Source: `backend/tests/test_knowledge.py`] — test structure and fixture patterns to replicate
- [Source: `frontend/src/lib/hooks/useKnowledge.ts`] — `useIngestConfluence()` to mirror for `useIngestJira()`

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Added `_check_jira_credentials()` and `fetch_tickets_from_project()` to `jira_service.py`; paginated Jira Search API v3 with JQL filter support (sprint, label)
- Added `JiraIngestRequest` schema; extended `knowledge_service.py` with `ingest_jira()` reusing all existing helpers; no new migration (reuses `knowledge_sources` table with `source_type="jira"`)
- Added `POST /ingest/jira` route mirroring `ingest_confluence` pattern exactly
- Frontend: added `JiraIngestRequest` type + `useIngestJira()` hook with Supabase auth header (same pattern as `useIngestConfluence`)
- Added `test_jira_knowledge.py` with 11 tests: 4 route tests + 7 service tests; 27/27 tests passing, ruff clean
- Also cleaned up pre-existing ruff violations (E501, B904, W293) in `fetch_ticket_content()` in `jira_service.py`

### File List

- `backend/app/services/jira_service.py` (modified)
- `backend/app/schemas/knowledge.py` (modified)
- `backend/app/services/knowledge_service.py` (modified)
- `backend/app/api/v1/knowledge.py` (modified)
- `backend/tests/test_jira_knowledge.py` (new)
- `frontend/src/lib/types/knowledge.ts` (modified)
- `frontend/src/lib/hooks/useKnowledge.ts` (modified)
