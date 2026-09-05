# Maintenance Record — Ingest Confluence / Jira by URL

**Date:** 2026-08-22
**Kind:** Small full-stack increment (schema + service + UI), performed outside the story workflow
**Status:** ✅ Implemented. Backend 639 passed, Ruff introduces no new violations; frontend `tsc` clean, ESLint clean, 164 passed / 2 skipped.
**Schema:** none — request-body change only, no migration
**Amends:** Stories 4.1, 4.2, 4.5 (each carries a pointer to this record)

---

## 1. What

Both ingestion forms gained a **method selector** and a URL path:

| Source | Whole (existing default) | New |
|---|---|---|
| Confluence | Space key | **Page URLs** — page URLs and/or bare numeric ids, one per line |
| Jira | Project key + sprint/label | **Ticket URLs** — issue URLs and/or bare keys, one per line |

Previously you could only take an entire space or an entire project. Ingesting
the four pages that actually describe a feature meant ingesting the space.

## 2. The fetch step was the only thing that had to change

`jira_service.fetch_ticket_content` and `confluence_service.fetch_page_by_id`
return the **same types** as the bulk fetchers (`JiraTicketContent`,
`ConfluencePage`). So each ingest generator swaps only how it obtains the list;
the chunking, embedding, SSE progress and `knowledge_sources` persistence below
it are untouched and remain shared between both paths.

`fetch_ticket_content` already accepted a URL or a bare key — the same parser
the ticket-ingestion flow uses — so Jira needed no new parsing at all.

## 3. Confluence page-id extraction

New `confluence_service.extract_page_id(ref)`. Handles:

```
.../wiki/spaces/ENG/pages/123456/Title    → 123456
.../wiki/spaces/ENG/pages/123456          → 123456
.../pages/viewpage.action?pageId=123456   → 123456
123456                                    → 123456
```

**Short `/wiki/x/AbCdEf` links are rejected**, not silently skipped: they carry
no id and resolving one needs an extra API round trip. The error names the
offending input, so one bad line in a pasted list says which line to fix.

## 4. Deduplication

Both paths deduplicate before fetching/embedding:

- Confluence by extracted **page id** — the same page pasted as a URL and as an
  id is one page.
- Jira by resolved **ticket key** — same reasoning.

Without this, a duplicate line re-embeds the same content under the same
deterministic vector ids. Harmless in the store, but it double-counts
`ingested_count` and wastes an embeddings call.

## 5. Request contract

`ConfluenceIngestRequest` gained `page_refs: list[str] | None`;
`JiraIngestRequest` gained `ticket_refs: list[str] | None` and its
`project_key` became **optional**.

Precedence is explicit and checked in this order: `page_refs` → `space_key` →
`page_id` (and `ticket_refs` → `project_key`). The older fields are kept so
existing callers keep working; new callers should use the `*_refs` form, which
also accepts a bare id/key.

Two guards preserve the previous 422 behaviour:

- Both `*_refs` validators **drop blank entries** and collapse an all-blank list
  to `None` — blank lines are how a pasted list ends, not an error — so an empty
  paste still fails the "provide something" check rather than reaching the API.
- `JiraIngestRequest` has a model validator requiring `project_key` **or**
  `ticket_refs`. A body with neither is still a 422, which is exactly what
  callers got when `project_key` was mandatory; the existing
  `test_missing_project_key_returns_422` passes unchanged.

## 6. UI

`MethodSelector` in `KnowledgeBasePanel` is a `role="tablist"` segmented control
matching `GitHubSourceSelector`, so "pick how, then fill in what" reads
identically wherever the app asks it. The whole-space / whole-project method
stays the **default** — it is what the forms did before, and changing the
default would silently alter what an existing user's muscle memory produces.

`parseRefs` splits on newlines **and commas**, since pasting a comma-separated
list is common, and trims and drops blanks. The frontend does no URL parsing —
it sends the raw refs and lets the backend own the shape, so the two ends cannot
drift on what a valid reference looks like.

## 7. Tests

**Backend** — `extract_page_id` across every supported shape and every rejected
one; blank-ref stripping on both schemas; Jira accepting `ticket_refs` with no
project key; a body with neither source still rejected; project-key format
validation still enforced.

**Frontend** — both URL methods send the parsed refs (blank lines dropped,
commas split); both block an empty submit with an actionable message; the
project-key method is still the Jira default.

One existing test changed meaning: "blocks Confluence submit with no space key
or page ID" is now "…with no space key", because the page ID moved into the URL
method and the whole-space method legitimately needs only a space key.
