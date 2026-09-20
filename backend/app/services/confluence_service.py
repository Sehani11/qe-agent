"""Confluence API integration service.

All Confluence HTTP calls are centralised here — never call the Confluence API
from knowledge_service.py or route handlers directly.
"""

import html
import re

import httpx
from pydantic import BaseModel

from app.core.config import settings
from app.services.project_config_service import (
    ConfluenceCredentials,
    confluence_credentials_for,
    missing_credentials_message,
)


class ConfluenceServiceError(Exception):
    """Raised when a Confluence API call fails."""

    def __init__(self, message: str, code: str = "CONFLUENCE_FETCH_FAILED"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class ConfluencePage(BaseModel):
    """Structured content extracted from a Confluence page."""

    id: str
    title: str
    url: str
    body: str  # Plain text — HTML stripped


# Block-level elements are where one sentence ends and the next begins. Deleting
# them outright welded the two together — a page ending a paragraph with "...can
# end it." before an "Coding Challenges" heading came back as "...can end it.Coding
# Challenges", which reads as broken text in the UI and is no clearer to the
# embedding model. Inline tags stay deleted so words inside them are not split.
_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|div|br|hr|li|ul|ol|tr|td|th|table|thead|tbody|section|article"
    r"|h[1-6]|blockquote|pre|dl|dt|dd|figcaption)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")

# Macro parameters are configuration, and their VALUES are text nodes — so
# stripping tags alone left them in the prose. A page built with a layout macro
# came through carrying "wide760true", which is both nonsense to read and noise
# in the embedding. Dropped with their contents, unlike every other element.
_MACRO_PARAM_RE = re.compile(
    r"<ac:parameter\b[^>]*>.*?</ac:parameter>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(raw: str) -> str:
    """Flatten Confluence body HTML into plain text.

    Entities are decoded only AFTER the tags are gone, so an escaped `&lt;p&gt;`
    in the prose is left as text rather than becoming a tag and being stripped.
    """
    text = _MACRO_PARAM_RE.sub("", raw)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    # Each element contributes a newline for its opening AND closing tag, so the
    # blank lines between blocks are an artefact of the substitution, not of the
    # source.
    lines = (line.strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line).strip()


def _build_page_url(page_id: str, base_url: str = "") -> str:
    """Deep link to a page, on whichever Confluence it actually came from."""
    base = (base_url or settings.confluence_base_url).rstrip("/")
    return f"{base}/wiki/spaces/viewpage.action?pageId={page_id}"


# Page ids appear in every Confluence URL shape we can support:
#   cloud   .../wiki/spaces/ENG/pages/123456/Title
#   short   .../wiki/spaces/ENG/pages/123456
#   server  .../pages/viewpage.action?pageId=123456
# Tiny links (/wiki/x/AbCdEf) carry no id and cannot be resolved without an
# extra API round trip, so they are rejected with an actionable message rather
# than silently skipped.
_PAGE_ID_PATTERNS = (
    re.compile(r"/pages/(\d+)"),
    re.compile(r"[?&]pageId=(\d+)"),
)


def extract_page_id(ref: str) -> str:
    """Return the page id from a Confluence URL, or the ref if already an id.

    Raises ConfluenceServiceError with a message naming the offending input, so
    one bad line in a pasted list says which line to fix.
    """
    candidate = ref.strip()
    if not candidate:
        raise ConfluenceServiceError(
            "Empty page reference.", code="CONFLUENCE_INVALID_PAGE_REF"
        )
    if candidate.isdigit():
        return candidate
    for pattern in _PAGE_ID_PATTERNS:
        match = pattern.search(candidate)
        if match:
            return match.group(1)
    raise ConfluenceServiceError(
        f"Could not find a page ID in {candidate!r}. Paste the full page URL "
        f"(it contains /pages/<id>) or the numeric page ID. Short /wiki/x/ "
        f"links do not contain an ID.",
        code="CONFLUENCE_INVALID_PAGE_REF",
    )


def _check_credentials(creds: ConfluenceCredentials) -> None:
    if not creds.configured:
        raise ConfluenceServiceError(
            missing_credentials_message("Confluence", creds.source),
            code="CONFLUENCE_NOT_CONFIGURED",
        )


async def fetch_pages_from_space(
    space_key: str, credentials: ConfluenceCredentials | None = None
) -> list[ConfluencePage]:
    """Fetch all pages from a Confluence space.

    Args:
        space_key: The Confluence space key (e.g., "MYSPACE").

    Returns:
        List of ConfluencePage with stripped plain-text body.

    Raises:
        ConfluenceServiceError: On credential misconfiguration or HTTP failure.
    """
    creds = credentials or confluence_credentials_for(None)
    _check_credentials(creds)

    base = creds.base_url.rstrip("/")
    auth = httpx.BasicAuth(creds.user_email, creds.api_token)
    pages: list[ConfluencePage] = []

    url = (
        f"{base}/wiki/rest/api/content"
        f"?spaceKey={space_key}&type=page&expand=body.storage&limit=50"
    )

    async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
        while url:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ConfluenceServiceError(
                    f"Confluence API returned {exc.response.status_code} "
                    f"for space '{space_key}'."
                ) from exc
            except httpx.RequestError as exc:
                raise ConfluenceServiceError(
                    f"Failed to connect to Confluence: {exc}"
                ) from exc

            data = response.json()
            for result in data.get("results", []):
                body_html = (
                    result.get("body", {}).get("storage", {}).get("value", "")
                )
                pages.append(
                    ConfluencePage(
                        id=str(result["id"]),
                        title=result.get("title", ""),
                        url=_build_page_url(str(result["id"]), creds.base_url),
                        body=_strip_html(body_html),
                    )
                )

            # Follow pagination: _links.next is relative ("/wiki/rest/api/...")
            # but guard against Confluence returning an absolute URL.
            next_path = data.get("_links", {}).get("next")
            if not next_path:
                url = ""
            elif next_path.startswith("http"):
                url = next_path
            else:
                url = f"{base}{next_path}"

    return pages


async def fetch_page_by_id(
    page_id: str, credentials: ConfluenceCredentials | None = None
) -> ConfluencePage:
    """Fetch a single Confluence page by its numeric ID.

    Args:
        page_id: The Confluence page ID.

    Returns:
        ConfluencePage with stripped plain-text body.

    Raises:
        ConfluenceServiceError: On credential misconfiguration or HTTP failure.
    """
    creds = credentials or confluence_credentials_for(None)
    _check_credentials(creds)

    base = creds.base_url.rstrip("/")
    auth = httpx.BasicAuth(creds.user_email, creds.api_token)
    url = f"{base}/wiki/rest/api/content/{page_id}?expand=body.storage"

    async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ConfluenceServiceError(
                f"Confluence API returned {exc.response.status_code} "
                f"for page '{page_id}'."
            ) from exc
        except httpx.RequestError as exc:
            raise ConfluenceServiceError(
                f"Failed to connect to Confluence: {exc}"
            ) from exc

    data = response.json()
    body_html = data.get("body", {}).get("storage", {}).get("value", "")
    return ConfluencePage(
        id=str(data["id"]),
        title=data.get("title", ""),
        url=_build_page_url(str(data["id"]), creds.base_url),
        body=_strip_html(body_html),
    )
