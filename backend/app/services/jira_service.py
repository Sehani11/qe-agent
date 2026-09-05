"""Jira ticket ingestion service."""

import re

import httpx
from pydantic import BaseModel

from app.services.project_config_service import (
    JiraCredentials,
    jira_credentials_for,
    missing_credentials_message,
)


class JiraTicketContent(BaseModel):
    """Structured content extracted from a Jira ticket."""

    ticket_id: str
    summary: str
    description: str
    acceptance_criteria: str
    labels: list[str]
    linked_issues: list[str]


class JiraServiceError(Exception):
    """Custom error for Jira API failures."""

    def __init__(self, message: str, code: str = "JIRA_FETCH_FAILED"):
        self.message = message
        self.code = code
        super().__init__(self.message)


async def fetch_ticket_content(
    ticket_id_or_url: str, credentials: JiraCredentials | None = None
) -> JiraTicketContent:
    """Fetch and extract content from a Jira ticket.

    Args:
        ticket_id_or_url: The Jira ticket ID (e.g., PROJ-123) or URL.
        credentials: Which Jira to talk to and as whom. None falls back to the
            environment, which is what callers with no project context get.

    Returns:
        Structured JiraTicketContent.

    Raises:
        JiraServiceError: If the fetch fails.
    """
    creds = credentials or jira_credentials_for(None)

    ticket_id = _extract_ticket_id(ticket_id_or_url)
    if not ticket_id:
        raise JiraServiceError("Invalid Jira ticket format.")

    if not creds.configured:
        # If env vars are missing, mock data for the DEV environment logic
        if ticket_id.startswith("MOCK-"):
            return _mock_fetch_ticket(ticket_id)
        raise JiraServiceError(
            missing_credentials_message("Jira", creds.source),
            code="JIRA_AUTH_MISSING",
        )

    # Prefer the base URL from the entered URL so the user never has to set a
    # base URL separately when pasting a full ticket URL.
    base_url = _extract_base_url(ticket_id_or_url) or creds.base_url
    if not base_url:
        raise JiraServiceError(
            "Cannot determine Jira base URL. Set it in project settings, or "
            "paste the full ticket URL."
        )

    url = f"{base_url.rstrip('/')}/rest/api/3/issue/{ticket_id}"
    auth = (creds.user_email, creds.api_token)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, auth=auth, params={"expand": "names"})

            if response.status_code == 401:
                raise JiraServiceError(
                    "Jira authentication failed. Please check your credentials."
                )
            elif response.status_code == 404:
                raise JiraServiceError(f"Jira ticket {ticket_id} not found.")

            if response.status_code == 403:
                raise JiraServiceError(
                    f"Permission denied for ticket {ticket_id}. "
                    "Check that your Jira credentials have read access."
                )
            response.raise_for_status()
            data = response.json()

            fields = data.get("fields", {})
            field_names = data.get("names", {})

            summary = fields.get("summary", "")
            description = _extract_text_from_jira_adf(fields.get("description"))
            labels = fields.get("labels", [])

            linked_issues = []
            for link in fields.get("issuelinks", []):
                if "outwardIssue" in link:
                    linked_issues.append(link["outwardIssue"].get("key"))
                elif "inwardIssue" in link:
                    linked_issues.append(link["inwardIssue"].get("key"))

            ac_text = _extract_acceptance_criteria(
                fields, field_names, description, summary
            )

            if len(ac_text) < 20:
                raise JiraServiceError(
                    f"Ticket {ticket_id} has no usable content "
                    "(summary, description, or acceptance criteria). "
                    "Please add a description to the ticket in Jira and try again."
                )

            return JiraTicketContent(
                ticket_id=ticket_id,
                summary=summary,
                description=description,
                acceptance_criteria=ac_text,
                labels=labels,
                linked_issues=[li for li in linked_issues if li],
            )

    except httpx.TimeoutException as exc:
        raise JiraServiceError(
            "Connection to Jira timed out. Please try again."
        ) from exc
    except httpx.RequestError as exc:
        raise JiraServiceError(
            "Network error communicating with Jira connection bounds."
        ) from exc


