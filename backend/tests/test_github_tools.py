"""The three GitHub tools the verification agent calls.

These cover the shape of GitHub's responses rather than its status codes: the
`/contents/{path}` endpoint returns an OBJECT for a file and an ARRAY for a
directory, and the model — which is guessing paths from a scenario — hits the
wrong one routinely. Each tool has to recognise being pointed at the other kind
and say so, because whatever it returns goes straight back to the model as its
next observation.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import github_tools
from app.services.github_service import GitHubServiceError


def _response(payload, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", "/"))


class TestGetFileContentsPointedAtADirectory:
    """The reported crash: `'list' object has no attribute 'get'`.

    A directory answers the same endpoint with a JSON array, so reading
    `data.get("encoding")` raised an AttributeError that was not a
    GitHubServiceError — so the executor did not catch it, it escaped through
    the provider's tool loop, and the whole scenario failed with a message
    blaming the Claude API.
    """

    async def test_a_directory_is_reported_not_crashed(self) -> None:
        listing = [
            {"name": "login.py", "type": "file"},
            {"name": "helpers", "type": "dir"},
        ]

        with (
            patch.object(
                github_tools, "_github_get", AsyncMock(return_value=_response(listing))
            ),
            pytest.raises(GitHubServiceError) as caught,
        ):
            await github_tools.get_file_contents(
                repo="org/app", path="src/auth", pat="pat"
            )

        # The text is the model's next observation, so it has to name the
        # recovery rather than merely state the failure.
        assert "is a directory" in caught.value.message
        assert "list_directory" in caught.value.message

    async def test_a_real_file_still_decodes(self) -> None:
        import base64

        payload = {
            "encoding": "base64",
            "content": base64.b64encode(b"def login():\n    pass\n").decode(),
        }

        with patch.object(
            github_tools, "_github_get", AsyncMock(return_value=_response(payload))
        ):
            content = await github_tools.get_file_contents(
                repo="org/app", path="src/auth/login.py", pat="pat"
            )

        assert content == "def login():\n    pass\n"


class TestListDirectoryPointedAtAFile:
    """The mirror case, which was already guarded — kept here beside its twin
    so the pair cannot drift apart."""

    async def test_a_file_is_reported_not_crashed(self) -> None:
        payload = {"encoding": "base64", "content": "abc", "type": "file"}

        with (
            patch.object(
                github_tools, "_github_get", AsyncMock(return_value=_response(payload))
            ),
            pytest.raises(GitHubServiceError) as caught,
        ):
            await github_tools.list_directory(
                repo="org/app", path="src/auth/login.py", pat="pat"
            )

        assert "got a file" in caught.value.message
