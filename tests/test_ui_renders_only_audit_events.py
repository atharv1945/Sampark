"""Spec §12.1's trace-integrity rule, ENFORCED rather than asserted.

Spec §19 names this file explicitly: "worth writing purely so the
trace-integrity rule is enforced rather than merely asserted."

    "The UI renders the audit log and nothing else. No emit_demo_event(), no
     parallel websocket telemetry, no component reporting its own progress to
     the frontend. ... An instrumented visualization is a SECOND CODE PATH,
     which means the demo can be correct while the system is broken."

A test that merely checked "a render function was called" would prove
nothing. These attack the invariant from four directions:

  1. STRUCTURAL (backend) — the SSE module's only query names only
     `audit_events`, and imports no decision machinery.
  2. STRUCTURAL (frontend) — the audit store has exactly ONE writer, reachable
     only from the SSE handler and the SSE gap-repair fetch. Every other
     endpoint response is parsed by different code into different state.
  3. ABSENCE — the forbidden second channel exists nowhere in the repository.
  4. ADVERSARIAL (live) — every event the API serves corresponds to a real
     row in `audit_events`, and a fabricated fact pushed at the API is
     refused and never enters the chain.

NOTE on method: `ui/static/app.js` documents this rule at length, naming the
very functions these tests police. Every scan below therefore runs on CODE
with comments stripped — counting a docstring's mention of
`ingestAuditEvents` as a call site would make the check meaningless.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
APP_JS = REPO / "ui" / "static" / "app.js"
INDEX_HTML = REPO / "ui" / "static" / "index.html"
SSE_PY = REPO / "ui" / "sse.py"
ROUTES_PY = REPO / "ui" / "routes.py"

# Every table in sampark/schema.sql EXCEPT audit_events. The SSE query may
# name none of them.
OTHER_TABLES = (
    "agents", "capability_scopes", "customers", "contact_states", "risk_items",
    "grant_requests", "grants", "merchants", "budget_windows",
    "customer_margin_windows", "contact_slot_claims",
)


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _js_code_only() -> str:
    """`app.js` with /* */ blocks and // lines stripped."""
    text = re.sub(r"/\*.*?\*/", "", _js(), flags=re.DOTALL)
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))


# ---------------------------------------------------------------- 1. backend


def test_the_sse_query_names_audit_events_and_nothing_else():
    """If a future change enriches the stream from `grants` or `agents`, the
    UI would render a joined view rather than the log. This fails first.

    Scans only genuine SQL (a literal containing both SELECT and FROM), so
    prose naming a table is not mistaken for a query; and asserts the named
    constant directly, so the check cannot be dodged by relocating the SQL.
    """
    from ui import sse

    tree = ast.parse(SSE_PY.read_text(encoding="utf-8"))
    sql_literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(r"\bSELECT\b", node.value)
        and re.search(r"\bFROM\b", node.value)
    ]
    assert sse.EVENTS_SQL in sql_literals, "EVENTS_SQL is not a literal in ui/sse.py"
    assert len(sql_literals) == 1, "ui/sse.py has more than one query: " + repr(sql_literals)

    sql = sql_literals[0]
    assert "audit_events" in sql
    for table in OTHER_TABLES:
        assert not re.search(r"\b" + table + r"\b", sql), (
            "ui/sse.py's SQL touches " + table + ": the trace would no longer be the log alone"
        )


