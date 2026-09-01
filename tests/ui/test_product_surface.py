"""The product surface's HONESTY properties, enforced statically.

The Phase 8 screen is policed by `tests/test_ui_renders_only_audit_events.py`,
which reads `ui/static/app.js` and `index.html` by name. This file does the
same job for the product screen, plus the claims that are specific to it:
Test Mode must be visible, ₹1,000 must be what the page actually shows, the
synthetic simulation must be labelled synthetic, real and synthetic events
must not be merged, an MCP label must not be fabricated in the browser, and no
credential may reach the frontend.

Method, as in the Phase 8 file: every scan runs on CODE with comments
stripped, because `product.js` documents the very rules these tests police and
counting a docstring's mention of `ingestAuditEvents` would make the check
meaningless.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
HTML = REPO / "ui" / "static" / "product.html"
JS = REPO / "ui" / "static" / "product.js"
CSS = REPO / "ui" / "static" / "product.css"
ROUTES = REPO / "ui" / "routes_razorpay.py"
SESSION = REPO / "ui" / "razorpay_session.py"


def html() -> str:
    return HTML.read_text(encoding="utf-8")


def js() -> str:
    return JS.read_text(encoding="utf-8")


def js_code_only() -> str:
    text = re.sub(r"/\*.*?\*/", "", js(), flags=re.DOTALL)
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))


def html_code_only() -> str:
    return re.sub(r"<!--.*?-->", "", html(), flags=re.DOTALL)


# --- trace integrity, same rule as the system page --------------------------


def test_the_audit_store_has_exactly_one_writer():
    code = js_code_only()
    pushes = [m.start() for m in re.finditer(r"auditState\.events\.push\s*\(", code)]
    assert len(pushes) == 1, "auditState.events is written in " + str(len(pushes)) + " places"
    start = code.index("function ingestAuditEvents")
    end = code.index("function opportunity")
    assert start < pushes[0] < end


def test_the_audit_store_writer_is_called_only_from_the_sse_paths():
    code = js_code_only()
    sites = [m.start() for m in re.finditer(r"(?<!function )ingestAuditEvents\s*\(", code)]

    def enclosing(index: int) -> str:
        return code[:index].rsplit("function ", 1)[1].split("(")[0].strip()

    assert {enclosing(i) for i in sites} == {"onSseMessage", "backfillFrom"}, (
        "ingestAuditEvents is called from " + repr({enclosing(i) for i in sites})
    )


def test_no_control_endpoint_can_reach_the_audit_store():
    """`/state`, `/health`, `/session`, `/payment-link`, `/ingest`, `/verify`
    and `/reset` are INTEGRATION CONTROL responses. If any of them fed
    auditState, the page would have a second source of system truth."""
    code = js_code_only()
    boundaries = sorted(
        [m.start() for m in re.finditer(r"\nfunction \w+", code)]
        + [m.start() for m in re.finditer(r"\ndocument\.getElementById", code)]
        + [0, len(code)]
    )
    for endpoint in ("/state", "/health", "/session", "/payment-link", "/ingest",
                     "/verify", "/reset", "/provider-failure"):
        for match in re.finditer(re.escape(endpoint), code):
            start = max(b for b in boundaries if b <= match.start())
            end = min(b for b in boundaries if b > match.start())
            body = code[start:end]
            assert "ingestAuditEvents" not in body, endpoint + " feeds the audit store"
            assert "auditState.events" not in body, endpoint + " writes auditState.events"


def test_the_only_event_source_is_the_product_audit_stream():
    code = js_code_only()
    sources = re.findall(r"new EventSource\('([^']+)", code)
    assert sources == ["/api/integrations/razorpay/stream?after_seq="], sources
    for path in set(re.findall(r"fetch\('([^']+)", code)):
        assert path.startswith("/api/integrations/razorpay/"), path


def test_the_stream_reuses_the_one_audited_query():
    """The product routes must not carry a second SQL statement. The stream
    calls `ui.sse.event_stream`, whose single query names one table — so the
    Phase 8 trace-integrity test covers this page too."""
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    sql = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and re.search(r"\bSELECT\b", node.value) and re.search(r"\bFROM\b", node.value)
    ]
    assert sql == [], "ui/routes_razorpay.py contains SQL: " + repr(sql)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported.add(node.module + "." + alias.name)
    assert "ui.sse" in {n.rsplit(".", 1)[0] for n in imported} or "ui.sse" in imported


def test_the_routes_delegate_explanation_and_verification():
    imported = set()
    for node in ast.walk(ast.parse(ROUTES.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported.add(node.module + "." + alias.name)
    assert "sampark.audit.explain.format_explanation" in imported
    assert "sampark.audit.explain.explain_request" in imported
    assert "sampark.audit.chain.verify_chain" in imported


def test_control_state_is_separate_and_labelled_on_screen():
    code, markup = js_code_only(), html_code_only()
    assert "const auditState" in code and "const controlState" in code
    assert "chip-control" in markup and "integration control" in markup
    assert "renders that audit log and nothing else" in markup


# --- Test Mode is unmissable ------------------------------------------------


def test_test_mode_is_stated_in_the_header_and_the_banner():
    markup = html_code_only()
    assert "RAZORPAY TEST MODE" in markup
    assert "no real money moves" in markup
    assert "badge-test" in markup, "the Test Mode badge must be visually distinguished"


def test_the_page_never_claims_razorpay_uses_or_deployed_sampark():
    """The prohibited claims, checked literally against the rendered text."""
    markup = html_code_only().lower()
    for claim in (
        "razorpay uses sampark",
        "deployed in production",
        "in production at razorpay",
        "real money",
    ):
        if claim == "real money":
            # "no real money moves" is the honest form; a bare claim is not.
            assert "no real money moves" in markup
            continue
        assert claim not in markup, "the page makes a prohibited claim: " + claim
    assert "proposed" in markup, "the page must describe itself as a proposed integration"


def test_the_page_states_the_amount_and_reads_it_from_the_backend():
    """₹1,000 appears as the demo's subject, and the LIVE amount rendered in
    the integration panel comes from the backend rather than a second literal
    that could drift from `gateway.demo_amount_paise()`."""
    markup = html_code_only()
    assert "&#8377;1,000" in markup, "the 1,000 INR subject is not stated on the page"
    code = js_code_only()
    assert "t.amount_paise" in code, "the integration panel must read the amount from /health"
    assert not re.search(r"\b100000\b", code), "the amount is hard-coded in the frontend"


def test_the_amount_the_backend_reports_is_1000_inr_by_default(monkeypatch):
    from sampark.integrations import gateway

    monkeypatch.delenv("RAZORPAY_DEMO_AMOUNT_INR", raising=False)
    assert gateway.demo_amount_paise() == 100_000


# --- provenance is never fabricated in the browser --------------------------


def test_the_frontend_never_invents_a_transport_label():
    """`labelForTransport` only RENAMES a value it was given. The browser must
    not decide that something came from MCP."""
    code = js_code_only()
    assigns = re.findall(r"transport\s*=\s*'(\w+)'", code)
    assert assigns == [], "product.js assigns a transport literal: " + repr(assigns)
    assert "o.transport = p.transport" in code, (
        "the hero's transport must be copied from the audit payload"
    )


def test_the_transport_label_has_a_distinct_string_for_each_transport():
    code = js_code_only()
    for value, label in (("mcp", "Razorpay MCP Server"),
                         ("rest_api", "Razorpay Test API"),
                         ("webhook", "Razorpay webhook")):
        assert "'" + value + "'" in code and label in code
    assert code.index("Razorpay MCP Server") != code.index("Razorpay Test API"), (
        "MCP and the Test API must never share a label"
    )


def test_a_fallback_reason_is_surfaced_rather_than_hidden():
    code, markup = js_code_only(), html_code_only()
    assert "fallback_reason" in code
    assert "int-fallback" in markup
    assert "MCP was not used" in code or "MCP not used" in code


def test_the_page_explains_that_an_mcp_label_cannot_be_fabricated():
    markup = html_code_only()
    assert "cannot be shown unless" in markup or "cannot exist unless" in markup


# --- real vs synthetic are never merged -------------------------------------


def test_the_synthetic_simulation_is_labelled_synthetic():
    markup = html_code_only()
    assert "synthetic" in markup.lower()
    assert "not from Razorpay" in markup, (
        "the link to the system demo must say its data does not come from Razorpay"
    )


def test_the_product_page_never_streams_the_synthetic_demo():
    """The two demos have separate sessions and separate schemas. If the
    product page read `/api/stream`, a synthetic replay's events would appear
    beside real Razorpay ones with nothing distinguishing them."""
    code = js_code_only()
    assert "/api/stream" not in code.replace("/api/integrations/razorpay/stream", "")
    assert "/api/events" not in code.replace("/api/integrations/razorpay/events", "")
    assert "/api/chaos" not in code
    assert "/api/run" not in code


def test_the_two_surfaces_use_different_sessions():
    """Read off the source rather than by importing `ui.app`, which builds the
    application at module import and would make this whole file — otherwise a
    pure static scan — require a live PostgreSQL."""
    source = (REPO / "ui" / "app.py").read_text(encoding="utf-8")
    assert "app.state.session = DemoSession" in source
    assert "app.state.razorpay_session = RazorpayProductSession" in source
    assert 'STATIC_DIR / "product.html"' in source
    assert 'STATIC_DIR / "index.html"' in source


def test_the_phase_8_screen_is_byte_identical_and_only_its_route_moved():
    """Phase 8's `index.html`, `app.js` and `ui/sse.py` are what
    `tests/test_ui_renders_only_audit_events.py` reads BY NAME. The product
    layer moved that screen from `/` to `/system`; if it had also edited it,
    the Phase 8 invariants would be being asserted against changed files."""
    import subprocess

    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--",
         "ui/static/index.html", "ui/static/app.js", "ui/static/styles.css", "ui/sse.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert changed == [], "Phase 8 UI files were modified: " + repr(changed)


def test_the_committed_evidence_block_is_labelled_as_not_audit_derived():
    """The Phase 9 numbers cannot be audit-derived — they come from committed
    result files. Shown, and labelled, and never blended into the trace."""
    markup = html_code_only()
    assert "results/phase9_abh_table.json" in markup
    assert "not</b>\n        audit-derived" in markup or "not audit-derived" in markup.replace(
        "<b>not</b>", "not"
    )


def test_the_negative_finding_is_stated_on_the_page():
    """CLAUDE.md §14 and the task brief both require the unfavourable result
    to stay visible. It is the credibility of everything else."""
    markup = html_code_only()
    assert "recovered less total money" in markup
    assert "0.00" in markup and "ML model contribution" in markup
    assert "selection and allocation" in markup


def test_the_page_does_not_claim_the_ml_model_helped():
    markup = html_code_only().lower()
    assert "uplift model is honestly unavailable" in markup or "honestly unavailable" in markup
    for claim in ("machine learning improves", "our model improves recovery", "ai-powered ranking"):
        assert claim not in markup


# --- prioritisation semantics ------------------------------------------------


def test_the_page_denies_that_the_bigger_payment_simply_wins():
    markup = html_code_only()
    assert "not choosing the bigger payment" in markup
    assert "expected_net" in markup
    assert "fatigue cost" in markup
    assert "amount alone never decides" in markup


def test_the_expected_net_shown_is_read_off_the_audit_event():
    """The break-even PROSE is static. The per-opportunity number is not — it
    must come from the decision event's own payload, never be recomputed in
    the browser."""
    code = js_code_only()
    assert "p.expected_net_paise" in code
    assert "fatigue" not in code.lower(), (
        "product.js appears to compute a fatigue term; scoring belongs to the allocator"
    )


# --- no credentials, no second telemetry channel ----------------------------


BANNED_TOKENS = ("emit_demo_event", "new WebSocket", "socket.io")


@pytest.mark.parametrize("path", [HTML, JS, CSS])
def test_no_bespoke_telemetry_channel(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".js":
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))
    for token in BANNED_TOKENS:
        assert token not in text, str(path) + " uses " + token


SECRET_SHAPES = (
    re.compile(r"rzp_(test|live)_[A-Za-z0-9]{6,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|bearer)\b\s*[:=]\s*['\"][^'\"]{8,}"),
    re.compile(r"RAZORPAY_(KEY_SECRET|MCP_TOKEN|WEBHOOK_SECRET)\s*[:=]"),
)


@pytest.mark.parametrize("path", [HTML, JS, CSS])
def test_no_credential_of_any_shape_reaches_the_frontend(path):
    text = path.read_text(encoding="utf-8")
    for pattern in SECRET_SHAPES:
        assert not pattern.search(text), str(path) + " matches " + pattern.pattern


def test_the_api_never_returns_a_credential():
    """The session's own projections are checked structurally: no field name
    on the wire may look like a secret."""
    source = SESSION.read_text(encoding="utf-8") + ROUTES.read_text(encoding="utf-8")
    for banned in ("key_secret", "RAZORPAY_KEY_SECRET", "RAZORPAY_MCP_TOKEN",
                   "webhook_secret\":", "config.token", "_config.token"):
        assert banned not in source, "the API surface references " + banned


def test_health_reports_whether_the_webhook_is_configured_never_its_value():
    source = ROUTES.read_text(encoding="utf-8")
    assert "webhook.webhook_configured()" in source
    assert "webhook.webhook_secret()" not in source
