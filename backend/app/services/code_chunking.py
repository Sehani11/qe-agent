"""Split source files into retrievable units for the code index.

``vector_service.chunk_text`` is prose-oriented: it splits on sentence
boundaries and packs 500-word blocks. Source code has almost no sentence
boundaries, so a whole file becomes one "sentence" and is cut into arbitrary
blobs — mid-function, decorators separated from the functions they decorate,
imports orphaned. That is why this module exists rather than reusing it.

A regex splitter is deliberate rather than a compromise on a parser. What the
index produces is a *pointer* to a file the agent will then read live, so the
chunker only has to be good enough to rank the right file first. A mis-split
chunk costs a little retrieval quality; it cannot reach a verdict, because
retrieved text is never the evidence a verdict rests on.
"""

import re

#: Hard cap on one chunk, in characters. Roughly 400 tokens: small enough that
#: a chunk is about one thing, large enough to hold a whole ordinary function.
_CHUNK_CHAR_CAP = 1_500

#: Definition starts, tried in order; group 1 is the symbol name.
#:
#: Leading whitespace is allowed, so methods split from their class. A class
#: with twenty methods would otherwise be one chunk far over the cap, and the
#: blank-line fallback would then cut it at arbitrary points anyway — splitting
#: on the definition is the same division made at a meaningful boundary.
_BOUNDARY_PATTERNS = (
    re.compile(r"^[ \t]*(?:async\s+)?def\s+(\w+)"),
    re.compile(r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)"),
    re.compile(r"^[ \t]*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"),
    re.compile(r"^[ \t]*(?:export\s+)?(?:interface|type|enum|struct|trait)\s+(\w+)"),
    re.compile(r"^[ \t]*func\s+(?:\([^)]*\)\s*)?(\w+)"),
    # An arrow-function assignment — how most React components and hooks are
    # written. The `=>` is required: matching every `const x = ...` would cut a
    # constants or config file into one chunk per line, which is noise in the
    # index and drowns out the files that matter.
    re.compile(
        r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+(\w+)"
        r"(?:\s*:[^=\n]+)?\s*=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*=>"
    ),
    # Java/C# member declarations, which have no keyword of their own.
    re.compile(
        r"^[ \t]*(?:public|private|protected|internal)\s+"
        r"(?:static\s+|final\s+|async\s+|virtual\s+|override\s+)*"
        r"[\w<>\[\],.?]+\s+(\w+)\s*\("
    ),
)

#: Lines that belong to the definition BELOW them, not the one above: Python
#: and TS decorators, and the comment block that documents what follows.
_ATTACHES_DOWNWARD = re.compile(r"^[ \t]*(?:@|#|//|/\*|\*)")


def _boundary_symbol(line: str) -> str | None:
    """The symbol a line defines, or None when it starts no definition."""
    for pattern in _BOUNDARY_PATTERNS:
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def _pull_up_preamble(lines: list[str], start: int, floor: int) -> int:
    """Move a boundary up over the decorators and comments introducing it.

    A chunk that begins at the ``def`` line and leaves ``@router.post(...)`` at
    the tail of the previous chunk describes neither: the route loses the path
    it is mounted at, and the function above it gains a decorator that is not
    its own. ``floor`` stops the walk from crossing into the previous chunk's
    real content.
    """
    index = start
    while index > floor and _ATTACHES_DOWNWARD.match(lines[index - 1]):
        index -= 1
    return index


