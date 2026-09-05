"""Tests for the `partial` verdict (Finding 4).

`partial` exists for the case the binary verdict could not express: the
behaviour is present in the code but is not reachable the way the scenario
describes - wired to a different trigger, or built on one side of the stack
only. Reporting those as `fail` sends a developer to rebuild working code.

Because `partial` asserts that implementing code WAS read, it is held to the
same evidence and hedging bars as `pass` and `fail`. These tests pin that: the
new verdict must not become the one status a model can reach on no evidence.
"""

import uuid

import pytest

from app.services.agentic_verification_service import _build_verdict

REPO = "acme/widgets"


def _scenario() -> dict:
    return {"id": str(uuid.uuid4()), "title": "Refund is issued on cancellation"}


def _verdict(**overrides) -> dict:
    base = {
        "scenario_id": "ignored, overwritten from the scenario",
        "scenario_title": "ignored, overwritten from the scenario",
        "status": "partial",
        "justification": (
            "stripe.refunds.create is called in payments.py refund_payment, but "
            "no cancellation path invokes it"
        ),
        "code_reference": {
            "file": "payments.py",
            "function": "refund_payment",
            "line": 221,
        },
        "github_links": [],
        "implementation_suggestion": "call the refund from the cancel route",
    }
    base.update(overrides)
    return base


def _known_files() -> dict[str, str]:
    return {"payments.py": "def refund_payment():\n    stripe.refunds.create()\n"}


def test_partial_verdict_is_accepted() -> None:
    verdict = _build_verdict(
        _verdict(),
        _scenario(),
        REPO,
        tool_errors=[],
        tool_successes=[True],
        known_files=_known_files(),
    )

    assert verdict.status == "partial"
    assert verdict.implementation_suggestion


def test_partial_without_a_suggestion_gets_one() -> None:
    """A partial verdict whose whole value is naming the gap, but which names
    no gap, is a fail with extra steps."""
    verdict = _build_verdict(
        _verdict(implementation_suggestion=None),
        _scenario(),
        REPO,
        tool_errors=[],
        tool_successes=[True],
        known_files=_known_files(),
    )

    assert verdict.status == "partial"
    assert verdict.implementation_suggestion


def test_partial_on_no_evidence_is_downgraded_to_inconclusive() -> None:
    """`partial` claims implementing code was read. With no file read, that
    claim is unfounded - and exempting it would leave exactly one verdict
    reachable from a directory listing alone."""
    verdict = _build_verdict(
        _verdict(),
        _scenario(),
        REPO,
        tool_errors=[],
        tool_successes=[True],
        evidence_provided=False,
        known_files={},
    )

    assert verdict.status == "inconclusive"


def test_hedged_partial_is_downgraded_to_inconclusive() -> None:
    """Same rule as a hedged fail: the words the model chose are better
    evidence of its confidence than the label it picked."""
    verdict = _build_verdict(
        _verdict(
            justification=(
                "the refund logic appears to exist but I cannot verify whether "
                "any cancellation path calls it"
            )
        ),
        _scenario(),
        REPO,
        tool_errors=[],
        tool_successes=[True],
        known_files=_known_files(),
    )

    assert verdict.status == "inconclusive"


def test_partial_claiming_missing_access_control_needs_the_router_read() -> None:
    """A guard is normally applied where a route is registered. Judging it from
    a component alone establishes nothing, whichever verdict it is dressed as.
    """
    verdict = _build_verdict(
        _verdict(
            justification=(
                "the handler exists but there is no authorization check, so "
                "access control is missing for non-participants"
            )
        ),
        _scenario(),
        REPO,
        tool_errors=[],
        tool_successes=[True],
        known_files={
            "components/RefundButton.tsx": "export function RefundButton() {}"
        },
    )

    assert verdict.status == "inconclusive"


def test_pass_still_forbids_a_suggestion() -> None:
    """The existing contract is unchanged by the new verdict."""
    verdict = _build_verdict(
        _verdict(status="pass", implementation_suggestion=None),
        _scenario(),
        REPO,
        tool_errors=[],
        tool_successes=[True],
        known_files=_known_files(),
    )

    assert verdict.status == "pass"
    assert verdict.implementation_suggestion is None


def test_unknown_status_is_rejected_by_the_schema() -> None:
    with pytest.raises(ValueError):
        _build_verdict(
            _verdict(status="mostly"),
            _scenario(),
            REPO,
            tool_errors=[],
            tool_successes=[True],
            known_files=_known_files(),
        )
