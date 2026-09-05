"""Tests for the read window on GitHub file contents.

Silent truncation is the failure these cover. A reader handed the first N
characters of a file cannot tell a feature that is absent from one that is
merely below the cut, so it reports the second as the first — which is how a
verification run comes to call working code unimplemented. The window must
therefore either return the whole file, or say plainly that it did not and
where to resume.
"""

from app.services.github_tools import _FILE_CONTENT_CAP, _window


def _body(window: str) -> str:
    """Strip the window's annotations, leaving only file content."""
    body = window.split("\n\n[TRUNCATED")[0]
    if body.startswith("[CONTINUED"):
        body = body.split("]\n\n", 1)[1]
    return body


class TestWindowFitsWholeFile:
    def test_small_file_is_returned_byte_for_byte(self) -> None:
        content = "export default function Page() {}\n"
        assert _window(content, 0) == content

    def test_file_at_exactly_the_cap_is_not_annotated(self) -> None:
        content = "a" * _FILE_CONTENT_CAP
        assert _window(content, 0) == content

    def test_empty_file_stays_empty(self) -> None:
        assert _window("", 0) == ""


class TestWindowTruncates:
    def test_oversized_file_announces_truncation_and_the_next_offset(self) -> None:
        content = "b" * (_FILE_CONTENT_CAP + 1_000)
        result = _window(content, 0)

        assert result.startswith("b" * _FILE_CONTENT_CAP)
        assert "TRUNCATED" in result
        assert "NOT the whole" in result
        assert f"offset={_FILE_CONTENT_CAP}" in result
        assert str(len(content)) in result

    def test_offset_reads_the_next_window_and_marks_it_continued(self) -> None:
        content = "c" * (_FILE_CONTENT_CAP + 10)
        result = _window(content, _FILE_CONTENT_CAP)

        assert "CONTINUED" in result
        assert result.endswith("c" * 10)
        # This window reaches the end, so nothing further is promised.
        assert "TRUNCATED" not in result

    def test_offset_past_the_end_says_so_rather_than_returning_nothing(self) -> None:
        # Silence here would read as "this file is empty", which is the same
        # false signal the cap itself used to send.
        result = _window("short file", 5_000)
        assert "EMPTY WINDOW" in result
        assert "read in full" in result

    def test_negative_offset_is_treated_as_the_start(self) -> None:
        assert _window("hello", -20) == "hello"


class TestWindowPagination:
    def test_a_long_file_can_be_read_completely_across_windows(self) -> None:
        content = "".join(str(i % 10) for i in range(_FILE_CONTENT_CAP * 2 + 37))

        first = _window(content, 0)
        second = _window(content, _FILE_CONTENT_CAP)
        third = _window(content, _FILE_CONTENT_CAP * 2)

        assert "TRUNCATED" in first
        assert "TRUNCATED" in second
        assert "TRUNCATED" not in third

        # The three windows must rebuild the original exactly: pagination that
        # drops or repeats characters would be worse than the truncation it
        # replaces, because nothing downstream could detect it.
        assert _body(first) + _body(second) + _body(third) == content