def _split_oversized(text: str, first_line: int) -> list[tuple[str, int, int]]:
    """Break a too-large segment into (text, start_line, end_line) pieces.

    Blank lines first, since in code they usually separate coherent groups.
    Anything still over the cap after that — a minified bundle, a long data
    literal, a file with no blank lines at all — is cut at line boundaries,
    which is arbitrary but bounded. The alternative is emitting one enormous
    vector whose embedding means nothing in particular.
    """
    lines = text.split("\n")
    pieces: list[tuple[str, int, int]] = []
    current: list[str] = []
    current_start = first_line

    def _flush() -> None:
        nonlocal current, current_start
        if not current:
            return
        pieces.append(
            ("\n".join(current), current_start, current_start + len(current) - 1)
        )
        current_start += len(current)
        current = []

    for line in lines:
        # +1 for the newline this line would add when joined.
        projected = sum(len(item) + 1 for item in current) + len(line)
        if current and projected > _CHUNK_CHAR_CAP:
            # Prefer to break where the code already breaks. When the line that
            # tipped the cap is blank the break is free; otherwise flush anyway
            # rather than exceed the cap, since the cap is the guarantee.
            _flush()
        current.append(line)

    _flush()
    return pieces


def _segments(lines: list[str]) -> list[tuple[str, int, int, str]]:
    """Cut the file at definition boundaries.

    Returns (text, start_line, end_line, symbol) with 1-indexed inclusive line
    numbers. The span before the first definition — imports, module docstring,
    top-level constants — is kept as its own segment rather than dropped: for a
    file of pure configuration or constants it is the only segment there is.
    """
    boundaries: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        symbol = _boundary_symbol(line)
        if symbol is None:
            continue
        floor = boundaries[-1][0] + 1 if boundaries else 0
        start = _pull_up_preamble(lines, index, floor)
        # The pull-up can land on a line already claimed by the previous
        # boundary when definitions are stacked with no gap; keep the first.
        if boundaries and start <= boundaries[-1][0]:
            continue
        boundaries.append((start, symbol))

    if not boundaries:
        return [("\n".join(lines), 1, len(lines), "")]

    segments: list[tuple[str, int, int, str]] = []
    if boundaries[0][0] > 0:
        head = lines[: boundaries[0][0]]
        segments.append(("\n".join(head), 1, len(head), ""))

    for position, (start, symbol) in enumerate(boundaries):
        end = (
            boundaries[position + 1][0]
            if position + 1 < len(boundaries)
            else len(lines)
        )
        segments.append(("\n".join(lines[start:end]), start + 1, end, symbol))

    return segments


def path_header(path: str) -> str:
    """The line prefixed to a chunk's embedded text.

    Path tokens are a strong retrieval signal and this is the cheapest way to
    get them into the vector — a scenario mentioning "verification" should
    reach ``api/v1/verification.py`` even when the chunk body never says the
    word. It also puts the path in the stored metadata text, which is what the
    lexical re-rank in ``knowledge_service._query_matches_hybrid`` scans for
    exact identifiers.
    """
    return f"# {path}"


def chunk_code(text: str, path: str) -> list[dict]:
    """Split one source file into retrievable units.

    Returns dicts with:
      ``text``       what to embed and store — the code prefixed with its path
      ``code``       the raw source, unprefixed
      ``path``       the file's repository-relative path
      ``start_line`` 1-indexed, inclusive
      ``end_line``   1-indexed, inclusive
      ``symbol``     the definition this chunk starts at, or "" when it starts
                     at none (a file header, or an oversized body's remainder)

    Blank and whitespace-only files return no chunks: an empty vector is not a
    pointer to anything, and embedding one costs a request to say so.
    """
    if not text or not text.strip():
        return []

    lines = text.split("\n")
    header = path_header(path)
    chunks: list[dict] = []

    for segment_text, start_line, end_line, symbol in _segments(lines):
        if not segment_text.strip():
            continue

        if len(segment_text) <= _CHUNK_CHAR_CAP:
            pieces = [(segment_text, start_line, end_line)]
        else:
            pieces = _split_oversized(segment_text, start_line)

        for position, (piece, piece_start, piece_end) in enumerate(pieces):
            if not piece.strip():
                continue
            chunks.append(
                {
                    # Only the first piece of a split body still starts at the
                    # definition; labelling the rest with the same symbol would
                    # claim a line range that does not contain it.
                    "symbol": symbol if position == 0 else "",
                    "path": path,
                    "code": piece,
                    "text": f"{header}\n\n{piece}",
                    "start_line": piece_start,
                    "end_line": piece_end,
                }
            )

    return chunks
