"""One-shot manual verification: create ONE Razorpay test-mode Payment Link.

Phase 0 exit criterion (CLAUDE.md §15, spec §18.1): "a test-mode payment
link created from code, and CI passing." This script is the "from code"
half of that criterion. It is deliberately NOT a pytest test:

- it is not under tests/, so pytest's `testpaths = ["tests"]` never
  collects it, and it makes a real, non-deterministic external API call —
  the opposite of what belongs in the normal suite.
- CI never invokes it (CLAUDE.md §15: "Do NOT make CI call Razorpay").
- it only runs when passed --create, so it cannot fire by accident.

Credentials come from RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET
(sampark.integrations.razorpay.RazorpayConfig enforces this and rejects
anything that isn't an rzp_test_ key). If they are not already present in
the process environment, this script reads exactly those two variables out
of the repository's root .env before checking — nothing else in that file
is parsed or loaded. A variable already set in the process environment is
never overwritten by .env. Neither is ever printed or logged.

Usage:

    .venv\\Scripts\\python.exe scripts\\verify_razorpay_payment_link.py --create

Only non-secret response fields are printed: payment_link_id, short_url,
status, amount, currency. RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, and any
Authorization header are never printed or logged.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sampark.integrations.razorpay import (  # noqa: E402
    RazorpayConfigError,
    RazorpayConfig,
    RazorpayRequestError,
    build_client,
    create_test_payment_link,
)

TEST_AMOUNT_PAISE = 100  # INR 1.00 — small deterministic test value
DESCRIPTION = "SAMPARK Phase 0 test-mode payment link verification"

_DOTENV_KEYS = ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET")


def _load_dotenv_defaults(dotenv_path: Path) -> None:
    """Fill RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET from .env if not already set.

    Minimal, dependency-free parsing of exactly these two keys — not a
    general .env loader. Existing process-environment values always win.
    Never prints or logs what it reads.
    """
    if not dotenv_path.is_file():
        return

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in _DOTENV_KEYS:
            continue
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create",
        action="store_true",
        help="Actually call the Razorpay test API and create one payment link.",
    )
    args = parser.parse_args()

    if not args.create:
        parser.print_help()
        print("\nRefusing to call Razorpay without --create.", file=sys.stderr)
        return 1

    _load_dotenv_defaults(REPO_ROOT / ".env")

    try:
        config = RazorpayConfig.from_env()
    except RazorpayConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    client = build_client(config)
    reference_id = f"sampark-phase0-{uuid.uuid4()}"

    try:
        result = create_test_payment_link(
            client,
            amount_paise=TEST_AMOUNT_PAISE,
            description=DESCRIPTION,
            reference_id=reference_id,
        )
    except RazorpayRequestError as exc:
        print(f"Request error: {exc}", file=sys.stderr)
        return 1

    print("Payment link created successfully.")
    print(f"  payment_link_id: {result.payment_link_id}")
    print(f"  short_url:       {result.short_url}")
    print(f"  status:          {result.status}")
    print(f"  amount:          {result.amount}")
    print(f"  currency:        {result.currency}")
    print(f"  reference_id:    {reference_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
