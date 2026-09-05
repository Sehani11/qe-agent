"""Hybrid discovery — the code chunker, the index, and its use in verification.

The tests that carry the most weight here are the ones about what retrieval is
allowed to be. A code index makes the agent faster by telling it where to look;
it must never become the thing a verdict rests on, because vector search cannot
prove absence — it returns its top-k nearest chunks whether or not any of them
are relevant, and never returns "this does not exist".

So: candidates are paths, not text; a stale index is refused rather than used;
every failure degrades to the tree-walking behaviour that predates all of this;
and the prompt says out loud that a file's absence from the list proves nothing.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agentic_verification_service import run_agentic_verification
from app.services.code_chunking import chunk_code
from app.services.code_index_service import (
    _line_range_label,
    code_namespace,
    find_candidate_files,
    format_candidate_block,
)

from tests.test_agentic_verification_service import (
    _SINGLE_BDD,
    _collect,
    _make_db_session,
    _make_verdict_json,
    _parse_events,
    _prompt_text,
    _simulate_tool_read,
)

# ---------------------------------------------------------------------------
# Step 1 — the chunker
# ---------------------------------------------------------------------------

_PYTHON = '''\
"""Module docstring."""

import os

CONSTANT = 1


@router.post("/run")
async def run_verification(request):
    """Run it."""
    return await do_the_thing(request)


class Verifier:
    def verify(self, scenario):
        return True
'''

_TSX = """\
import { useState } from "react";

export const useRunVerification = (id: string) => {
  const [busy, setBusy] = useState(false);
  return { busy };
};