def _extract_ticket_id(ticket_id_or_url: str) -> str:
    """Extract a Jira issue key from a bare key or any Jira URL variant.

    Tries patterns in priority order so subdomain noise in Atlassian Cloud URLs
    is never mistaken for a ticket key.

    Supported forms (examples):
    - Bare key:            ``KAN-1``, ``PROJ-123``
    - Classic browse URL:  ``https://org.atlassian.net/browse/KAN-1``
    - Next-gen issue URL:  ``https://org.atlassian.net/jira/software/…/issues/KAN-1``
    - Board selected:      ``https://org.atlassian.net/…?selectedIssue=KAN-1``
    - Self-hosted:         ``https://jira.company.com/browse/KAN-1``
    """
    upper = ticket_id_or_url.strip().upper()

    # 1. Bare key — entire input is just the key
    if re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", upper):
        return upper

    # 2. /browse/<KEY>  — most common cloud and server URL
    m = re.search(r"/BROWSE/([A-Z][A-Z0-9_]*-\d+)", upper)
    if m:
        return m.group(1)

    # 3. /issues/<KEY> or /issue/<KEY>  — next-gen project URLs
    m = re.search(r"/ISSUES?/([A-Z][A-Z0-9_]*-\d+)", upper)
    if m:
        return m.group(1)

    # 4. Query param  ?selectedIssue=KEY  or  ?issue=KEY
    m = re.search(r"[?&](?:SELECTED)?ISSUES?=([A-Z][A-Z0-9_]*-\d+)", upper)
    if m:
        return m.group(1)

    # 5. Last resort: search only the URL path+query (skip scheme+host) so
    #    subdomain segments are never matched.
    if "//" in upper:
        path_start = upper.find("/", upper.index("//") + 2)
        search_region = upper[path_start:] if path_start != -1 else ""
    else:
        search_region = upper
    m = re.search(r"([A-Z][A-Z0-9_]*-\d+)", search_region)
    if m:
        return m.group(1)

    return ""


def _extract_base_url(ticket_id_or_url: str) -> str:
    """Return scheme+host from a URL, or empty string if input is a bare key."""
    m = re.match(r"(https?://[^/]+)", ticket_id_or_url.strip())
    return m.group(1) if m else ""


#: ADF node types that occupy their own line in the rendered document. Joining
#: their text with a space instead of a newline is what used to flatten a whole
#: ticket into one line, which left every downstream line-based reader — the
#: heading extractor here, and the model that reads the criteria — with no
#: document structure to work from.
_ADF_BLOCK_TYPES = frozenset(
    {
        "paragraph",
        "heading",
        "listItem",
        "blockquote",
        "codeBlock",
        "panel",
        "rule",
        "tableRow",
        "mediaSingle",
    }
)

#: Prefix written in front of a list item so an ordered or bulleted clause
#: survives as a clause. Without it "1." and "2." are indistinguishable from
#: prose once the ADF markup is gone.
_ADF_LIST_MARKER = "- "


def _extract_text_from_jira_adf(content: dict | None) -> str:
    """Extract plain text from Jira's Atlassian Document Format (ADF).

    Block-level nodes are separated by newlines and list items are marked, so
    the result keeps the line structure the original ticket had. Everything
    downstream — heading detection, clause counting, and the model reading the
    criteria — depends on that structure, and ADF is the only shape the real
    Jira API ever returns.
    """
    if not content:
        return ""
    if isinstance(content, str):
        return content

    lines: list[str] = []
    current: list[str] = []

    def flush() -> None:
        text = "".join(current).strip()
        current.clear()
        if text:
            lines.append(text)

    def extract_node(node) -> None:
        if not isinstance(node, dict):
            return

        node_type = node.get("type")

        if node_type == "text":
            current.append(node.get("text", ""))
            return
        if node_type == "hardBreak":
            flush()
            return

        is_block = node_type in _ADF_BLOCK_TYPES
        if is_block:
            flush()

        # Where this node's own output starts, so a list item can mark the
        # first line it produced. A listItem almost always wraps a paragraph,
        # which flushes the buffer before control returns here -- prefixing
        # `current` at that point would mark nothing.
        first_line = len(lines)

        for child in node.get("content", []):
            extract_node(child)

        if is_block:
            flush()
            if node_type == "listItem" and len(lines) > first_line:
                lines[first_line] = _ADF_LIST_MARKER + lines[first_line]

    extract_node(content)
    flush()
    return "\n".join(lines)


