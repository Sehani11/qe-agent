"""Shared test fixtures.

The one thing in here is a network block. It exists because a test in
`test_embedding_policy.py` silently made a REAL OpenAI embeddings call: it
patched `vector_service.settings` to simulate "no credentials", the code moved
to reading its key from elsewhere, and the now-ineffective patch left the
request to go out with whatever key the developer's `.env` holds. Nothing
failed — the test passed on to the next assertion and the bill was the only
evidence.

Mocks that stop matching the code are normal and expected. A test suite that
spends money when they do is not, so the block is here rather than in the one
file that happened to get caught.
"""

import httpx
import pytest


class BlockedNetworkCallError(RuntimeError):
    """A test tried to make a real outbound request."""


_MESSAGE = (
    "This test tried to reach {url!r} for real. A mock is missing or no longer "
    "matches the code under test — check what the code reads its credentials "
    "and URLs from. Use @pytest.mark.allow_network if the call is intended."
)


@pytest.fixture(autouse=True)
def block_network(request, monkeypatch):
    """Fail any test that makes a real HTTP request, instead of letting it out.

    Blocks httpx's real transports. Everything in this codebase that calls out
    goes through them — raw `httpx.AsyncClient`, the OpenAI SDK and the
    Anthropic SDK all use httpx underneath — so one seam covers the lot.

    Deliberately NOT a socket-level block: on Windows, asyncio's Proactor loop
    connects through IOCP and never touches `socket.socket.connect`, so a
    socket guard passes every async call straight through. That version of this
    fixture looked like it worked and blocked nothing.

    `ASGITransport` is left alone — that is how FastAPI's TestClient reaches
    the app under test, and it never leaves the process.

    Mark a test `@pytest.mark.allow_network` when it is genuinely meant to
    reach out.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    def blocked_sync(self, request_, *args, **kwargs):
        raise BlockedNetworkCallError(_MESSAGE.format(url=str(request_.url)))

    async def blocked_async(self, request_, *args, **kwargs):
        raise BlockedNetworkCallError(_MESSAGE.format(url=str(request_.url)))

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked_sync)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", blocked_async
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_network: test may make real outbound requests"
    )


@pytest.fixture(autouse=True)
def no_repo_tree_prefetch(monkeypatch, request):
    """Default the repo-tree prefetch to "unavailable" for every test.

    Agentic verification prefetches the repository file tree once per run to
    save the agent a directory walk. It is best-effort and falls back to an
    empty list, so tests written before it exist are still exercising a valid
    path — but only if the call does not go out to the network first.

    Defaulting it here keeps those tests honest (no outbound request, and the
    same no-tree behaviour they were written against) without putting an
    identical patch in every one of them. A test about the tree itself patches
    `get_repo_tree` directly, and that inner patch wins for its own duration.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    async def _no_tree(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "app.services.github_tools.get_repo_tree", _no_tree, raising=False
    )
