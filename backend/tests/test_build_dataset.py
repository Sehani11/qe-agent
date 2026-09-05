"""Tests for training/build_dataset.py — the training-data consent filter.

These live under backend/tests so they run with the normal `pytest` command
rather than needing a separate invocation. `training/` sits outside the backend
package, so the module is loaded by path.

The filter tested here is the ONLY enforcement point for TRAINING_DATA_OPT_IN.
The application stamps the flag onto rows (tests in test_bdd.py), but nothing
else prevents an opted-out row from reaching a training set. If this regresses,
the corpus silently grows and the failure looks like success.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_TRAINING_DIR = Path(__file__).resolve().parents[2] / "training"


def _load_build_dataset():
    """Import training/build_dataset.py by path (it is not an installed package)."""
    if str(_TRAINING_DIR) not in sys.path:
        sys.path.insert(0, str(_TRAINING_DIR))
    spec = importlib.util.spec_from_file_location(
        "build_dataset", _TRAINING_DIR / "build_dataset.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves its own module via sys.modules,
    # and fails with an opaque AttributeError if it is not there yet.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bd = _load_build_dataset()


# ---------------------------------------------------------------------------
# The consent filter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["uploaded", "edited", "authored", "generated", "all"])
def test_opted_out_rows_are_excluded_for_every_source(source: str) -> None:
    """No --db-source choice may bypass the opt-out."""
    query, _, _, _ = bd.build_db_queries(source, None)
    assert "training_opt_in = true" in query


@pytest.mark.parametrize("source", ["uploaded", "edited", "authored", "generated", "all"])
def test_opt_out_survives_a_limit(source: str) -> None:
    """--limit must narrow the result set, never relax the consent filter."""
    query, params, _, _ = bd.build_db_queries(source, 5)
    assert "training_opt_in = true" in query
    assert params["limit"] == 5


def test_only_one_where_clause_is_emitted() -> None:
    """Two WHERE clauses is invalid SQL - the trap this filter had to avoid."""
    query, _, _, _ = bd.build_db_queries("uploaded", 10)
    assert query.count("WHERE") == 1
    assert "AND" in query


def test_source_filter_is_parameterised_not_interpolated() -> None:
    """The source values must be bound, never concatenated into the SQL."""
    query, params, _, _ = bd.build_db_queries("uploaded", None)
    assert ":sources" in query
    assert "uploaded" not in query
    assert params["sources"] == ["uploaded"]


def test_all_source_applies_no_source_predicate() -> None:
    query, params, _, _ = bd.build_db_queries("all", None)
    assert ":sources" not in query
    assert "sources" not in params
    # ...but the consent filter still applies
    assert "training_opt_in = true" in query


# ---------------------------------------------------------------------------
# The exclusion counter
# ---------------------------------------------------------------------------


def test_exclusion_count_targets_opted_out_rows() -> None:
    """An all-excluded database must be distinguishable from an empty one."""
    _, _, excluded_query, excluded_params = bd.build_db_queries("uploaded", None)
    assert "count(*)" in excluded_query
    assert "training_opt_in = false" in excluded_query
    assert excluded_params["sources"] == ["uploaded"]


def test_exclusion_count_ignores_limit() -> None:
    """The operator wants the true total excluded, not a page of it."""
    _, _, excluded_query, excluded_params = bd.build_db_queries("uploaded", 5)
    assert "LIMIT" not in excluded_query
    assert "limit" not in excluded_params


def test_exclusion_count_is_unscoped_for_all_source() -> None:
    _, _, excluded_query, excluded_params = bd.build_db_queries("all", None)
    assert ":sources" not in excluded_query
    assert excluded_params == {}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_stats_reports_the_opted_out_count() -> None:
    """The count must be visible, or an excluded corpus looks like a missing one."""
    stats = bd.Stats()
    stats.opted_out = 4
    rendered = stats.render()
    assert "opted out" in rendered
    assert "4" in rendered


def test_db_engine_disables_prepared_statement_cache() -> None:
    """Supabase's PgBouncer pooler rejects prepared statements.

    Without statement_cache_size=0 this raises DuplicatePreparedStatementError
    intermittently, which would abort a corpus build partway through.
    """
    source = (_TRAINING_DIR / "build_dataset.py").read_text(encoding="utf-8")
    for engine_call in source.split("create_async_engine(")[1:]:
        assert "statement_cache_size" in engine_call.split(")")[0]


# ---------------------------------------------------------------------------
# The uploaded corpus source (Story 6.7)
# ---------------------------------------------------------------------------


def test_uploads_query_excludes_opted_out_rows() -> None:
    """The manual-upload page must not be a route around the opt-out."""
    query, _, _, _ = bd.build_upload_queries(None)
    assert "training_opt_in = true" in query
    assert "training_datasets" in query


def test_uploads_query_emits_one_where_clause() -> None:
    query, _, _, _ = bd.build_upload_queries("user-a")
    assert query.count("WHERE") == 1
    assert "AND" in query


def test_uploads_user_filter_is_parameterised() -> None:
    query, params, _, _ = bd.build_upload_queries("user-a")
    assert ":user_id" in query
    assert "user-a" not in query
    assert params["user_id"] == "user-a"


def test_uploads_query_without_a_user_filter_still_filters_consent() -> None:
    query, params, _, _ = bd.build_upload_queries(None)
    assert ":user_id" not in query
    assert params == {}
    assert "training_opt_in = true" in query


def test_uploads_exclusion_count_targets_opted_out_rows() -> None:
    _, _, excluded_query, excluded_params = bd.build_upload_queries("user-a")
    assert "count(*)" in excluded_query
    assert "training_opt_in = false" in excluded_query
    assert excluded_params["user_id"] == "user-a"


def test_db_query_selects_content_format() -> None:
    """The parser is chosen per row, so the column must come back with it."""
    query, _, _, _ = bd.build_db_queries("all", None)
    assert "content_format" in query


def test_a_json_row_is_routed_to_the_json_parser() -> None:
    """`generated` rows are serialized BDDGenerateResponse, not Gherkin."""
    import json

    stats = bd.Stats()
    content = json.dumps(
        {
            "scenarios": [
                {
                    "source_ac_clause": "AC1",
                    "feature": "Password reset",
                    "scenario": "A user requests a reset link",
                    "given": "a registered user with a verified email address",
                    "when": "they submit the password reset form",
                    "then": "a reset link is emailed to their address",
                }
            ]
        }
    )

    doc = bd._accept(
        content, origin="db:generated:1", stats=stats, content_format="json"
    )

    assert doc is not None
    assert doc.feature == "Password reset"
    assert stats.unparsable == 0


def test_the_same_json_row_is_unparsable_without_the_format_hint() -> None:
    """Proves the routing is what fixes it, not a change in the Gherkin parser."""
    import json

    stats = bd.Stats()
    content = json.dumps(
        {
            "scenarios": [
                {
                    "source_ac_clause": "AC1",
                    "feature": "Password reset",
                    "scenario": "A user requests a reset link",
                    "given": "a registered user with a verified email address",
                    "when": "they submit the password reset form",
                    "then": "a reset link is emailed to their address",
                }
            ]
        }
    )

    doc = bd._accept(
        content, origin="db:generated:1", stats=stats, content_format="gherkin"
    )

    assert doc is None
    assert stats.unparsable == 1


def test_a_malformed_json_row_still_counts_as_unparsable() -> None:
    stats = bd.Stats()

    doc = bd._accept(
        "{broken", origin="db:generated:2", stats=stats, content_format="json"
    )

    assert doc is None
    assert stats.unparsable == 1


def test_generated_rows_stay_out_of_the_default_source() -> None:
    """Parsing them is not the same as training on them (self-distillation)."""
    query, params, _, _ = bd.build_db_queries("uploaded", None)
    assert params["sources"] == ["uploaded"]
    assert "source = ANY(:sources)" in query


def test_uploads_limit_is_pushed_into_sql() -> None:
    """Every returned row costs a Storage download - trimming later is waste."""
    query, params, _, _ = bd.build_upload_queries(None, 10)
    assert "LIMIT :limit" in query
    assert params["limit"] == 10


def test_uploads_limit_survives_the_consent_filter() -> None:
    query, _, _, _ = bd.build_upload_queries("user-a", 5)
    assert "training_opt_in = true" in query
    assert query.count("WHERE") == 1


def test_uploads_exclusion_count_ignores_limit() -> None:
    _, _, excluded_query, excluded_params = bd.build_upload_queries("user-a", 5)
    assert "LIMIT" not in excluded_query
    assert "limit" not in excluded_params


# --- Processing uploaded rows ----------------------------------------------

_GOOD_FEATURE = """Feature: Password reset

  Scenario: A user requests a reset link
    Given a registered user with a verified email address
    When they submit the password reset form
    Then a reset link is emailed to their address