export default function VerificationForm({ onSubmit }) {
  return <form onSubmit={onSubmit} />;
}
"""


class TestChunkCode:
    def test_splits_python_at_definitions(self) -> None:
        chunks = chunk_code(_PYTHON, "backend/app/api/v1/verification.py")
        symbols = [c["symbol"] for c in chunks]

        assert "run_verification" in symbols
        assert "Verifier" in symbols
        # The header — docstring, import, module constant — is kept as its own
        # chunk rather than dropped or glued to the first definition.
        assert "" in symbols
        header = next(c for c in chunks if c["symbol"] == "")
        assert "CONSTANT = 1" in header["code"]

    def test_a_decorator_stays_with_the_function_it_decorates(self) -> None:
        """A route whose decorator was cut away loses the path it is mounted at.

        The function above it would also gain a decorator that is not its own,
        so the mis-split damages two chunks, not one.
        """
        chunks = chunk_code(_PYTHON, "backend/app/api/v1/verification.py")
        run = next(c for c in chunks if c["symbol"] == "run_verification")

        assert '@router.post("/run")' in run["code"]
        assert '@router.post("/run")' not in next(
            c for c in chunks if c["symbol"] == ""
        )["code"]

    def test_splits_tsx_at_components_and_hooks(self) -> None:
        chunks = chunk_code(_TSX, "frontend/src/components/VerificationForm.tsx")
        symbols = [c["symbol"] for c in chunks]

        assert "useRunVerification" in symbols
        assert "VerificationForm" in symbols

    def test_embedded_text_carries_the_path(self) -> None:
        """Path tokens are a strong retrieval signal and the cheapest to add.

        A scenario about "verification" should reach verification.py even when
        the chunk body never says the word.
        """
        chunks = chunk_code(_PYTHON, "backend/app/api/v1/verification.py")

        assert all(
            c["text"].startswith("# backend/app/api/v1/verification.py")
            for c in chunks
        )
        # The raw code stays separately available, unprefixed.
        assert not chunks[0]["code"].startswith("#  backend")

    def test_line_numbers_bracket_the_chunk(self) -> None:
        chunks = chunk_code(_PYTHON, "x.py")
        lines = _PYTHON.split("\n")

        for chunk in chunks:
            assert 1 <= chunk["start_line"] <= chunk["end_line"] <= len(lines)
            # The recorded start really is where this chunk's text begins.
            assert lines[chunk["start_line"] - 1] == chunk["code"].split("\n")[0]

    def test_unstructured_input_is_size_capped_not_one_huge_chunk(self) -> None:
        """A file with no recognisable boundaries must still chunk sensibly.

        Minified bundles, data literals and unfamiliar languages all land here,
        and one enormous vector has an embedding that means nothing specific.
        """
        from app.services.code_chunking import _CHUNK_CHAR_CAP

        blob = "\n".join(f"value_{i} = {i} ;;;" for i in range(2000))
        chunks = chunk_code(blob, "data.unknown")

        assert len(chunks) > 1
        assert all(len(c["code"]) <= _CHUNK_CHAR_CAP for c in chunks)
        # Nothing is lost in the split.
        assert sum(len(c["code"].split("\n")) for c in chunks) == len(
            blob.split("\n")
        )

    def test_a_long_function_is_split_but_only_the_first_piece_keeps_the_symbol(
        self,
    ) -> None:
        """The tail of a split body no longer starts at the definition.

        Labelling it with the same symbol would claim a line range that does
        not contain it — and those line numbers are shown to the agent.
        """
        body = "\n".join(f"    step_{i}()" for i in range(400))
        chunks = chunk_code(f"def enormous():\n{body}\n", "big.py")

        assert len(chunks) > 1
        assert chunks[0]["symbol"] == "enormous"
        assert all(c["symbol"] == "" for c in chunks[1:])

    def test_a_constants_file_is_not_cut_into_one_chunk_per_line(self) -> None:
        """`const X = 1` is not a definition boundary; `const X = () =>` is.

        Matching every assignment would shatter config and constants files into
        hundreds of near-identical vectors, which crowd real source out of the
        top-k.
        """
        source = "\n".join(f'export const KEY_{i} = "{i}";' for i in range(50))
        chunks = chunk_code(source, "constants.ts")

        assert len(chunks) == 1

    def test_empty_and_whitespace_files_produce_nothing(self) -> None:
        assert chunk_code("", "a.py") == []
        assert chunk_code("   \n\n  \n", "a.py") == []


# ---------------------------------------------------------------------------
# Step 2/3 — namespace, retrieval
# ---------------------------------------------------------------------------


class TestCodeNamespace:
    def test_is_separate_from_the_knowledge_namespace(self) -> None:
        """Same index, different namespace — see code_namespace's docstring."""
        from app.services.knowledge_service import knowledge_namespace

        project = uuid.uuid4()
        assert code_namespace("u", project) != knowledge_namespace("u", project)
        assert code_namespace("u", project).endswith(":code")

    def test_scoped_per_project(self) -> None:
        assert code_namespace("u", uuid.uuid4()) != code_namespace("u", uuid.uuid4())


class TestCodeIndexDeletion:
    """Removing the row must remove the vectors, or retrieval outlives its record."""

    @pytest.mark.asyncio
    async def test_deleting_the_code_row_wipes_the_code_namespace(self) -> None:
        """The per-source delete cannot reach them.

        Code vectors are keyed per file path while the row is keyed by
        repository, so a `source_ref`-targeted delete matches nothing and the
        whole index is orphaned.
        """
        from app.services.knowledge_service import delete_knowledge_source

        row = MagicMock()
        row.source_type = "code"
        row.source_ref = "org/repo"
        row.user_id = "u"
        row.project_id = None

        db = MagicMock()
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        wipe = AsyncMock()
        per_source = AsyncMock()
        with (
            patch(
                "app.services.knowledge_service.delete_code_index_vectors", new=wipe
            ),
            patch(
                "app.services.knowledge_service.delete_source_vectors",
                new=per_source,
            ),
        ):
            await delete_knowledge_source(row, db)

        wipe.assert_awaited_once()
        per_source.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_other_source_types_still_use_the_per_source_delete(self) -> None:
        from app.services.knowledge_service import delete_knowledge_source

        row = MagicMock()
        row.source_type = "confluence"
        row.source_ref = "42"
        row.user_id = "u"
        row.project_id = None

        db = MagicMock()
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        wipe = AsyncMock()
        per_source = AsyncMock()
        with (
            patch(
                "app.services.knowledge_service.delete_code_index_vectors", new=wipe
            ),
            patch(
                "app.services.knowledge_service.delete_source_vectors",
                new=per_source,
            ),
        ):
            await delete_knowledge_source(row, db)

        per_source.assert_awaited_once()
        wipe.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_wipe_failure_does_not_block_removing_the_row(self) -> None:
        """Same rule the other deletion helpers follow: a vector-store hiccup
        must not leave a row nobody can get rid of."""
        from app.services.knowledge_service import delete_code_index_vectors

        with (
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key",
                new="test-key",
            ),
            patch(
                "app.services.knowledge_service._delete_namespace_sync",
                side_effect=RuntimeError("pinecone down"),
            ),
        ):
            await delete_code_index_vectors("u", None)