def _extract_acceptance_criteria(
    fields: dict[str, object],
    field_names: dict[str, str],
    description: str,
    summary: str,
) -> str:
    """Extract acceptance criteria from Jira fields or description text."""
    candidate_texts: list[tuple[int, str]] = []

    for field_key, value in fields.items():
        if value in (None, "", []):
            continue

        field_label = field_names.get(field_key, field_key).lower()
        text_value = _coerce_jira_field_text(value)
        if not text_value:
            continue

        score = 0
        if "acceptance criteria" in field_label:
            score += 100
        elif field_label in {"ac", "acceptance", "acceptance criteria / notes"}:
            score += 90

        lower_text = text_value.lower()
        if "acceptance criteria" in lower_text:
            score += 40
        if all(keyword in lower_text for keyword in ("given", "when", "then")):
            score += 25
        if any(
            marker in lower_text
            for marker in ("must", "should", "shall", "scenario")
        ):
            score += 10

        if score > 0:
            candidate_texts.append((score, text_value.strip()))

    if candidate_texts:
        candidate_texts.sort(key=lambda item: item[0], reverse=True)
        return candidate_texts[0][1]

    description_section = _extract_section_by_heading(
        description,
        ["acceptance criteria", "acceptance criterion", "business rules"],
    )
    if description_section:
        return description_section

    if description.strip():
        return description.strip()

    return summary.strip()


def _coerce_jira_field_text(value: object) -> str:
    """Convert common Jira field payload types into plain text."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _extract_text_from_jira_adf(value).strip()
    if isinstance(value, list):
        texts = [_coerce_jira_field_text(item) for item in value]
        return "\n".join([text for text in texts if text]).strip()
    return str(value).strip()


def _normalise_heading_line(line: str) -> str:
    """Reduce a line to the bare heading text it would be if it were one.

    Strips the decorations a heading picks up in Markdown and in Jira's own
    renderings - leading ``#``/``*``/``-``, wrapping ``*``/``_``, a trailing
    colon - so ``## Acceptance Criteria`` and ``Acceptance Criteria:`` both
    compare equal to ``acceptance criteria``.
    """
    return line.strip().lstrip("#*->").strip("*_").strip().strip(":").strip().lower()


def _is_heading_like(line: str) -> bool:
    """True when a line reads as a section heading rather than body text.

    This is the terminator when collecting a section: a section ends where the
    next one starts. A blank line does NOT end a section - treating it as a
    terminator is what truncated a multi-paragraph criteria list at its first
    paragraph break.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return True
    # "Notes:", "Out of scope:" - a short line that announces what follows.
    return stripped.endswith(":") and len(stripped.split()) <= 6


def _extract_section_by_heading(text: str, headings: list[str]) -> str:
    """Extract a section body under a likely heading from plain text.

    ``headings`` is a PRIORITY order, not an unordered set of alternatives.
    A ticket carrying both "Business Rules" and "Acceptance Criteria" must
    yield the acceptance criteria whichever section is written first, so each
    heading is searched across the whole document before the next one is
    considered. Scanning in document order instead returns whichever section
    the author happened to put at the top, which silently drops the other.
    """
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")

    start_index: int | None = None
    for heading in headings:
        for index, line in enumerate(lines):
            if _normalise_heading_line(line) == heading:
                start_index = index + 1
                break
        if start_index is not None:
            break

    if start_index is None:
        # Use multiline + word-boundary so "ac" only matches as a standalone label,
        # never as a substring of words like "access" or "each".
        match = re.search(
            r"(?im)^\s*"
            r"(acceptance criteria|acceptance criterion|\bac\b|business rules)"
            r"\s*:?\s*(.+)",
            normalized,
        )
        if match:
            return match.group(2).strip()
        return ""

    collected: list[str] = []
    for line in lines[start_index:]:
        stripped = line.strip()
        if not stripped:
            # A blank line separates paragraphs within a section; only the
            # next heading ends it.
            if collected:
                collected.append("")
            continue
        if _is_heading_like(stripped):
            break
        collected.append(stripped)

    return "\n".join(collected).strip()


