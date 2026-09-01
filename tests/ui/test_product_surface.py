"""The product surfaces' HONESTY properties, enforced statically.

The Phase 8 screen is policed by `tests/test_ui_renders_only_audit_events.py`,
which reads `ui/static/app.js` and `index.html` by name. This file does the
same job for the two NEW surfaces — `/` (overview) and `/live` (the Razorpay
test) — plus the claims specific to them: Test Mode must be visible, 1,000 INR
must be what the page actually shows, the synthetic simulation must be labelled
synthetic, real and synthetic events must not be merged, an MCP label must not
be fabricated in the browser, the prohibited claims must appear nowhere, the
unfavourable Phase 9 findings must stay on screen, and no credential may reach
the frontend.

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
STATIC = REPO / "ui" / "static"
HTML = STATIC / "live.html"
JS = STATIC / "live.js"
CSS = STATIC / "live.css"
OVERVIEW_HTML = STATIC / "overview.html"
OVERVIEW_JS = STATIC / "overview.js"
NAVBAR_CSS = STATIC / "navbar.css"
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


def overview_code_only() -> str:
    return re.sub(r"<!--.*?-->", "", OVERVIEW_HTML.read_text(encoding="utf-8"), flags=re.DOTALL)


def overview_js_code_only() -> str:
    text = re.sub(r"/\*.*?\*/", "", OVERVIEW_JS.read_text(encoding="utf-8"), flags=re.DOTALL)
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))


# --- trace integrity, same rule as the system page --------------------------


def test_the_audit_store_has_exactly_one_writer():
    code = js_code_only()
    pushes = [m.start() for m in re.finditer(r"auditState\.events\.push\s*\(", code)]
    assert len(pushes) == 1, "auditState.events is written in " + str(len(pushes)) + " places"
    start = code.index("function ingestAuditEvents")
    end = code.index("function tally")
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
    """The two stores stay separate in the code, and the regions that render
    control state are marked as such on screen."""
    code, markup = js_code_only(), html_code_only()
    assert "const auditState" in code and "const controlState" in code
    assert "operator control" in markup, "the control region is not labelled"
    assert "tag-arch" in markup or "tag-none" in markup
    assert "written to a hash-chained audit log" in markup


# --- Test Mode is unmissable ------------------------------------------------


def test_test_mode_is_stated_in_the_header_and_the_banner():
    for markup in (html_code_only(), overview_code_only()):
        assert "RAZORPAY TEST MODE" in markup
        assert "no real money moves" in markup.lower()
        assert "sk-badge-test" in markup, "the Test Mode badge must be visually distinguished"


def test_no_page_claims_razorpay_uses_or_deployed_sampark():
    """The prohibited claims, checked literally against the rendered text of
    every page a judge can reach."""
    for markup in (html_code_only().lower(), overview_code_only().lower()):
        _no_prohibited_claims(markup)


def _no_prohibited_claims(markup: str) -> None:
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
    assert "link.amount_paise" in code, (
        "the live amount must be read from the backend, not from a second frontend literal"
    )
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
    assert "mcp-fallback" in markup
    assert "MCP was not used" in code or "MCP not used" in code


def test_the_page_explains_that_an_mcp_label_cannot_be_fabricated():
    markup = html_code_only()
    assert any(phrase in markup for phrase in
               ("cannot be shown unless", "cannot exist unless", "cannot appear unless"))


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
    assert 'STATIC_DIR / "live.html"' in source
    assert 'STATIC_DIR / "index.html"' in source


def test_the_committed_evidence_block_is_sourced_and_not_audit_derived():
    """The Phase 9 numbers cannot be audit-derived — they come from committed
    result files. Shown, sourced, and never blended into a live trace. They
    live on the OVERVIEW page, which renders no system state at all."""
    markup = overview_code_only()
    assert "results/phase9_abh_table.json" in markup
    assert "results/phase9_sensitivity.json" in markup
    assert "committed evidence" in markup


def test_the_negative_findings_are_stated_on_the_overview():
    """CLAUDE.md §14 and the brief both require the unfavourable results to
    stay visible. They are the credibility of everything else."""
    markup = overview_code_only()
    assert "recovered less total money" in markup
    assert "&minus;8.2 %" in markup
    assert "0.00 %" in markup
    assert "did not improve" in markup
    assert "selection and allocation" in markup


def test_no_page_claims_the_ml_model_helped():
    for markup in (overview_code_only().lower(), html_code_only().lower()):
        for claim in ("machine learning improves", "model improves recovery",
                      "ai-powered ranking", "ml-driven uplift"):
            assert claim not in markup
    assert "honestly unavailable" in overview_code_only().lower()


def test_no_page_claims_more_revenue():
    """The single most tempting false claim in the project."""
    for markup in (overview_code_only().lower(), html_code_only().lower()):
        for claim in ("recovers more revenue", "recovers more total revenue",
                      "increases revenue", "more money recovered", "boosts revenue"):
            assert claim not in markup, "a page claims more revenue: " + claim
    assert "efficiency" in overview_code_only().lower()


# --- prioritisation semantics ------------------------------------------------


def test_the_pages_deny_that_the_bigger_payment_simply_wins():
    markup = overview_code_only()
    assert "not picking the bigger payment" in markup
    assert "expected" in markup and "net" in markup
    assert "fatigue" in markup
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


# ================== the three-page product structure ==================


def test_all_three_pages_exist_and_are_routed():
    for path in (OVERVIEW_HTML, HTML, STATIC / "index.html", NAVBAR_CSS):
        assert path.is_file(), str(path) + " is missing"
    source = (REPO / "ui" / "app.py").read_text(encoding="utf-8")
    for target in ('STATIC_DIR / "overview.html"', 'STATIC_DIR / "live.html"',
                   'STATIC_DIR / "index.html"'):
        assert target in source, "no route serves " + target
    assert '@app.get("/live"' in source


def test_every_page_carries_the_same_navigation_with_one_active_link():
    for path, active in ((OVERVIEW_HTML, "/"), (HTML, "/live"), (STATIC / "index.html", "/system")):
        markup = path.read_text(encoding="utf-8")
        assert 'class="sk-nav"' in markup, str(path) + " has no shared nav"
        for href in ("/", "/live", "/system"):
            assert 'href="' + href + '"' in markup, str(path) + " does not link " + href
        current = re.findall(r'<a href="([^"]+)" aria-current="page"', markup)
        assert current == [active], str(path) + " active link is " + repr(current)


def test_the_navbar_stylesheet_cannot_disturb_the_phase_8_page():
    """`/system` loads Phase 8's own `styles.css`, which declares `:root`
    variables with the SAME names as the product design system and a
    fixed-height flex `body`. The shared navbar must therefore declare no
    `:root` block and no element selectors at all — only `.sk-*` classes."""
    css = re.sub(r"/\*.*?\*/", "", NAVBAR_CSS.read_text(encoding="utf-8"), flags=re.DOTALL)
    assert ":root" not in css, "navbar.css declares :root variables"
    # `[^{}]*` cannot cross a brace, so each match is exactly one selector list
    # or one at-rule prelude — never a declaration body.
    for prelude in re.findall(r"([^{}]*)\{", css):
        prelude = prelude.strip()
        if not prelude or prelude.startswith("@"):
            continue
        for part in prelude.split(","):
            part = part.strip()
            if part:
                assert part.startswith(".sk-"), "navbar.css has a non-.sk- selector: " + repr(part)


def test_the_phase_8_page_loads_no_product_stylesheet():
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "styles.css" in markup
    assert "navbar.css" in markup
    for forbidden in ("shared.css", "live.css", "overview.css"):
        assert forbidden not in markup, "index.html loads " + forbidden


def test_only_the_navigation_was_added_to_the_phase_8_page():
    """Phase 8's screen moved route, not content. `app.js`, `styles.css` and
    `ui/sse.py` — the files that actually carry the trace-integrity guarantee —
    must be byte-identical, and `index.html` must differ ONLY by the nav."""
    import subprocess

    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--",
         "ui/static/app.js", "ui/static/styles.css", "ui/sse.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert changed == [], "Phase 8 trace files were modified: " + repr(changed)

    diff = subprocess.run(
        ["git", "diff", "-U0", "HEAD", "--", "ui/static/index.html"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    removed = [l for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    assert removed == [], "index.html had lines REMOVED, not just the nav added: " + repr(removed)
    added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    assert added, "no nav was added to index.html"
    joined = "\n".join(added)
    assert "sk-nav" in joined and "navbar.css" in joined
    for token in ("<script", "auditState", "EventSource", "fetch("):
        assert token not in joined, "the index.html addition contains " + token


# ========================= the overview page =========================


def test_the_overview_renders_no_system_state():
    """It is a marketing page. If it fetched anything it could appear to be
    showing live decisions, which is exactly what the trace-integrity rule
    forbids — the audit log is the only source of decision data."""
    code = overview_js_code_only()
    for forbidden in ("fetch(", "EventSource", "XMLHttpRequest", "auditState", "WebSocket"):
        assert forbidden not in code, "overview.js uses " + forbidden


def test_the_overview_states_the_problem_and_the_two_cases():
    markup = overview_code_only()
    assert "&#8377;1,000" in markup and "&#8377;4,000" in markup
    assert "DENIED" in markup and "GRANTED" in markup
    assert "allocation.negative_expected_net" in markup
    assert "&minus;&#8377;267.74" in markup, "the expected-net figure is not shown"
    assert "1,978" in markup, "the break-even figure is not shown"


def test_the_overview_says_the_threshold_was_not_tuned():
    assert "not adjusted to make the demo look better" in overview_code_only()


def test_the_overview_positions_razorpay_correctly():
    markup = overview_code_only()
    assert "could" in markup and "integrate" in markup
    for claim in ("Razorpay lacks", "Razorpay has no", "Razorpay does not have"):
        assert claim not in markup, "the overview disparages Razorpay's own systems: " + claim
    assert "not replace" in markup


def test_every_page_uses_the_same_four_provenance_labels():
    """The categories must be spelled identically everywhere, or a viewer
    cannot rely on them."""
    for markup in (overview_code_only(), html_code_only()):
        for label in ("Live &middot; Razorpay MCP", "Live &middot; SAMPARK",
                      "Simulated", "Architectural capability"):
            assert label.lower() in markup.lower(), "missing label: " + label
    css = (STATIC / "shared.css").read_text(encoding="utf-8")
    for cls in ("tag-live-rzp", "tag-live-sam", "tag-sim", "tag-arch", "tag-none"):
        assert "." + cls in css, "shared.css has no style for " + cls


def test_the_live_page_labels_the_webhook_as_capability_not_demonstrated():
    """The receiver is tested against genuine signatures, but Razorpay has
    never delivered to it. That distinction must survive on screen."""
    markup = html_code_only()
    assert "Architectural capability" in markup
    assert "never delivered" in markup
    assert "Not demonstrated" in markup


def test_the_live_page_opens_the_real_razorpay_checkout_and_imitates_nothing():
    markup, code = html_code_only(), js_code_only()
    assert "Open Razorpay Test Checkout" in markup
    assert "link.short_url" in code, "the checkout button must use Razorpay's own short_url"
    assert 'target="_blank"' in markup
    for imitation in ("razorpay-checkout", "fake-checkout", "mock-checkout"):
        assert imitation not in markup.lower()
