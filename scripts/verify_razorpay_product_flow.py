"""One-shot manual verification of the Razorpay product integration.

The counterpart to `scripts/verify_razorpay_payment_link.py`, and deliberately
built the same way: it makes REAL calls to Razorpay's test environment, so it
is NOT a pytest test.

  - it is not under `tests/`, so pytest's `testpaths = ["tests"]` never
    collects it;
  - CI never invokes it (CLAUDE.md §15: "Do NOT make CI call Razorpay");
  - every action that writes anything requires an explicit flag, so nothing
    can fire by accident.

    .venv\\Scripts\\python.exe scripts\\verify_razorpay_product_flow.py
        --probe          read-only: what the MCP server offers, and whether
                         it is on the same TEST ledger as the rzp_test_ key
        --create-link    create ONE payment link at the demo amount
        --create-link --contrast
                         create ONE payment link at the contrast amount
        --status         read every link this account has, with attempt counts
        --decide LINK_ID run the failed payment on that link through SAMPARK
                         against a throwaway PostgreSQL schema, then drop it

Credentials come from the environment; if they are not already there, exactly
the Razorpay and PostgreSQL variables are read out of the repository's `.env`.
Nothing else in that file is parsed. A variable already set in the process
environment is never overwritten. No credential is ever printed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_DOTENV_KEYS = (
    "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET",
    "RAZORPAY_MCP_URL", "RAZORPAY_MCP_TOKEN", "RAZORPAY_MCP_SKIP_LEDGER_CHECK",
    "RAZORPAY_WEBHOOK_SECRET", "RAZORPAY_DEMO_AMOUNT_INR", "RAZORPAY_CONTRAST_AMOUNT_INR",
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
)


def _load_dotenv_defaults(dotenv_path: Path) -> None:
    """Fill exactly `_DOTENV_KEYS` from .env if not already set. Minimal and
    dependency-free — not a general loader. Never prints what it reads."""
    if not dotenv_path.is_file():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in _DOTENV_KEYS:
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _rupees(paise: int) -> str:
    return "Rs " + format(paise / 100, ",.2f")


def probe() -> int:
    from sampark.integrations import gateway

    status = gateway.transport_status()
    print("CONFIGURATION (no network call)")
    print("  MCP configured      : " + str(status["mcp_configured"]))
    print("  REST configured     : " + str(status["rest_configured"]) + "  (rzp_test_ only)")
    print("  preferred transport : " + status["preferred_transport"])
    print("  demo amount         : " + _rupees(status["amount_paise"]))
    print("  contrast amount     : " + _rupees(status["contrast_amount_paise"]))
    print()

    print("MCP PROBE (initialize + tools/list, read-only)")
    result = gateway.probe_mcp()
    if not result["reachable"]:
        print("  reachable : NO")
        print("  reason    : " + str(result["reason"]))
        print()
        print("  The REST test API will be used instead, and every transport label")
        print("  in the product surface will say `Razorpay Test API`.")
        return 0
    print("  reachable : YES")
    print("  server    : " + result["server"]["name"] + " " + result["server"]["version"])
    print("  tools     : " + str(len(result["tools"])) + " offered")
    print("  used here : " + ", ".join(result["tools_used_by_sampark"]))
    print()

    print("TEST-MODE CROSS-CHECK (read-only)")
    ok, reason = gateway.assert_same_test_ledger()
    print("  same test ledger : " + str(ok))
    print("  " + reason)
    if not ok:
        print()
        print("  MCP WRITES ARE WITHHELD until this passes. The product falls back to")
        print("  the rzp_test_ REST key, which is test-mode by construction.")
        print("  On a brand-new test account with no links to compare, set")
        print("  RAZORPAY_MCP_SKIP_LEDGER_CHECK=1 to proceed deliberately.")
    return 0


def create_link(contrast: bool) -> int:
    from sampark.integrations import gateway

    role = "contrast" if contrast else "headline"
    amount = gateway.contrast_amount_paise() if contrast else gateway.demo_amount_paise()
    print("Creating ONE " + role + " payment link for " + _rupees(amount) + " (Razorpay TEST mode)...")
    try:
        result = gateway.create_demo_payment_link(amount_paise=amount)
    except gateway.GatewayUnavailable as exc:
        print("FAILED: " + str(exc), file=sys.stderr)
        return 1

    payload = result.payload
    print()
    print("  transport      : " + result.transport.value + "  (" + _label(result.transport.value) + ")")
    if result.fallback_reason:
        print("  MCP not used   : " + result.fallback_reason)
    print("  payment_link_id: " + str(payload.get("id")))
    print("  short_url      : " + str(payload.get("short_url")))
    print("  status         : " + str(payload.get("status")))
    print("  amount         : " + _rupees(int(payload.get("amount", amount))))
    print("  reference_id   : " + str(payload.get("reference_id")))
    print()
    print("Open short_url and pay with a Razorpay TEST card that FAILS, then run:")
    print("  --decide " + str(payload.get("id")))
    return 0


def _label(transport: str) -> str:
    return {
        "mcp": "Razorpay MCP Server",
        "rest_api": "Razorpay Test API",
        "webhook": "Razorpay webhook",
    }.get(transport, transport)


def status() -> int:
    from sampark.integrations import gateway
    from sampark.integrations import razorpay_rest as rest
    from sampark.integrations.razorpay import RazorpayConfig

    link_ids = rest.list_payment_link_ids(RazorpayConfig.from_env())
    if not link_ids:
        print("No payment links on this test account yet.")
        return 0
    for link_id in link_ids:
        result = gateway.fetch_payment_link(link_id)
        payload = result.payload if isinstance(result.payload, dict) else {}
        attempts = [p for p in payload.get("payments") or [] if isinstance(p, dict)]
        failed = [a for a in attempts if str(a.get("status")) == "failed"]
        print(
            "%-24s %-10s %12s  attempts=%d failed=%d  (read via %s)"
            % (link_id, str(payload.get("status")), _rupees(int(payload.get("amount") or 0)),
               len(attempts), len(failed), result.transport.value)
        )
    return 0


def decide(payment_link_id: str, keep: bool) -> int:
    import psycopg

    from sampark.audit.chain import verify_chain
    from sampark.demo import isolation
    from sampark.demo.razorpay_product import RazorpayProductRun
    from sampark.integrations import gateway
    from sampark.integrations.normalize import normalize_payment
    from sim.persistence import PostgresConfig, PostgresConfigError

    print("Looking for a FAILED payment on " + payment_link_id + "...")
    lookup = gateway.find_failed_payment(payment_link_id)
    if lookup.payment is None:
        print()
        print("No failed payment attempt observed on this link yet.")
        print("  link status   : " + str(lookup.link_status))
        print("  attempts seen : " + str(lookup.attempts_seen))
        print()
        print("Open the link's short_url and pay with a Razorpay TEST card that fails.")
        print("Nothing is simulated here: Razorpay has to produce the failure.")
        return 1

    payment = lookup.payment
    print("  found " + str(payment.get("id")) + "  status=" + str(payment.get("status"))
          + "  error_code=" + str(payment.get("error_code"))
          + "  error_reason=" + str(payment.get("error_reason")))
    print("  matched by " + str(lookup.matcher) + ", read via " + lookup.provenance.transport.value)

    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        print("Postgres not configured: " + str(exc), file=sys.stderr)
        return 2
    try:
        conn = psycopg.connect(config.conninfo(), connect_timeout=5)
    except psycopg.OperationalError as exc:
        print("Postgres not reachable: " + str(exc), file=sys.stderr)
        return 2
    conn.autocommit = True

    before = isolation.public_audit_fingerprint(conn)
    schema = isolation.create_demo_schema(conn)
    exit_code = 0
    try:
        run = RazorpayProductRun(conn=conn, schema=schema)
        run.prepare(at=dt.datetime.now(dt.timezone.utc))
        opportunity = normalize_payment(payment, lookup.provenance, payment_link_id=payment_link_id)

        print()
        print("NORMALISED OPPORTUNITY")
        print("  risk_id     : " + opportunity.risk_id)
        print("  customer_id : " + opportunity.customer_id + "   (hash-derived; no raw contact detail)")
        print("  source      : " + opportunity.risk_item.source)
        print("  root_cause  : " + opportunity.root_cause + "   (from context code "
              + opportunity.context_code + ")")
        print("  amount      : " + _rupees(opportunity.amount_paise))

        outcome = run.ingest(opportunity)
        print()
        print("SAMPARK DECISION")
        print("  outcome     : " + outcome.outcome)
        print("  reason      : " + str(outcome.reason_code))
        print("  windows     : " + ", ".join(outcome.windows_evaluated))
        print("  grant       : " + str(outcome.grant_id))
        if outcome.delivery is not None:
            d = outcome.delivery
            print("  delivery    : channel=" + d.channel + " attempts=" + str(d.attempts)
                  + " delivered=" + str(d.delivered) + " rolled_back=" + str(d.rolled_back))

        with conn.cursor() as cur:
            cur.execute("SELECT event_type, reason_code FROM audit_events ORDER BY seq")
            print()
            print("AUDIT CHAIN (" + schema + ")")
            for event_type, reason_code in cur.fetchall():
                print("  %-26s %s" % (event_type, reason_code or "-"))

        report = verify_chain(conn)
        print()
        print("CHAIN VERIFICATION: " + ("VALID" if report.ok else "INVALID")
              + "  events=" + str(report.event_count)
              + "  genesis=" + str(report.genesis_ok) + "  linkage=" + str(report.linkage_ok))
        if not report.ok:
            exit_code = 1
    finally:
        if keep:
            print()
            print("demo schema KEPT: " + schema)
        else:
            isolation.drop_demo_schema(conn, schema)
            print()
            print("demo schema dropped")
        after = isolation.public_audit_fingerprint(conn)
        print("protected public.audit_events before/after: " + str(before) + " / " + str(after))
        if before != after:
            print("FATAL: the protected public audit chain CHANGED", file=sys.stderr)
            exit_code = 1
        conn.close()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="read-only capability + test-mode check")
    parser.add_argument("--create-link", action="store_true", help="create ONE test-mode payment link")
    parser.add_argument("--contrast", action="store_true", help="use the contrast amount for --create-link")
    parser.add_argument("--status", action="store_true", help="read every payment link and its attempts")
    parser.add_argument("--decide", metavar="LINK_ID", help="run that link's failed payment through SAMPARK")
    parser.add_argument("--keep", action="store_true", help="do not drop the demo schema after --decide")
    args = parser.parse_args(argv)

    if not (args.probe or args.create_link or args.status or args.decide):
        parser.print_help()
        print("\nRefusing to contact Razorpay without an explicit action.", file=sys.stderr)
        return 1

    _load_dotenv_defaults(REPO_ROOT / ".env")

    code = 0
    if args.probe:
        code = max(code, probe())
    if args.create_link:
        code = max(code, create_link(args.contrast))
    if args.status:
        code = max(code, status())
    if args.decide:
        code = max(code, decide(args.decide, args.keep))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
