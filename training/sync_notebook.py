"""Regenerate finetune_bdd.ipynb's training cell from finetune_bdd.py.

The notebook and the script must never drift: the script is what was debugged
across eight Kaggle attempts, and the notebook is what actually runs. Keeping
two copies in sync by hand is how they diverge silently.

    python training/sync_notebook.py           # rewrite the notebook
    python training/sync_notebook.py --check   # exit 1 if out of sync

`backend/tests/test_finetune_notebook.py` runs the --check logic in the normal
suite, so a stale notebook fails CI rather than a training run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TRAINING_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = TRAINING_DIR / "finetune_bdd.py"
NOTEBOOK_PATH = TRAINING_DIR / "finetune_bdd.ipynb"

# The notebook calls main() from its own final cell, so the script's CLI guard
# is the one thing dropped on the way in.
ENTRYPOINT_GUARD = '\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'


def script_as_cell_source() -> str:
    """The script body destined for the notebook's training cell."""
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    return text.replace(ENTRYPOINT_GUARD, "\n").rstrip() + "\n"


def notebook_cell_source(notebook: dict) -> str:
    """The training cell as it currently stands in the notebook."""
    code_cells = [c for c in notebook["cells"] if c["cell_type"] == "code"]
    if len(code_cells) < 2:
        raise SystemExit("Notebook lost its training cell — regenerate it.")
    return "".join(code_cells[1]["source"])


def sync(check_only: bool) -> int:
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    wanted = script_as_cell_source()

    if notebook_cell_source(notebook) == wanted:
        print("notebook is in sync with finetune_bdd.py")
        return 0

    if check_only:
        print(
            "OUT OF SYNC: finetune_bdd.ipynb does not match finetune_bdd.py.\n"
            "Run: python training/sync_notebook.py",
            file=sys.stderr,
        )
        return 1

    code_cells = [c for c in notebook["cells"] if c["cell_type"] == "code"]
    code_cells[1]["source"] = wanted.splitlines(keepends=True)
    NOTEBOOK_PATH.write_text(
        json.dumps(notebook, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"regenerated {NOTEBOOK_PATH.name} from {SCRIPT_PATH.name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Verify sync without writing"
    )
    return sync(parser.parse_args().check)


if __name__ == "__main__":
    raise SystemExit(main())
