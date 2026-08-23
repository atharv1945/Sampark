"""Phase 0: the interpreter running the suite must be Python 3.11.

This machine has Python 3.14 as the default `python` on PATH and also carries a
3.12 install. SAMPARK targets 3.11 (spec §7). This test turns that constraint
into something CI and the local venv both enforce, rather than a convention that
degrades silently the first time a shell resolves the wrong interpreter.
"""

import sys


EXPECTED = (3, 11)


def test_running_on_python_311() -> None:
    actual = sys.version_info[:2]
    assert actual == EXPECTED, (
        f"SAMPARK targets Python {EXPECTED[0]}.{EXPECTED[1]}, but this suite is "
        f"running on {actual[0]}.{actual[1]} "
        f"(interpreter: {sys.executable}). "
        "Activate the project .venv, or rebuild it with `py -3.11 -m venv .venv`."
    )
