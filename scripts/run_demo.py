"""`python scripts/run_demo.py` — start the SAMPARK demo with one command.

The application reads its configuration from the PROCESS ENVIRONMENT, not from
`.env` (`sim.persistence.PostgresConfig.from_env`,
`sampark.integrations.razorpay.RazorpayConfig.from_env`). That is deliberate —
nothing in `sampark/` or `ui/` should depend on a dotenv file existing — but it
means `uvicorn ui.app:app` on its own dies at import with a RuntimeError about
missing POSTGRES_* variables, which is a confusing first experience.

This launcher closes that gap for an operator: it reads exactly the variables
the demo needs out of the repository's `.env`, prints a preflight so you can see
what is and is not configured BEFORE the browser opens, and then runs uvicorn.

    python scripts/run_demo.py                 # preflight, then serve on :8000
    python scripts/run_demo.py --port 9000
    python scripts/run_demo.py --check         # preflight only, do not serve

No secret is ever printed. The preflight reports whether a variable is SET, and
whether a server answered — never a value.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Exactly the variables the demo reads. Nothing else in `.env` is touched.
DOTENV_KEYS = (
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
    "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET",
    "RAZORPAY_MCP_URL", "RAZORPAY_MCP_TOKEN", "RAZORPAY_MCP_SKIP_LEDGER_CHECK",
    "RAZORPAY_WEBHOOK_SECRET",
    "RAZORPAY_DEMO_AMOUNT_INR", "RAZORPAY_CONTRAST_AMOUNT_INR",
)

OK = "  ok   "
WARN = " warn  "
FAIL = " FAIL  "


def load_dotenv(path: Path) -> int:
    """Fill DOTENV_KEYS from `.env` if not already set. A variable already in
    the process environment always wins. Never prints what it reads."""
    if not path.is_file():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in DOTENV_KEYS and key not in os.environ:
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ[key] = value
                loaded += 1
    return loaded


def preflight() -> bool:
    """Print what is configured and what answered. Returns False only if the
    demo genuinely cannot start."""
    ready = True

    print("=" * 72)
    print("SAMPARK demo preflight")
    print("=" * 72)

    # --- PostgreSQL: required ---------------------------------------------
    try:
        import psycopg

        from sim.persistence import PostgresConfig, PostgresConfigError

        try:
            config = PostgresConfig.from_env()
        except PostgresConfigError as exc:
            print(FAIL + "PostgreSQL   " + str(exc))
            print("             Fix: fill POSTGRES_* in .env, then `docker compose up -d`.")
            return False
        try:
            conn = psycopg.connect(config.conninfo(), connect_timeout=5)
        except psycopg.OperationalError:
            print(FAIL + "PostgreSQL   configured, but not reachable on "
                  + config.host + ":" + str(config.port))
            print("             Fix: `docker compose up -d`, wait for healthy, retry.")
            return False
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.audit_events')")
            has_schema = cur.fetchone()[0] is not None
            cur.execute("SELECT count(*) FROM public.audit_events" if has_schema else "SELECT 0")
            events = cur.fetchone()[0]
        conn.close()
        if not has_schema:
            print(FAIL + "PostgreSQL   reachable, but the schema is not applied")
            print("             Fix: psql \"$DATABASE_URL\" -f sampark/schema.sql")
            return False
        print(OK + "PostgreSQL   reachable · protected chain has " + str(events) + " events")
    except ImportError as exc:  # pragma: no cover - environment failure
        print(FAIL + "PostgreSQL   " + str(exc))
        return False

    # --- Razorpay REST: required for /live --------------------------------
    from sampark.integrations import gateway
    from sampark.integrations import razorpay_rest as rest

    if rest.rest_configured():
        print(OK + "Razorpay     rzp_test_ key present (test mode enforced in code)")
    else:
        ready = False
        print(WARN + "Razorpay     RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET missing or not rzp_test_")
        print("             /live cannot create payment links. / and /system still work.")

    # --- Razorpay MCP: optional, preferred ---------------------------------
    status = gateway.transport_status()
    if status["mcp_configured"]:
        probe = gateway.probe_mcp()
        if probe["reachable"]:
            print(OK + "Razorpay MCP " + probe["server"]["name"] + " " + probe["server"]["version"]
                  + " · " + str(len(probe["tools"])) + " tools offered")
            ok, _reason = gateway.assert_same_test_ledger()
            print((OK if ok else WARN) + "Test-mode    cross-check "
                  + ("verified — MCP is on the same test ledger as the rzp_test_ key"
                     if ok else "NOT verified — MCP writes will be withheld, REST used instead"))
        else:
            print(WARN + "Razorpay MCP configured but unreachable — the REST test API will be")
            print("             used instead, and every label on screen will say so.")
    else:
        print(WARN + "Razorpay MCP RAZORPAY_MCP_TOKEN not set — the REST test API will be used,")
        print("             and every transport label will read 'Razorpay Test API'.")

    # --- webhook: optional --------------------------------------------------
    from sampark.integrations import webhook

    print((OK if webhook.webhook_configured() else WARN)
          + "Webhook      " + ("secret set — POST /webhook will verify signatures"
                               if webhook.webhook_configured()
                               else "RAZORPAY_WEBHOOK_SECRET not set; the webhook route refuses"))

    # --- amounts ------------------------------------------------------------
    print(OK + "Amounts      headline Rs " + format(status["amount_paise"] / 100, ",.0f")
          + " · contrast Rs " + format(status["contrast_amount_paise"] / 100, ",.0f"))
    print("=" * 72)
    return ready


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="8000")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--check", action="store_true", help="preflight only; do not serve")
    args = parser.parse_args(argv)

    loaded = load_dotenv(REPO_ROOT / ".env")
    if loaded:
        print("loaded " + str(loaded) + " setting(s) from .env\n")

    ready = preflight()
    if not ready and args.check:
        return 1
    if args.check:
        return 0
    if not ready:
        print()
        print("Starting anyway — the pages above that do work will work.")

    base = "http://" + args.host + ":" + args.port
    print()
    print("  Overview            " + base + "/")
    print("  Live Razorpay Test  " + base + "/live")
    print("  System Simulation   " + base + "/system")
    print()
    print("Ctrl-C to stop.")
    print()

    return subprocess.call(
        [sys.executable, "-m", "uvicorn", "ui.app:app",
         "--host", args.host, "--port", args.port],
        cwd=str(REPO_ROOT),
    )


if __name__ == "__main__":
    raise SystemExit(main())
