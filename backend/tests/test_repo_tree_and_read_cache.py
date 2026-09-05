"""The two accelerators that cut round-trips out of a verification run.

`get_repo_tree` replaces the directory walk each scenario would otherwise
repeat: GitHub's code search returns nothing for private repositories, so
without a tree the agent discovers the layout one `list_directory` at a time,
several LLM rounds deep, before it reads any code at all.

The run-scoped read cache covers the other repetition: scenarios are checked
against one repository at one pinned ref, so they converge on the same files and
would otherwise re-fetch each of them once per scenario.

Both are optimisations, which is exactly why they are tested this closely — an
optimisation that changes an answer is a bug, and one that silently stops
optimising is invisible without a test.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.agentic_verification_service import _make_tool_executor
from app.services.github_service import GitHubServiceError
from app.services.github_tools import (
    _TREE_PATH_CAP,
    format_repo_tree,
    get_repo_tree,
)


def _tree_response(paths: list[str], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={"tree": [{"path": p, "type": "blob"} for p in paths]},
        request=httpx.Request("GET", "https://api.github.com/"),
    )


class TestGetRepoTree:
    @pytest.mark.asyncio
    async def test_source_paths_come_back_sorted(self) -> None:
        with patch(
            "app.services.github_tools._github_get",
            new=AsyncMock(return_value=_tree_response(["b/z.py", "a/y.ts"])),
        ):
            assert await get_repo_tree("org/repo", "pat") == ["a/y.ts", "b/z.py"]

    @pytest.mark.asyncio
    async def test_dependency_and_build_directories_are_excluded(self) -> None:
        """These are what put a real repository over the path cap.

        A tree that spends its budget on `node_modules` pushes the application's
        own files out of the listing — the opposite of the point.
        """
        paths = [
            "src/app.py",
            "node_modules/react/index.js",
            "frontend/.next/static/chunk.js",
            "backend/__pycache__/app.cpython-312.pyc",
            "dist/bundle.js",
            ".git/config",
        ]
        with patch(
            "app.services.github_tools._github_get",
            new=AsyncMock(return_value=_tree_response(paths)),
        ):
            assert await get_repo_tree("org/repo", "pat") == ["src/app.py"]

    @pytest.mark.asyncio
    async def test_trees_are_capped(self) -> None:
        paths = [f"src/mod_{i:05d}.py" for i in range(_TREE_PATH_CAP + 250)]
        with patch(
            "app.services.github_tools._github_get",
            new=AsyncMock(return_value=_tree_response(paths)),
        ):
            assert len(await get_repo_tree("org/repo", "pat")) == _TREE_PATH_CAP

    @pytest.mark.asyncio
    async def test_only_blobs_are_listed(self) -> None:
        """A tree entry of type "tree" is a directory, not a file to read."""
        response = httpx.Response(
            200,
            json={
                "tree": [
                    {"path": "src", "type": "tree"},
                    {"path": "src/app.py", "type": "blob"},
                ]
            },
            request=httpx.Request("GET", "https://api.github.com/"),
        )
        with patch(
            "app.services.github_tools._github_get",
            new=AsyncMock(return_value=response),
        ):
            assert await get_repo_tree("org/repo", "pat") == ["src/app.py"]

    @pytest.mark.asyncio
    async def test_the_pinned_ref_is_requested(self) -> None:
        """A tree read at the wrong ref describes the wrong code."""
        fake = AsyncMock(return_value=_tree_response([]))
        with patch("app.services.github_tools._github_get", new=fake):
            await get_repo_tree("org/repo", "pat", "abc123")

        assert "git/trees/abc123?recursive=1" in fake.await_args.args[1]

    @pytest.mark.parametrize(
        "failure",
        [
            GitHubServiceError("rate limited"),
            httpx.ConnectError("no route"),
            ValueError("not json"),
            RuntimeError("something else entirely"),
        ],
    )
    @pytest.mark.asyncio
    async def test_any_failure_degrades_to_no_tree(self, failure: Exception) -> None:
        """The tree is an accelerator; losing it must never end a run.

        The run still works without one — the agent just explores the way it did
        before this existed — so every failure mode returns the empty list
        rather than propagating.
        """
        with patch(
            "app.services.github_tools._github_get", new=AsyncMock(side_effect=failure)
        ):
            assert await get_repo_tree("org/repo", "pat") == []

    @pytest.mark.asyncio
    async def test_an_http_error_status_degrades_to_no_tree(self) -> None:
        with patch(
            "app.services.github_tools._github_get",
            new=AsyncMock(return_value=_tree_response([], status=409)),
        ):
            assert await get_repo_tree("org/repo", "pat") == []


class TestFormatRepoTree:
    def test_no_paths_render_to_nothing(self) -> None:
        """An empty block would otherwise add a heading with no list under it."""
        assert format_repo_tree([]) == ""

    def test_paths_are_listed_whole_by_default(self) -> None:
        """The default asks nothing of the model but reading a path.

        Grouping is smaller but makes the model rejoin a directory to a
        filename, and a path it reconstructs wrongly is a 404 — which this
        codebase treats as a step towards wrongly concluding a feature is
        absent. Cheapest is not the default; safest is.
        """
        block = format_repo_tree(["src/app.py", "src/auth.py"])
        assert "REPOSITORY FILE TREE" in block
        assert "src/app.py" in block
        assert "src/auth.py" in block

    def test_the_compact_rendering_is_smaller_on_a_real_sized_tree(self) -> None:
        paths = [
            f"backend/app/{area}/module_{i}.py"
            for area in ("services", "api/v1", "models", "schemas")
            for i in range(60)
        ]
        flat = format_repo_tree(paths)
        compact = format_repo_tree(paths, compact=True)

        assert len(compact) < len(flat) * 0.7
        # The model has to be told how to rejoin the two halves.
        assert "join" in compact.lower()

    def test_the_compact_rendering_does_not_pay_off_on_a_small_tree(self) -> None:
        """Grouping is not free: it needs a paragraph explaining how to rejoin
        a path, and on a handful of files that paragraph costs more than the
        repeated directory prefixes it removes.

        Recorded rather than fixed, because it is the reason the flag stays off
        by default and is only worth reaching for on a deep repository.
        """
        paths = ["src/app.py", "src/auth.py", "README.md"]

        assert len(format_repo_tree(paths, compact=True)) > len(
            format_repo_tree(paths)
        )

    def test_root_files_stay_whole_and_unindented_when_compact(self) -> None:
        """A root-level file has no directory to join to, so it is written out
        in full — and the absence of indentation is what says so."""
        block = format_repo_tree(["README.md", "src/app.py"], compact=True)
        assert "\nREADME.md" in block
        assert "  README.md" not in block

    def test_no_path_is_lost_in_either_rendering(self) -> None:
        """Presentation may change; the set of visible paths may not.

        Anything dropped is a file the agent can no longer find, which is
        indistinguishable to it from a file that does not exist.
        """
        paths = [
            "README.md",
            "backend/api/v1/verification.py",
            "src/app.py",
            "src/auth/login.py",
            "src/auth/register.py",
        ]
        assert all(p in format_repo_tree(paths) for p in paths)

        rebuilt: list[str] = []
        directory = ""
        for line in format_repo_tree(paths, compact=True).split("\n")[1:]:
            if line.startswith("  "):
                rebuilt.append(f"{directory}{line.strip()}")
            elif line.endswith("/"):
                directory = line
            elif line:
                rebuilt.append(line)

        assert sorted(rebuilt) == sorted(paths)

    def test_a_capped_tree_says_so(self) -> None:
        """A partial list that looks complete is how an agent concludes that a
        file it cannot see does not exist."""
        block = format_repo_tree([f"f{i}.py" for i in range(_TREE_PATH_CAP)])
        assert "TRUNCATED" in block
        assert "list_directory" in block

    def test_a_short_tree_carries_no_truncation_notice(self) -> None:
        assert "TRUNCATED" not in format_repo_tree(["a.py"])


class TestRunScopedReadCache:
    @pytest.mark.asyncio
    async def test_a_second_scenario_reuses_the_first_scenarios_read(self) -> None:
        """Twenty scenarios reading one route file made twenty identical calls."""
        cache: dict = {}
        fake = AsyncMock(return_value="def login(): ...")

        with patch("app.services.github_tools.get_file_contents", new=fake):
            for _ in range(3):
                executor, _e, _s, _f = _make_tool_executor(
                    pat="pat", repo="org/repo", default_ref="main", read_cache=cache
                )
                await executor("get_file_contents", {"path": "src/auth.py"})

        assert fake.await_count == 1

    @pytest.mark.asyncio
    async def test_each_scenario_still_records_its_own_read(self) -> None:
        """The fetch is shared; the bookkeeping must not be.

        `files_read` grounds the citation check, so a cache hit has to leave the
        second scenario knowing it saw the file — while never handing it a file
        only some other scenario read.
        """
        cache: dict = {}
        with patch(
            "app.services.github_tools.get_file_contents",
            new=AsyncMock(return_value="def login(): ..."),
        ):
            first, _e, _s, first_files = _make_tool_executor(
                pat="pat", repo="org/repo", default_ref="main", read_cache=cache
            )
            await first("get_file_contents", {"path": "src/auth.py"})

            second, _e2, _s2, second_files = _make_tool_executor(
                pat="pat", repo="org/repo", default_ref="main", read_cache=cache
            )
            await second("get_file_contents", {"path": "src/auth.py"})

            _third, _e3, _s3, third_files = _make_tool_executor(
                pat="pat", repo="org/repo", default_ref="main", read_cache=cache
            )

        assert first_files == {"src/auth.py": "def login(): ..."}
        assert second_files == {"src/auth.py": "def login(): ..."}
        # A scenario that read nothing must not inherit the cache's contents.
        assert third_files == {}

    @pytest.mark.asyncio
    async def test_different_offsets_are_cached_separately(self) -> None:
        """Two windows of one file are two different results."""
        cache: dict = {}
        fake = AsyncMock(side_effect=["head", "tail"])

        with patch("app.services.github_tools.get_file_contents", new=fake):
            executor, _e, _s, _f = _make_tool_executor(
                pat="pat", repo="org/repo", default_ref="main", read_cache=cache
            )
            first = await executor("get_file_contents", {"path": "big.py"})
            second = await executor(
                "get_file_contents", {"path": "big.py", "offset": 40_000}
            )

        assert (first, second) == ("head", "tail")
        assert fake.await_count == 2

    @pytest.mark.asyncio
    async def test_different_refs_are_cached_separately(self) -> None:
        """Contents are immutable at a ref, not across refs."""
        cache: dict = {}
        fake = AsyncMock(side_effect=["old", "new"])

        with patch("app.services.github_tools.get_file_contents", new=fake):
            executor, _e, _s, _f = _make_tool_executor(
                pat="pat", repo="org/repo", default_ref="main", read_cache=cache
            )
            await executor("get_file_contents", {"path": "a.py", "ref": "v1"})
            await executor("get_file_contents", {"path": "a.py", "ref": "v2"})

        assert fake.await_count == 2

    @pytest.mark.asyncio
    async def test_a_failed_read_is_not_cached(self) -> None:
        """A transient failure must not be pinned for the rest of the run."""
        cache: dict = {}
        fake = AsyncMock(
            side_effect=[GitHubServiceError("boom"), "def login(): ..."]
        )

        with patch("app.services.github_tools.get_file_contents", new=fake):
            executor, errors, _s, files_read = _make_tool_executor(
                pat="pat", repo="org/repo", default_ref="main", read_cache=cache
            )
            first = await executor("get_file_contents", {"path": "src/auth.py"})
            second = await executor("get_file_contents", {"path": "src/auth.py"})

        assert "GitHub API error" in first
        assert second == "def login(): ..."
        assert len(errors) == 1
        assert files_read == {"src/auth.py": "def login(): ..."}

    @pytest.mark.asyncio
    async def test_directory_listings_and_searches_are_cached_too(self) -> None:
        cache: dict = {}
        listing = AsyncMock(return_value=json.dumps(["a.py"]))
        search = AsyncMock(return_value=json.dumps([]))

        with (
            patch("app.services.github_tools.list_directory", new=listing),
            patch("app.services.github_tools.search_code", new=search),
        ):
            for _ in range(2):
                executor, _e, _s, _f = _make_tool_executor(
                    pat="pat", repo="org/repo", default_ref="main", read_cache=cache
                )
                await executor("list_directory", {"path": "src"})
                await executor("search_code", {"query": "login"})

        assert listing.await_count == 1
        assert search.await_count == 1

    @pytest.mark.asyncio
    async def test_without_a_shared_cache_scenarios_are_independent(self) -> None:
        """The parameter is optional, and omitting it keeps the old behaviour."""
        fake = AsyncMock(return_value="contents")

        with patch("app.services.github_tools.get_file_contents", new=fake):
            for _ in range(2):
                executor, _e, _s, _f = _make_tool_executor(
                    pat="pat", repo="org/repo", default_ref="main"
                )
                await executor("get_file_contents", {"path": "a.py"})

        assert fake.await_count == 2
