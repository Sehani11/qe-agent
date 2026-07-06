"""Jira ticket ingestion service."""

import re

import httpx
from pydantic import BaseModel

from app.core.config import settings


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


async def fetch_ticket_content(ticket_id_or_url: str) -> JiraTicketContent:
    """Fetch and extract content from a Jira ticket.
    
    Args:
        ticket_id_or_url: The Jira ticket ID (e.g., PROJ-123) or URL.
        
    Returns:
        Structured JiraTicketContent.
        
    Raises:
        JiraServiceError: If the fetch fails.
    """
    ticket_id = _extract_ticket_id(ticket_id_or_url)
    if not ticket_id:
        raise JiraServiceError("Invalid Jira ticket format.")

    if not settings.jira_api_token or not settings.jira_user_email:
        # If env vars are missing, mock data for the DEV environment logic
        if ticket_id.startswith("MOCK-"):
            return _mock_fetch_ticket(ticket_id)
        raise JiraServiceError("Jira application configuration is missing credentials.", code="JIRA_AUTH_MISSING")

    # Prefer the base URL embedded in the entered URL (scheme + host) so the
    # user never has to set JIRA_BASE_URL separately when pasting a full URL.
    base_url = _extract_base_url(ticket_id_or_url) or settings.jira_base_url
    if not base_url:
        raise JiraServiceError("Cannot determine Jira base URL. Set JIRA_BASE_URL in your environment or paste the full ticket URL.")

    url = f"{base_url.rstrip('/')}/rest/api/3/issue/{ticket_id}"

    # We need to fetch Summary, Description, Acceptance Criteria (custom field), Labels, Linked issues
    # Note: custom field IDs vary per Jira instance, we'll try to extract them if available,
    # or just use generic description.
    auth = (settings.jira_user_email, settings.jira_api_token)
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, auth=auth, params={"expand": "names"})
            
            if response.status_code == 401:
                raise JiraServiceError("Jira authentication failed. Please check your credentials.")
            elif response.status_code == 404:
                raise JiraServiceError(f"Jira ticket {ticket_id} not found.")
            
            if response.status_code == 403:
                raise JiraServiceError(
                    f"Permission denied for ticket {ticket_id}. Check that your Jira credentials have read access."
                )
            response.raise_for_status()
            data = response.json()
            
            fields = data.get("fields", {})
            field_names = data.get("names", {})

            # Simple extraction from Jira ADF (Atlassian Document Format) or plain text
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

            if len(ac_text) < 20:
                raise JiraServiceError(
                    f"Ticket {ticket_id} has no usable content (summary, description, or acceptance criteria). "
                    "Please add a description to the ticket in Jira and try again."
                )

            return JiraTicketContent(
                ticket_id=ticket_id,
                summary=summary,
                description=description,
                acceptance_criteria=ac_text,
                labels=labels,
                linked_issues=[li for li in linked_issues if li]
            )
            
    except httpx.TimeoutException:
        raise JiraServiceError("Connection to Jira timed out. Please try again.")
    except httpx.RequestError:
        raise JiraServiceError("Network error communicating with Jira connection bounds.")


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


def _extract_text_from_jira_adf(content: dict | None) -> str:
    """Extract plain text from Jira's Atlassian Document Format (ADF)."""
    if not content:
        return ""
    if isinstance(content, str):
        return content
        
    texts = []
    
    def extract_node(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "text":
            texts.append(node.get("text", ""))
        for child in node.get("content", []):
            extract_node(child)
            
    extract_node(content)
    return " ".join(texts)


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
        if any(marker in lower_text for marker in ("must", "should", "shall", "scenario")):
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


def _extract_section_by_heading(text: str, headings: list[str]) -> str:
    """Extract a section body under a likely heading from plain text."""
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")

    start_index: int | None = None
    for index, line in enumerate(lines):
        cleaned = line.strip().strip(":").lower()
        if cleaned in headings:
            start_index = index + 1
            break

    if start_index is None:
        # Use multiline + word-boundary so "ac" only matches as a standalone label,
        # never as a substring of words like "access" or "each".
        match = re.search(
            r"(?im)^\s*(acceptance criteria|acceptance criterion|\bac\b|business rules)\s*:?\s*(.+)",
            normalized,
        )
        if match:
            return match.group(2).strip()
        return ""

    collected: list[str] = []
    for line in lines[start_index:]:
        stripped = line.strip()
        if not stripped:
            if collected:
                break
            continue
        if stripped.endswith(":") and len(stripped.split()) <= 6:
            break
        collected.append(stripped)

    return "\n".join(collected).strip()


def _mock_fetch_ticket(ticket_id: str) -> JiraTicketContent:
    """Return mock ticket data for testing purposes."""
    return JiraTicketContent(
        ticket_id=ticket_id,
        summary=f"Mock summary for {ticket_id}",
        description="This is a mock description.",
        acceptance_criteria="Given a mock ticket, When processed, Then it works.",
        labels=["mock"],
        linked_issues=[]
    )