def _check_jira_credentials(creds: JiraCredentials) -> None:
    """Raise JiraServiceError if the resolved credentials cannot be used."""
    if not creds.base_url or not creds.api_token:
        raise JiraServiceError(
            missing_credentials_message("Jira", creds.source),
            code="JIRA_NOT_CONFIGURED",
        )


async def fetch_tickets_from_project(
    project_key: str,
    sprint: str | None = None,
    label: str | None = None,
    credentials: JiraCredentials | None = None,
) -> list[JiraTicketContent]:
    """Fetch all Jira tickets from a project using the Search API v3.

    Args:
        project_key: Jira project key (e.g., "PROJ").
        sprint: Optional sprint name to filter by.
        label: Optional label to filter by.

    Returns:
        List of JiraTicketContent for all matching issues.

    Raises:
        JiraServiceError: On credential misconfiguration or HTTP failure.
    """
    creds = credentials or jira_credentials_for(None)
    _check_jira_credentials(creds)

    base = creds.base_url.rstrip("/")
    auth = httpx.BasicAuth(creds.user_email, creds.api_token)

    jql_parts = [f"project={project_key}"]
    if sprint:
        safe_sprint = sprint.replace('"', '\\"')
        jql_parts.append(f'sprint="{safe_sprint}"')
    if label:
        safe_label = label.replace('"', '\\"')
        jql_parts.append(f'labels="{safe_label}"')
    jql = " AND ".join(jql_parts) + " ORDER BY created ASC"

    url = f"{base}/rest/api/3/search"
    tickets: list[JiraTicketContent] = []
    start_at = 0
    max_results = 50

    async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
        while True:
            try:
                response = await client.get(
                    url,
                    params={
                        "jql": jql,
                        "startAt": start_at,
                        "maxResults": max_results,
                        "fields": "summary,description,labels,issuelinks",
                        "expand": "names",
                    },
                )
                response.raise_for_status()

                try:
                    data = response.json()
                except Exception as exc:
                    raise JiraServiceError(
                        "Jira returned an invalid JSON response.",
                        code="JIRA_INVALID_RESPONSE",
                    ) from exc

                issues = data.get("issues", [])
                field_names = data.get("names", {})

                for issue in issues:
                    fields = issue.get("fields", {})
                    summary = fields.get("summary", "")
                    description = _extract_text_from_jira_adf(
                        fields.get("description")
                    )
                    labels = fields.get("labels", [])
                    linked_issues = []
                    for link in fields.get("issuelinks", []):
                        if "outwardIssue" in link:
                            linked_issues.append(link["outwardIssue"].get("key"))
                        elif "inwardIssue" in link:
                            linked_issues.append(link["inwardIssue"].get("key"))
                    ac_text = _extract_acceptance_criteria(
                        fields, field_names, description, summary
                    )
                    tickets.append(
                        JiraTicketContent(
                            ticket_id=issue["key"],
                            summary=summary,
                            description=description,
                            acceptance_criteria=ac_text,
                            labels=labels,
                            linked_issues=[li for li in linked_issues if li],
                        )
                    )

                total = data.get("total", 0)
                start_at += len(issues)
                if start_at >= total or not issues:
                    break

            except httpx.HTTPStatusError as exc:
                raise JiraServiceError(
                    f"Jira API returned {exc.response.status_code} "
                    f"for project '{project_key}'."
                ) from exc
            except httpx.RequestError as exc:
                raise JiraServiceError(
                    f"Failed to connect to Jira: {exc}"
                ) from exc

    return tickets


def _mock_fetch_ticket(ticket_id: str) -> JiraTicketContent:
    """Return mock ticket data for testing purposes."""
    return JiraTicketContent(
        ticket_id=ticket_id,
        summary=f"Mock summary for {ticket_id}",
        description="This is a mock description.",
        acceptance_criteria="Given a mock ticket, When processed, Then it works.",
        labels=["mock"],
        linked_issues=[],
    )
