"""Phase 0: the required top-level documents exist with exact uppercase names.

Casing matters here. The repo was created on a case-insensitive Windows
filesystem, and the submission is read from a case-sensitive one. A file
committed as `readme.md` renders as an untitled file on GitHub and breaks any
link to `README.md`.

`Path("README.md").exists()` is useless for this: on Windows it returns True for
`readme.md` as well. So the check compares against the literal directory listing
instead, which is case-exact on every platform.
"""

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_DOCS = (
    "README.md",
    "DECISIONS.md",
    "ARCHITECTURE.md",
    "DISCLAIMER.md",
    "CLAUDE.md",
)


def test_required_docs_exist_with_exact_casing() -> None:
    entries = set(os.listdir(REPO_ROOT))
    missing = [name for name in REQUIRED_DOCS if name not in entries]
    assert not missing, (
        f"Missing (or mis-cased) required document(s): {missing}. "
        f"Found in repo root: {sorted(e for e in entries if e.lower().endswith('.md'))}"
    )


def test_env_example_is_tracked_and_env_is_not_committed() -> None:
    """The secret-safety invariant: template present, real .env absent."""
    entries = set(os.listdir(REPO_ROOT))
    assert ".env.example" in entries, ".env.example template is missing."
    assert ".gitignore" in entries, ".gitignore is missing."

    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore, ".gitignore must ignore .env"
    assert "!.env.example" in gitignore, ".gitignore must re-include .env.example"
