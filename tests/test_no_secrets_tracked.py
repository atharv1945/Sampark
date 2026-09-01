"""No credential is tracked by git — CLAUDE.md §8, enforced.

`.env` holds real values and is gitignored; `.env.example` is tracked and
holds NAMES with empty values only. The Razorpay product integration added
three more secret-shaped variables (`RAZORPAY_MCP_TOKEN`,
`RAZORPAY_WEBHOOK_SECRET`, and the existing `RAZORPAY_KEY_SECRET`), which is
exactly when a check like this earns its place.

Scanned files come from `git ls-files`, so this tests what would actually be
PUSHED — not what happens to be on disk. A working tree with an untracked
scratch file full of keys is fine; a tracked one is not.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# Value shapes, not names. A variable NAME like RAZORPAY_KEY_SECRET is
# expected in .env.example and in prose; a VALUE is never expected anywhere.
SECRET_VALUE_PATTERNS = [
    ("razorpay key id", re.compile(r"rzp_(test|live)_[A-Za-z0-9]{8,}")),
    ("bearer token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{20,}")),
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
]

# `NAME=<non-empty>` for a secret-shaped NAME, which is what an accidentally
# committed .env actually looks like.
# `[ 	]*` rather than `\s*`, deliberately: `\s` matches a newline, so a
# permissive version of this pattern reads `NAME=` on one line and matches the
# NEXT line's first token as its "value" — which made every empty entry in
# .env.example look like a leaked secret.
ASSIGNED_SECRET = re.compile(
    r"(?m)^[ 	]*(RAZORPAY_KEY_SECRET|RAZORPAY_MCP_TOKEN|RAZORPAY_WEBHOOK_SECRET|"
    r"ANTHROPIC_API_KEY|POSTGRES_PASSWORD)[ 	]*=[ 	]*(\S+)"
)

TEXT_SUFFIXES = {
    ".py", ".js", ".html", ".css", ".md", ".yml", ".yaml", ".json", ".sql",
    ".toml", ".txt", ".sh", ".example", ".cfg", ".ini", "",
}


def tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [REPO / name for name in out.split("\0") if name]


def scannable() -> list[pathlib.Path]:
    return [
        p for p in tracked_files()
        if p.suffix.lower() in TEXT_SUFFIXES and p.is_file() and p.stat().st_size < 2_000_000
    ]


def test_git_ls_files_actually_returned_something():
    """Guards the guard: a silently empty file list would make every
    assertion below vacuously true."""
    files = scannable()
    assert len(files) > 50, "only " + str(len(files)) + " tracked text files found"


def test_dot_env_is_not_tracked():
    names = {p.name for p in tracked_files()}
    assert ".env" not in names, ".env is tracked by git"
    assert ".env.example" in names, ".env.example should be tracked"


def test_dot_env_is_ignored_by_git():
    """Not merely absent — actively ignored, so a careless `git add .` cannot
    pick it up."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=REPO, capture_output=True
    )
    assert result.returncode == 0, ".env is not covered by .gitignore"


def test_the_tracked_env_template_holds_names_with_empty_values_only():
    template = (REPO / ".env.example").read_text(encoding="utf-8")
    assigned = ASSIGNED_SECRET.findall(template)
    assert assigned == [], ".env.example carries values: " + repr(assigned)
    # It must still DOCUMENT the names, including the ones this integration added.
    for name in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_MCP_TOKEN",
                 "RAZORPAY_WEBHOOK_SECRET", "RAZORPAY_DEMO_AMOUNT_INR"):
        assert name + "=" in template, name + " is not documented in .env.example"


@pytest.mark.parametrize("label,pattern", SECRET_VALUE_PATTERNS, ids=[p[0] for p in SECRET_VALUE_PATTERNS])
def test_no_tracked_file_contains_a_secret_shaped_value(label, pattern):
    offenders = []
    for path in scannable():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable tracked file
            continue
        match = pattern.search(text)
        if match:
            offenders.append(path.relative_to(REPO).as_posix() + ":" + match.group(0)[:12] + "...")
    assert offenders == [], "tracked file(s) contain a " + label + ": " + repr(offenders)


def test_no_tracked_file_assigns_a_value_to_a_secret_variable():
    offenders = []
    for path in scannable():
        if path.name == ".env.example":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, value in ASSIGNED_SECRET.findall(text):
            # A placeholder or a reference is fine; a value is not.
            if value.startswith(("<", "$", "{", "%", '"$', "'$")) or value in {'""', "''"}:
                continue
            offenders.append(path.relative_to(REPO).as_posix() + ":" + name)
    assert offenders == [], "secret assignment(s) in tracked files: " + repr(offenders)


def test_the_integration_reads_credentials_only_from_the_environment():
    """No credential may be read from a file, a constant, or a config object
    baked into the repository."""
    import ast

    package = REPO / "sampark" / "integrations"
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for _label, pattern in SECRET_VALUE_PATTERNS:
                assert not pattern.search(node.value), path.name + " embeds a credential"
        source = path.read_text(encoding="utf-8")
        if "TOKEN" in source or "SECRET" in source:
            # Either the module reads the environment itself, or it delegates
            # to a config object that does. `razorpay_rest.py` takes the second
            # route on purpose: `RazorpayConfig.from_env` is the ONE place that
            # decides a key is test-mode, and duplicating the read here would
            # duplicate that gate.
            assert any(marker in source for marker in ("os.environ", "getenv", "from_env")), (
                path.name + " references a credential but neither reads the environment "
                "nor delegates to a config that does"
            )