"""

_INCOMPLETE_FEATURE = """Feature: Partial

  Scenario: Nothing is ever asserted here
    Given a registered user with a verified email address
    When they submit the password reset form
"""


def _pair_line(n: int = 1) -> str:
    """One JSONL training pair. `n` varies the content so distinct pairs differ."""
    import json

    target = {
        "scenarios": [
            {
                "source_ac_clause": f"AC{n}",
                "feature": "Password reset",
                "scenario": f"A user requests a reset link ({n})",
                "given": "a registered user with a verified email address",
                "when": "they submit the password reset form",
                "then": "a reset link is emailed to their address",
            }
        ]
    }
    return json.dumps(
        {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": f"AC{n}: reset password"},
                {"role": "assistant", "content": json.dumps(target)},
            ]
        }
    )


def _patch_download(mapping: dict[str, str]):
    """Patch the storage singleton the builder downloads uploads through."""
    from unittest.mock import AsyncMock, patch

    async def _download(folder: str, path: str) -> bytes:
        return mapping[path].encode()

    return patch(
        "app.services.training_data_service.storage_service.download_file",
        new=AsyncMock(side_effect=_download),
    )


@pytest.mark.asyncio
async def test_uploaded_feature_files_flow_through_back_generation() -> None:
    stats = bd.Stats()
    rows = [("id-1", "reset.feature", "feature", "u/training-data/id-1/reset.feature")]

    with _patch_download({rows[0][3]: _GOOD_FEATURE}):
        docs, ready = await bd.process_upload_rows(rows, stats)

    assert len(docs) == 1
    assert docs[0].origin == "upload:id-1:reset.feature"
    assert ready == []  # .feature still needs its AC reconstructed
    assert stats.kept == 1


@pytest.mark.asyncio
async def test_uploaded_jsonl_pairs_bypass_back_generation() -> None:
    """A .jsonl line is ALREADY a pair - re-inventing its input side is waste."""
    stats = bd.Stats()
    rows = [("id-2", "pairs.jsonl", "jsonl", "u/training-data/id-2/pairs.jsonl")]
    content = "\n".join(_pair_line(n) for n in range(3))

    with _patch_download({rows[0][3]: content}):
        docs, ready = await bd.process_upload_rows(rows, stats)

    assert docs == []  # nothing to back-generate
    assert len(ready) == 3
    assert stats.ready_pairs == 3
    assert all(r["messages"][-1]["role"] == "assistant" for r in ready)

    # One origin PER PAIR, namespaced under the upload.
    #
    # This used to stamp the upload id alone, making a whole uploaded file one
    # indivisible origin. The split is by origin, so a 179-pair upload could
    # only go entirely to train or entirely to holdout: with two uploaded files
    # a real run came out "2 train / 179 holdout", which cannot train. It also
    # disagreed with collect_pair_files, which splits the identical content per
    # record — and the two paths are supposed to reach the same verdict.
    origins = {r["meta"]["origin"] for r in ready}
    assert len(origins) == 3
    assert all(o.startswith("upload:id-2:pairs.jsonl#") for o in origins)


@pytest.mark.asyncio
async def test_uploaded_pairs_keep_their_own_origin_so_one_ticket_stays_together():
    """A record that names its source keeps it, so its pairs are not scattered.

    Re-uploading a previously built train.jsonl is the case that matters: every
    line already carries the feature file it came from, and pairs from one file
    are near-duplicates that must not straddle the holdout boundary.
    """
    import json as _json

    stats = bd.Stats()
    rows = [("id-9", "prev.jsonl", "jsonl", "u/training-data/id-9/prev.jsonl")]

    lines = []
    for n, source in enumerate(["login.feature", "login.feature", "signup.feature"]):
        record = _json.loads(_pair_line(n))
        record["meta"] = {"origin": source}
        lines.append(_json.dumps(record))

    with _patch_download({rows[0][3]: "\n".join(lines)}):
        _, ready = await bd.process_upload_rows(rows, stats)

    origins = [r["meta"]["origin"] for r in ready]
    # Two source files, so two groups — not three, and not one.
    assert len(set(origins)) == 2
    assert origins[0] == origins[1] != origins[2]
    # Namespaced, so another upload naming "login.feature" cannot merge into it.
    assert all(o.startswith("upload:id-9:prev.jsonl#") for o in origins)


@pytest.mark.asyncio
async def test_uploaded_feature_failing_quality_is_counted_not_kept() -> None:
    stats = bd.Stats()
    rows = [("id-3", "partial.feature", "feature", "u/training-data/id-3/p.feature")]

    with _patch_download({rows[0][3]: _INCOMPLETE_FEATURE}):
        docs, ready = await bd.process_upload_rows(rows, stats)

    assert docs == [] and ready == []
    assert stats.incomplete_steps == 1
    assert stats.kept == 0


@pytest.mark.asyncio
async def test_a_missing_stored_object_does_not_abort_the_build() -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.storage_service import StorageServiceError

    stats = bd.Stats()
    rows = [
        ("id-4", "gone.feature", "feature", "u/training-data/id-4/gone.feature"),
        ("id-5", "ok.feature", "feature", "u/training-data/id-5/ok.feature"),
    ]

    async def _download(folder: str, path: str) -> bytes:
        if "gone" in path:
            raise StorageServiceError("object not found")
        return _GOOD_FEATURE.encode()

    with patch(
        "app.services.training_data_service.storage_service.download_file",
        new=AsyncMock(side_effect=_download),
    ):
        docs, _ = await bd.process_upload_rows(rows, stats)

    assert len(docs) == 1  # the reachable one still made it
    assert stats.unparsable == 1


@pytest.mark.asyncio
async def test_the_same_jsonl_uploaded_twice_is_not_counted_twice() -> None:
    """Duplicate pairs reweight a fine-tune toward whatever was duplicated."""
    stats = bd.Stats()
    rows = [
        ("id-6", "pairs.jsonl", "jsonl", "u/training-data/id-6/pairs.jsonl"),
        ("id-7", "copy.jsonl", "jsonl", "u/training-data/id-7/copy.jsonl"),
    ]
    content = _pair_line()

    with _patch_download({rows[0][3]: content, rows[1][3]: content}):
        _, ready = await bd.process_upload_rows(rows, stats)

    assert len(ready) == 1
    assert stats.ready_pairs == 1
    assert stats.duplicates == 1


@pytest.mark.asyncio
async def test_distinct_pairs_from_two_files_are_both_kept() -> None:
    """De-duplication must not collapse genuinely different pairs."""
    import json

    stats = bd.Stats()
    rows = [
        ("id-8", "a.jsonl", "jsonl", "u/training-data/id-8/a.jsonl"),
        ("id-9", "b.jsonl", "jsonl", "u/training-data/id-9/b.jsonl"),
    ]
    other = json.loads(_pair_line(2))

    with _patch_download(
        {rows[0][3]: _pair_line(1), rows[1][3]: json.dumps(other)}
    ):
        _, ready = await bd.process_upload_rows(rows, stats)

    assert len(ready) == 2
    assert stats.duplicates == 0
    # Each pair carries the upload it came from, plus a per-record suffix —
    # these records name no origin of their own, so the line number is used.
    assert {r["meta"]["origin"] for r in ready} == {
        "upload:id-8:a.jsonl#1",
        "upload:id-9:b.jsonl#1",
    }


@pytest.mark.asyncio
async def test_a_corrupt_stored_object_is_reported_not_silently_mangled() -> None:
    """Content was UTF-8 validated at upload, so a failure means damage."""
    from unittest.mock import AsyncMock, patch

    stats = bd.Stats()
    rows = [("id-10", "bad.feature", "feature", "u/training-data/id-10/bad.feature")]

    with patch(
        "app.services.training_data_service.storage_service.download_file",
        new=AsyncMock(return_value=b"\xff\xfe\x00not utf8"),
    ):
        docs, ready = await bd.process_upload_rows(rows, stats)

    assert docs == [] and ready == []
    assert stats.unparsable == 1


# ---------------------------------------------------------------------------
# --db-source groups: reaching human-authored rows without self-distillation
# ---------------------------------------------------------------------------


def test_edited_rows_are_selectable_on_their_own() -> None:
    """The strongest signal the app collects used to be unreachable.

    `edited` rows are human corrections of generated output — they say exactly
    where the model was wrong. Before this, the only way to include them was
    `all`, which dragged every `generated` row in with them.
    """
    query, params, _, _ = bd.build_db_queries("edited", None)
    assert params["sources"] == ["edited"]
    assert "source = ANY(:sources)" in query


def test_authored_selects_both_human_written_sources() -> None:
    """Everything a person wrote, and nothing the app wrote itself."""
    _, params, _, _ = bd.build_db_queries("authored", None)
    assert sorted(params["sources"]) == ["edited", "uploaded"]
    assert "generated" not in params["sources"]


def test_authored_exclusion_count_is_scoped_to_the_same_rows() -> None:
    """The skipped counter must describe the set being built, not another one."""
    _, params, excluded_query, excluded_params = bd.build_db_queries("authored", 5)
    assert excluded_params["sources"] == params["sources"]
    assert "training_opt_in = false" in excluded_query
    assert "LIMIT" not in excluded_query


def test_default_source_is_unchanged() -> None:
    """Existing invocations must keep selecting exactly what they did before."""
    query, params, _, _ = bd.build_db_queries("uploaded", None)
    assert params["sources"] == ["uploaded"]
    assert "edited" not in str(params["sources"])
    assert "training_opt_in = true" in query


def test_every_cli_choice_maps_to_a_known_group() -> None:
    """argparse offers exactly what the query builder can serve."""
    assert set(bd.DB_SOURCE_GROUPS) == {
        "uploaded",
        "edited",
        "authored",
        "generated",
        "all",
    }
    # Only "all" means "no source predicate".
    assert bd.DB_SOURCE_GROUPS["all"] is None
    assert all(
        v for k, v in bd.DB_SOURCE_GROUPS.items() if k != "all"
    )