def test_the_sse_module_never_imports_decision_machinery():
    """The stream is a pure read path. It may not compute a verdict, a score,
    an allocation or a budget number to enrich a frame with."""
    tree = ast.parse(SSE_PY.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = [
        m for m in imported
        if m.startswith((
            "sampark.allocator", "sampark.policy", "sampark.mediation",
            "sampark.budget", "sampark.demo", "sampark.registry",
        ))
    ]
    assert forbidden == [], "ui/sse.py imports decision machinery: " + repr(forbidden)


def test_routes_delegate_explanation_export_and_verification():
    """Reuse `sampark.audit.*`; never build a second engine that could
    disagree with the log."""
    tree = ast.parse(ROUTES_PY.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported.add(node.module + "." + alias.name)
    assert "sampark.audit.explain.format_explanation" in imported
    assert "sampark.audit.explain.explain_request" in imported
    assert "sampark.audit.chain.verify_chain" in imported


# --------------------------------------------------------------- 2. frontend


def test_the_audit_store_has_exactly_one_writer():
    """`ingestAuditEvents` must be the sole function that pushes into
    `auditState.events`; anything else would be a second, unaudited source of
    system truth."""
    js = _js_code_only()
    pushes = [m.start() for m in re.finditer(r"auditState\.events\.push\s*\(", js)]
    assert len(pushes) == 1, "auditState.events is written in " + str(len(pushes)) + " places"

    start = js.index("function ingestAuditEvents")
    end = js.index("function tally")
    assert start < pushes[0] < end, "the only write to auditState.events is outside ingestAuditEvents"


def test_the_audit_store_writer_is_called_only_from_the_sse_paths():
    """Two callers, and both carry nothing but rows out of `audit_events`:
    the /api/stream SSE handler, and the /api/events gap repair."""
    js = _js_code_only()
    call_sites = [m.start() for m in re.finditer(r"(?<!function )ingestAuditEvents\s*\(", js)]
    assert len(call_sites) == 3, (
        "expected 3 call sites (onSseMessage, backfillFrom x2), found " + str(len(call_sites))
    )

    def enclosing(index: int) -> str:
        return js[:index].rsplit("function ", 1)[1].split("(")[0].strip()

    assert {enclosing(i) for i in call_sites} == {"onSseMessage", "backfillFrom"}


def test_no_other_api_response_can_reach_the_audit_store():
    """/api/status, /api/chaos, /api/run, /api/verify and /api/reset must be
    parsed into controlState or the DOM — never into auditState."""
    js = _js_code_only()
    boundaries = sorted(
        [m.start() for m in re.finditer(r"\nfunction \w+", js)]
        + [m.start() for m in re.finditer(r"\ndocument\.getElementById", js)]
        + [0, len(js)]
    )
    for endpoint in ("/api/status", "/api/chaos", "/api/run", "/api/verify", "/api/reset"):
        for match in re.finditer(re.escape(endpoint), js):
            start = max(b for b in boundaries if b <= match.start())
            end = min(b for b in boundaries if b > match.start())
            body = js[start:end]
            assert "ingestAuditEvents" not in body, (
                endpoint + " feeds the audit store - that is a second source of system truth"
            )
            assert "auditState.events" not in body, endpoint + " writes auditState.events"


def test_the_only_event_source_is_the_audit_stream():
    js = _js_code_only()
    sources = re.findall(r"new EventSource\('([^']+)", js)
    assert sources == ["/api/stream?after_seq="], sources
    for path in set(re.findall(r"fetch\('([^']+)", js)):
        assert path.startswith("/api/"), path


def test_demo_control_state_is_separate_and_labelled_on_screen():
    js, html = _js_code_only(), INDEX_HTML.read_text(encoding="utf-8")
    assert "const auditState" in js and "const controlState" in js
    assert "chip-control" in html and "demo control" in html
    # The rule is stated to the viewer, not only in the README.
    assert "renders the hash-chained audit log and nothing else" in html


def test_the_arm_a_reference_row_is_labelled_as_not_audit_derived():
    """Arm A has no audit log, so its numbers cannot be audit-derived. Spec
    §12.2 wants the side-by-side, so they are shown — and labelled, and never
    blended into the live trace."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "NOT audit-derived" in html
    assert "results/gate_headline.json" in html


# ---------------------------------------------------------------- 3. absence


BANNED_TOKENS = ("emit_demo_event", "new WebSocket", "socket.io")


def _python_code_only(path: pathlib.Path) -> str:
    """Python source with comments and string literals removed.

    Necessary, not fastidious: `ui/sse.py` QUOTES spec §12.1 verbatim in its
    docstring ("No emit_demo_event(), no parallel websocket telemetry"), which
    is exactly what well-documented code should do. A raw text scan would
    flag the citation of the rule as a violation of it.
    """
    import io
    import tokenize

    out: list[str] = []
    with io.StringIO(path.read_text(encoding="utf-8")) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(token.string)
    return " ".join(out)


def test_no_bespoke_telemetry_channel_is_USED_anywhere():
    """spec §12.1 names `emit_demo_event` explicitly. Its absence — and the
    absence of any websocket — is the invariant.

    The rule may be QUOTED (in a docstring, a comment, or this test). It may
    not be USED, so every file is scanned with comments and strings stripped.
    """
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".js", ".html", ".css"}:
            continue
        if {".venv", ".git", "__pycache__"} & set(path.parts):
            continue

        if path.suffix == ".py":
            code = _python_code_only(path)
        elif path.suffix == ".js":
            code = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
            code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("//"))
        else:
            code = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8", errors="ignore"), flags=re.DOTALL)

        for token in BANNED_TOKENS:
            assert token not in code, str(path) + " uses " + token


def test_the_backend_exposes_no_websocket_route():
    """Checked on the AST, so prose explaining WHY there is no websocket does
    not trip it."""
    for path in (REPO / "ui").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                assert not any("websocket" in n.lower() for n in names), str(path)
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    assert "websocket" not in ast.dump(decorator).lower(), (
                        str(path) + "::" + node.name
                    )


# ------------------------------------------------------------ 4. adversarial


@pytest.mark.postgres
def test_every_served_event_corresponds_to_a_real_audit_row(demo_api):
    """Live, end to end: nothing the API streams was synthesised."""
    client, conn = demo_api
    served = client.get("/api/events?limit=5000").json()
    assert served, "the run produced no events"

    with conn.cursor() as cur:
        cur.execute("SELECT event_id::text, event_type, prev_hash FROM audit_events")
        real = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    assert len(served) == len(real)
    for event in served:
        assert event["event_id"] in real, "the API served an event that is not in the chain"
        assert real[event["event_id"]] == (event["event_type"], event["prev_hash"])


@pytest.mark.postgres
def test_a_fabricated_fact_pushed_at_the_api_never_enters_the_chain(demo_api):
    """Someone trying to inject a fake 'grant confirmed' through the only
    state-changing surface the UI has must be refused, and must leave the
    chain untouched."""
    client, conn = demo_api
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_events")
        before = cur.fetchone()[0]

    assert client.post("/api/chaos/grant_confirmed", json={}).status_code == 400
    assert client.post("/api/chaos/nonexistent", json={"target": "x"}).status_code == 400
    assert client.post(
        "/api/chaos/kill_model", json={"unexpected_field": "grant confirmed"}
    ).status_code == 422

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_events")
        assert cur.fetchone()[0] == before


@pytest.mark.postgres
def test_the_served_hash_is_recomputed_and_actually_chains(demo_api):
    """`hash` is not a stored column — it is recomputed from the event, which
    is what lets a viewer check linkage by eye. So it must really link."""
    from sampark.audit.canonical import GENESIS_HASH

    client, _conn = demo_api
    served = client.get("/api/events?limit=5000").json()
    assert served[0]["prev_hash"] == GENESIS_HASH
    for previous, current in zip(served, served[1:]):
        assert current["prev_hash"] == previous["hash"], (
            "event #" + str(current["seq"]) + " does not chain to its predecessor"
        )