def _match(path: str, lines: str = "1-50", score: float = 0.9) -> MagicMock:
    match = MagicMock()
    match.metadata = {
        "source": "code",
        "source_id": path,
        "title": lines,
        "text": f"# {path}\n\ndef thing(): ...",
    }
    match.score = score
    return match


def _pinecone_returning(matches: list) -> MagicMock:
    index = MagicMock()
    index.query.return_value = MagicMock(matches=matches)
    client = MagicMock()
    client.Index.return_value = index
    return client


class TestFindCandidateFiles:
    @pytest.mark.asyncio
    async def test_returns_paths_and_line_ranges_never_chunk_text(self) -> None:
        """The whole design rests on this.

        Returning chunk text would let the model conclude "not implemented"
        from a sample it cannot know is complete — the false `fail` the
        verification prompt's evidence rules exist to prevent.
        """
        client = _pinecone_returning([_match("backend/app/api/v1/verification.py")])

        with (
            patch(
                "app.services.code_index_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.code_index_service.pinecone.Pinecone",
                return_value=client,
            ),
            patch(
                "app.services.code_index_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            result = await find_candidate_files("u", uuid.uuid4(), ["log in"])

        assert result == [
            [{"path": "backend/app/api/v1/verification.py", "lines": "1-50"}]
        ]
        assert "def thing" not in str(result)

    @pytest.mark.asyncio
    async def test_several_hits_in_one_file_collapse_to_one_candidate(self) -> None:
        """Chunks are an implementation detail; the agent opens files.

        Collapsing is also what keeps the injected block ~200 tokens instead of
        listing the same path eight times.
        """
        client = _pinecone_returning(
            [
                _match("a.py", "1-40", score=0.95),
                _match("a.py", "80-120", score=0.90),
                _match("b.py", "1-30", score=0.85),
            ]
        )

        with (
            patch(
                "app.services.code_index_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.code_index_service.pinecone.Pinecone",
                return_value=client,
            ),
            patch(
                "app.services.code_index_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            result = await find_candidate_files("u", None, ["log in"])

        assert result[0] == [
            # Best-scoring chunk decides the range shown for its file.
            {"path": "a.py", "lines": "1-40"},
            {"path": "b.py", "lines": "1-30"},
        ]

    @pytest.mark.asyncio
    async def test_one_embeddings_request_covers_every_scenario(self) -> None:
        """Not one request per scenario — the same rule query_knowledge_base_batch
        follows, and the reason a twenty-scenario run does not pay twenty
        round-trips before it starts."""
        embed = AsyncMock(return_value=[[0.1] * 1536, [0.2] * 1536, [0.3] * 1536])
        client = _pinecone_returning([_match("a.py")])

        with (
            patch("app.services.code_index_service._embed_chunks", new=embed),
            patch(
                "app.services.code_index_service.pinecone.Pinecone",
                return_value=client,
            ),
            patch(
                "app.services.code_index_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            await find_candidate_files("u", None, ["one", "two", "three"])

        assert embed.await_count == 1
        assert embed.await_args.args[0] == ["one", "two", "three"]
        # Voyage embeds stored text and search text asymmetrically.
        assert embed.await_args.kwargs["input_type"] == "query"

    @pytest.mark.asyncio
    async def test_an_empty_index_yields_no_candidates_not_an_error(self) -> None:
        client = _pinecone_returning([])

        with (
            patch(
                "app.services.code_index_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.code_index_service.pinecone.Pinecone",
                return_value=client,
            ),
            patch(
                "app.services.code_index_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            result = await find_candidate_files("u", None, ["log in"])

        assert result == [[]]

    @pytest.mark.asyncio
    async def test_a_failure_degrades_to_no_candidates(self) -> None:
        """Discovery is an accelerator. A run must work without it, exactly as
        it does today, so nothing here may raise into the verification stream."""
        with (
            patch(
                "app.services.code_index_service._embed_chunks",
                new=AsyncMock(side_effect=RuntimeError("vendor down")),
            ),
            patch(
                "app.services.code_index_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            result = await find_candidate_files("u", None, ["a", "b"])

        assert result == [[], []]

    @pytest.mark.asyncio
    async def test_no_pinecone_configured_yields_aligned_empties(self) -> None:
        with patch(
            "app.services.code_index_service.settings.pinecone_api_key", new=""
        ):
            result = await find_candidate_files("u", None, ["a", "b", "c"])

        assert result == [[], [], []]


class TestRetrievalDiagnostics:
    """A timeout has to say which phase was slow, or it says nothing useful."""

    def test_names_every_phase_and_marks_the_ones_that_never_ran(self) -> None:
        from app.services.code_index_service import _format_timings

        # Embedding finished, the query never did — which on a timeout is
        # exactly the fact worth having.
        rendered = _format_timings({"embed": 1.5, "connect": 0.25})

        assert "embed=1.50s" in rendered
        assert "connect=0.25s" in rendered
        assert "query=—" in rendered

    @pytest.mark.asyncio
    async def test_a_timeout_logs_the_phase_breakdown(self, caplog) -> None:
        import logging

        async def _never_returns(*args, **kwargs):
            await asyncio.sleep(60)

        with (
            patch(
                "app.services.code_index_service.settings.pinecone_api_key",
                new="test-key",
            ),
            patch(
                "app.services.code_index_service._RETRIEVAL_BUDGET_SECONDS", 0.05
            ),
            patch(
                "app.services.code_index_service._embed_chunks",
                new=_never_returns,
            ),
            caplog.at_level(logging.WARNING),
        ):
            result = await find_candidate_files("u", None, ["a"])

        assert result == [[]]
        assert "timed out" in caplog.text
        # Embedding never completed, so it has no time against it.
        assert "embed=—" in caplog.text

    @pytest.mark.asyncio
    async def test_over_fetch_is_not_multiplied_twice(self) -> None:
        """`_query_matches_hybrid` already over-fetches by _QUERY_OVERFETCH.

        Multiplying again here pulled 6x top_k chunks per scenario, each
        carrying its full text in metadata — which is what blew the budget.
        """
        seen: dict = {}

        async def _hybrid(index, namespace, vector, text, top_k):
            seen["top_k"] = top_k
            return []

        with (
            patch(
                "app.services.code_index_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.code_index_service.pinecone.Pinecone",
                return_value=_pinecone_returning([]),
            ),
            patch(
                "app.services.code_index_service.settings.pinecone_api_key",
                new="test-key",
            ),
            patch(
                "app.services.code_index_service._query_matches_hybrid", new=_hybrid
            ),
        ):
            await find_candidate_files("u", None, ["log in"], top_k=8)

        assert seen["top_k"] == 8


class TestLineRangeLabel:
    def test_spans_every_chunk_of_the_file(self) -> None:
        items = [
            ({"start_line": 40, "end_line": 95}, []),
            ({"start_line": 12, "end_line": 30}, []),
        ]
        assert _line_range_label(items) == "12-95"


class TestFormatCandidateBlock:
    def test_says_the_list_is_not_evidence(self) -> None:
        """A list that reads as authoritative but is nearest-neighbour output
        is exactly what turns into a confident wrong verdict."""
        block = format_candidate_block(
            [{"path": "backend/app/api/v1/verification.py", "lines": "40-95"}]
        )

        assert "NOT evidence" in block
        assert "backend/app/api/v1/verification.py" in block
        assert "lines 40-95" in block

    def test_no_candidates_means_no_block(self) -> None:
        assert format_candidate_block([]) == ""

    def test_a_candidate_without_a_range_still_renders(self) -> None:
        block = format_candidate_block([{"path": "a.py", "lines": ""}])
        assert "a.py" in block
        assert "lines" not in block.split("a.py")[1]


# ---------------------------------------------------------------------------
# Step 4/5 — wiring and freshness
# ---------------------------------------------------------------------------


def _index_row(repo: str = "org/repo", sha: str = "abc123") -> MagicMock:
    row = MagicMock()
    row.source_ref = repo
    row.indexed_ref = sha
    row.page_count = 10
    row.created_at = datetime.now(UTC)
    return row


async def _run(
    llm,
    *,
    code_index_enabled: bool = True,
    index_row=None,
    head_sha: str = "abc123",
    candidates: list[list[dict]] | None = None,
) -> list[dict]:
    with (
        patch(
            "app.services.code_index_service.get_code_index",
            new=AsyncMock(return_value=index_row),
        ),
        patch(
            "app.services.code_index_service.resolve_commit_sha",
            new=AsyncMock(return_value=head_sha),
        ),
        patch(
            "app.services.code_index_service.find_candidate_files",
            new=AsyncMock(
                return_value=candidates
                if candidates is not None
                else [[{"path": "src/auth.py", "lines": "10-40"}]]
            ),
        ),
    ):
        events = await _collect(
            run_agentic_verification(
                session_id="s",
                user_id="u",
                bdd_content=_SINGLE_BDD,
                mode="full_repo",
                github_input="https://github.com/org/repo",
                llm=llm,
                db=_make_db_session(),
                pat="pat",
                code_index_enabled=code_index_enabled,
            )
        )
    return _parse_events(events)


def _scenario_message(messages: list[dict]) -> str:
    """The per-scenario half of the prompt.

    Asserting a block is absent has to look here rather than at the whole
    prompt: the system prompt names CANDIDATE FILES in rule 0b — the rule that
    tells the model the list cannot prove absence — so it is present in every
    run whether or not any candidates were injected.
    """
    return str(messages[-1]["content"])


def _capturing_llm() -> tuple[MagicMock, dict]:
    captured: dict = {}

    async def _gen(*args, **kwargs):
        captured["messages"] = kwargs["messages"]
        await _simulate_tool_read(kwargs)
        return _make_verdict_json(
            "00000000-0000-0000-0000-000000000001", "User can log in"
        )

    llm = MagicMock()
    llm.generate_with_tools = _gen
    return llm, captured


class TestCandidateInjection:
    @pytest.mark.asyncio
    async def test_candidates_reach_the_prompt_when_the_index_is_current(self) -> None:
        llm, captured = _capturing_llm()
        await _run(llm, index_row=_index_row())

        prompt = _prompt_text(captured["messages"])
        assert "CANDIDATE FILES" in prompt
        assert "src/auth.py" in prompt

    @pytest.mark.asyncio
    async def test_the_block_is_per_scenario_not_in_the_cached_prefix(self) -> None:
        """The shared prefix must stay byte-identical across scenarios.

        Candidates differ per scenario, so folding them in would make the
        "shared" half different every time — not a cache hit for anyone.
        """
        llm, captured = _capturing_llm()
        await _run(llm, index_row=_index_row())

        cached = next(
            m for m in captured["messages"] if m.get("cache_control")
        )["content"]
        last = captured["messages"][-1]["content"]

        assert "CANDIDATE FILES" not in cached
        assert "CANDIDATE FILES" in last

    @pytest.mark.asyncio
    async def test_default_off_sends_no_candidates_and_queries_nothing(self) -> None:
        llm, captured = _capturing_llm()

        find = AsyncMock(return_value=[[]])
        with patch(
            "app.services.code_index_service.find_candidate_files", new=find
        ):
            await _collect(
                run_agentic_verification(
                    session_id="s",
                    user_id="u",
                    bdd_content=_SINGLE_BDD,
                    mode="full_repo",
                    github_input="https://github.com/org/repo",
                    llm=llm,
                    db=_make_db_session(),
                    pat="pat",
                )
            )

        find.assert_not_awaited()
        assert "CANDIDATE FILES" not in _scenario_message(captured["messages"])

    @pytest.mark.asyncio
    async def test_rule_0b_tells_the_model_the_list_cannot_prove_absence(
        self,
    ) -> None:
        """Without this the model reads an empty or irrelevant candidate list
        as proof the feature is missing."""
        llm, captured = _capturing_llm()
        await _run(llm, index_row=_index_row())

        system = captured["messages"][0]["content"]
        assert "CANDIDATE FILES" in system
        assert "NOT evidence" in system
        assert "list_directory" in system

    @pytest.mark.asyncio
    async def test_rule_0b_is_absent_when_no_candidates_can_arrive(self) -> None:
        """~140 tokens on every round of every scenario, explaining how to read
        a block that will never be sent. Waste, and one more instruction for
        the model to reconcile against input it cannot see."""
        llm, captured = _capturing_llm()

        await _collect(
            run_agentic_verification(
                session_id="s",
                user_id="u",
                bdd_content=_SINGLE_BDD,
                mode="full_repo",
                github_input="https://github.com/org/repo",
                llm=llm,
                db=_make_db_session(),
                pat="pat",
            )
        )

        system = captured["messages"][0]["content"]
        assert "CANDIDATE FILES" not in system
        # The sentinel it replaces must not leak into the prompt either.
        assert "<<CANDIDATE_RULE>>" not in system
        # The rest of the strategy is untouched.
        assert "REPOSITORY FILE TREE" in system
        assert "TRUNCATION" in system

    @pytest.mark.asyncio
    async def test_an_empty_candidate_list_injects_no_block(self) -> None:
        """A heading with nothing under it reads as "we looked and found none"."""
        llm, captured = _capturing_llm()
        await _run(llm, index_row=_index_row(), candidates=[[]])

        assert "CANDIDATE FILES" not in _scenario_message(captured["messages"])


class TestFreshness:
    @pytest.mark.asyncio
    async def test_a_stale_index_is_refused_and_warned_about(self) -> None:
        """Prefer skipping retrieval over using a stale index: it still answers
        confidently, for files that may have been renamed or deleted since."""
        llm, captured = _capturing_llm()
        events = await _run(
            llm, index_row=_index_row(sha="old111"), head_sha="new222"
        )

        warning = next(e for e in events if e["type"] == "warning")
        assert "stale" in warning["message"]
        assert "old111"[:7] in warning["message"]
        assert "CANDIDATE FILES" not in _scenario_message(captured["messages"])
        # The run still produced its verdict.
        assert any(e["type"] == "verdict" for e in events)

    @pytest.mark.asyncio
    async def test_never_indexed_warns_and_continues(self) -> None:
        llm, captured = _capturing_llm()
        events = await _run(llm, index_row=None)

        warning = next(e for e in events if e["type"] == "warning")
        assert "not been indexed" in warning["message"]
        assert any(e["type"] == "verdict" for e in events)

    @pytest.mark.asyncio
    async def test_an_index_of_a_different_repo_is_refused(self) -> None:
        """The index belongs to the project, but a project can verify against
        more than one repository over its life."""
        llm, _ = _capturing_llm()
        events = await _run(llm, index_row=_index_row(repo="org/other"))

        warning = next(e for e in events if e["type"] == "warning")
        assert "org/other" in warning["message"]

    @pytest.mark.asyncio
    async def test_an_unresolvable_head_is_treated_as_stale(self) -> None:
        """The comparison could not be made, which is the case the check is
        for — not a reason to assume the index is current."""
        llm, captured = _capturing_llm()
        events = await _run(llm, index_row=_index_row(), head_sha="")

        assert any(e["type"] == "warning" for e in events)
        assert "CANDIDATE FILES" not in _scenario_message(captured["messages"])
