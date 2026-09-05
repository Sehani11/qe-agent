"""Tests for the two guards that keep a verdict tied to what was actually read.

`_validate_code_reference` only ever acts on evidence: it repoints or drops a
line the read code contradicts, and leaves anything unverifiable exactly as the
model wrote it. A citation landing on the wrong line costs the reader their
trust in every other citation in the report, but a citation invented by the
validator would be worse.

`_is_hedged` catches the other half: a justification that admits it never
established its claim, filed under a decisive status.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.agentic_verification_service import (
    _build_verdict,
    _claims_missing_access_control,
    _is_hedged,
    _make_tool_executor,
    _read_route_registration,
    _validate_code_reference,
)

_FILE = """\
import re


def login(user):
    return True


def logout(user):
    return False
"""


def _ref(**kwargs) -> dict:
    return {"code_reference": {"file": "src/auth.py", **kwargs}}


class TestValidateCodeReference:
    def test_a_correct_line_is_left_alone(self) -> None:
        verdict = _ref(function="login", line=4)
        _validate_code_reference(verdict, {"src/auth.py": _FILE})
        assert verdict["code_reference"]["line"] == 4

    def test_a_line_past_the_end_of_the_file_is_repointed_to_the_function(self) -> None:
        verdict = _ref(function="logout", line=402)
        _validate_code_reference(verdict, {"src/auth.py": _FILE})
        assert verdict["code_reference"]["line"] == 8

    def test_an_in_range_line_on_the_wrong_row_is_repointed(self) -> None:
        # Line 1 is the import, not the function the reference names.
        verdict = _ref(function="login", line=1)
        _validate_code_reference(verdict, {"src/auth.py": _FILE})
        assert verdict["code_reference"]["line"] == 4

    def test_an_impossible_line_with_no_function_to_anchor_is_dropped(self) -> None:
        verdict = _ref(line=999)
        _validate_code_reference(verdict, {"src/auth.py": _FILE})
        assert verdict["code_reference"]["line"] is None

    def test_a_file_the_run_never_read_is_left_untouched(self) -> None:
        # Not reading a file proves nothing about it; "unverified" must not be
        # quietly converted into "wrong".
        verdict = _ref(function="login", line=999)
        _validate_code_reference(verdict, {})
        assert verdict["code_reference"]["line"] == 999

    def test_a_truncated_read_cannot_disprove_a_line(self) -> None:
        partial = _FILE + "\n\n[TRUNCATED — 40000 of 90000 chars shown; ...]"
        verdict = _ref(function="login", line=1_200)
        _validate_code_reference(verdict, {"src/auth.py": partial})
        assert verdict["code_reference"]["line"] == 1_200

    def test_a_leading_slash_still_matches_the_file(self) -> None:
        verdict = {
            "code_reference": {"file": "/src/auth.py", "function": "logout", "line": 1}
        }
        _validate_code_reference(verdict, {"src/auth.py": _FILE})
        assert verdict["code_reference"]["line"] == 8

    def test_an_ambiguous_suffix_is_treated_as_unknown(self) -> None:
        # Two files could be meant, so neither is used to "correct" anything.
        verdict = _ref(function="login", line=999)
        verdict["code_reference"]["file"] = "auth.py"
        _validate_code_reference(
            verdict, {"src/auth.py": _FILE, "legacy/auth.py": _FILE}
        )
        assert verdict["code_reference"]["line"] == 999

    def test_a_missing_code_reference_is_not_an_error(self) -> None:
        verdict: dict = {}
        _validate_code_reference(verdict, {"src/auth.py": _FILE})
        assert verdict == {}


class TestIsHedged:
    @pytest.mark.parametrize(
        "justification",
        [
            "Assuming the API implements the necessary server-side filtering.",
            "This is likely handled in the backend, which I cannot verify.",
            "The specifics are not fully detailed in the available excerpt.",
            "Presumably the charge and availability are rendered.",
            "There is no way to tell from this file.",
            "The filter appears to be missing from the route.",
        ],
    )
    def test_hedged_justifications_are_recognised(self, justification: str) -> None:
        assert _is_hedged(justification) is True

    @pytest.mark.parametrize(
        "justification",
        [
            "The handler lowercases the query and matches name, bio and"
            " specializations.",
            "No cancel route exists; the router defines only accept, reject"
            " and complete.",
            "The middleware assumes a bearer token is present and rejects the"
            " request otherwise.",
        ],
    )
    def test_decisive_justifications_are_not_flagged(self, justification: str) -> None:
        # The last case matters: "assumes" describing the code is a finding, not
        # a hedge, and flagging it would punish precise writing.
        assert _is_hedged(justification) is False

    def test_a_non_string_is_not_hedged(self) -> None:
        assert _is_hedged(None) is False


class TestToolExecutorRecordsReads:
    @pytest.mark.asyncio
    async def test_a_whole_file_read_is_recorded_for_citation_checking(self) -> None:
        executor, _errors, successes, files_read = _make_tool_executor(
            pat="pat", repo="org/repo", default_ref="main"
        )
        with patch(
            "app.services.github_tools.get_file_contents",
            new=AsyncMock(return_value=_FILE),
        ):
            await executor("get_file_contents", {"path": "src/auth.py"})

        assert successes == [True]
        assert files_read == {"src/auth.py": _FILE}

    @pytest.mark.asyncio
    async def test_the_offset_is_passed_through_to_the_tool(self) -> None:
        executor, _errors, _successes, files_read = _make_tool_executor(
            pat="pat", repo="org/repo", default_ref="main"
        )
        fake = AsyncMock(
            return_value="[CONTINUED — chars 40000-40010 of 40010.]\n\nrest"
        )
        with patch("app.services.github_tools.get_file_contents", new=fake):
            await executor(
                "get_file_contents", {"path": "src/auth.py", "offset": 40_000}
            )

        assert fake.await_args.kwargs["offset"] == 40_000
        # A later window is a fragment: it cannot ground a line number, so it is
        # deliberately not recorded as the file's contents.
        assert files_read == {}

    @pytest.mark.asyncio
    async def test_an_unparseable_offset_falls_back_to_the_start(self) -> None:
        executor, _errors, _successes, _files = _make_tool_executor(
            pat="pat", repo="org/repo", default_ref="main"
        )
        fake = AsyncMock(return_value=_FILE)
        with patch("app.services.github_tools.get_file_contents", new=fake):
            await executor(
                "get_file_contents", {"path": "src/auth.py", "offset": "start"}
            )

        assert fake.await_args.kwargs["offset"] == 0


# ---------------------------------------------------------------------------
# Access control judged from a component alone
# ---------------------------------------------------------------------------


class TestClaimsMissingAccessControl:
    @pytest.mark.parametrize(
        "justification",
        [
            "In SearchInterviewers.tsx there is no handling of an anonymous "
            "user's access to redirect them to the login screen.",
            "There is no conditional check or redirect when the user is not signed in.",
            "The page does not redirect unauthenticated visitors.",
            "The component lacks any authentication check.",
            "No route guard protects this screen.",
        ],
    )
    def test_an_assertion_that_access_control_is_absent_is_recognised(
        self, justification: str
    ) -> None:
        assert _claims_missing_access_control(justification) is True

    @pytest.mark.parametrize(
        "justification",
        [
            # Mentions auth, but as a finding about code that exists.
            "The middleware reads the bearer token and rejects the request otherwise.",
            "auth.ts defines signup, signin and a /me route guarded by authMiddleware.",
            # A negative finding about something that is not access control.
            "No cancel route exists; the router defines only accept and reject.",
            "The handler lowercases the query and matches name and bio.",
        ],
    )
    def test_sound_verdicts_mentioning_auth_are_not_flagged(
        self, justification: str
    ) -> None:
        # Matching these would downgrade correct verdicts, which is the same
        # class of error the guard exists to prevent, pointed the other way.
        assert _claims_missing_access_control(justification) is False


class TestReadRouteRegistration:
    @pytest.mark.parametrize(
        "path",
        [
            "frontend/src/App.tsx",
            "frontend/src/main.tsx",
            "src/router.tsx",
            "app/layout.tsx",
            "backend/app/urls.py",
            "src/routes/index.ts",
            # A route file whose own name says nothing — the directory does.
            "backend/user-service/src/routes/users.ts",
            "src/middleware/auth.ts",
            "app/guards/admin.guard.ts",
        ],
    )
    def test_route_registration_files_are_recognised(self, path: str) -> None:
        assert _read_route_registration({path: ""}) is True

    @pytest.mark.parametrize(
        "path",
        [
            "frontend/src/pages/SearchInterviewers.tsx",
            "frontend/src/components/Navbar.tsx",
            "frontend/src/hooks/useApi.ts",
            "src/services/payments.ts",
        ],
    )
    def test_ordinary_files_are_not_mistaken_for_a_router(self, path: str) -> None:
        assert _read_route_registration({path: ""}) is False

    def test_any_router_among_several_files_counts(self) -> None:
        known = {
            "frontend/src/pages/SearchInterviewers.tsx": "",
            "frontend/src/App.tsx": "",
        }
        assert _read_route_registration(known) is True


_PAGE = "frontend/src/pages/SearchInterviewers.tsx"


class TestAccessControlDowngrade:
    """The report's one surviving false positive, end to end.

    The agent read SearchInterviewers.tsx, found no auth check in it, and failed
    the scenario — while `/interviewers` was wrapped in a ProtectedRoute one file
    away in App.tsx. Reading a component and not finding a guard there does not
    establish that the route is unguarded.
    """

    @staticmethod
    def _verdict_dict(justification: str) -> dict:
        return {
            "scenario_id": "00000000-0000-0000-0000-000000000001",
            "scenario_title": "Anonymous user tries to access discovery screen",
            "status": "fail",
            "justification": justification,
            "code_reference": {"file": _PAGE},
            "github_links": [],
            "implementation_suggestion": "Add a useAuth check to the component.",
        }

    _REPORT_JUSTIFICATION = (
        "In the SearchInterviewers.tsx file, there is no handling of an anonymous "
        "user's access to the /interviewers page to redirect them to the login "
        "screen. There is no conditional check or redirect when the user is not "
        "signed in."
    )

    def test_a_component_only_access_control_fail_is_downgraded(self) -> None:
        verdict = _build_verdict(
            self._verdict_dict(self._REPORT_JUSTIFICATION),
            {"id": "00000000-0000-0000-0000-000000000001", "title": "Anonymous"},
            "org/repo",
            [],
            [True],
            False,
            {_PAGE: "export default function X(){}"},
        )
        assert verdict.status == "inconclusive"
        assert "route is registered" in verdict.implementation_suggestion

    def test_the_same_fail_stands_once_the_router_was_read(self) -> None:
        # Having read the router, "no guard" is a real finding, not a blind spot.
        verdict = _build_verdict(
            self._verdict_dict(self._REPORT_JUSTIFICATION),
            {"id": "00000000-0000-0000-0000-000000000001", "title": "Anonymous"},
            "org/repo",
            [],
            [True],
            False,
            {
                _PAGE: "export default function X(){}",
                "frontend/src/App.tsx": "<Route path='/x' element={<Page/>} />",
            },
        )
        assert verdict.status == "fail"

    def test_an_unrelated_fail_from_one_file_is_untouched(self) -> None:
        # The guard is narrow on purpose: a normal missing-feature verdict must
        # not be softened just because only one file was read.
        verdict = _build_verdict(
            self._verdict_dict(
                "No cancel route exists; the file defines only accept and reject."
            ),
            {"id": "00000000-0000-0000-0000-000000000001", "title": "Cancel"},
            "org/repo",
            [],
            [True],
            False,
            {"backend/src/bookings.ts": "router.patch('/accept')"},
        )
        assert verdict.status == "fail"
