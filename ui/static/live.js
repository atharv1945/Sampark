/* SAMPARK — Live Razorpay Test page.
 *
 * THE TRACE-INTEGRITY RULE (spec §12.1), implemented rather than asserted:
 *
 *   "The UI renders the audit log and nothing else."
 *
 * Three state stores, never merged:
 *
 *   auditState    SYSTEM TRUTH. Written by ingestAuditEvents() and by nothing
 *                 else, ever. ingestAuditEvents() is called from exactly two
 *                 functions — onSseMessage() (the SSE handler) and
 *                 backfillFrom() (the gap repair). Both carry nothing but rows
 *                 out of the audit_events table. Every verdict, reason code
 *                 and expected-net figure on this page comes from here.
 *                 `expected_net_paise` in particular is READ OFF the decision
 *                 event, never recomputed in the browser — the allocator owns
 *                 that arithmetic.
 *
 *   controlState  INTEGRATION CONTROL STATE. Session id, payment links,
 *                 transport labels, MCP capability, gateway fallback reasons,
 *                 step output. Real, but not system truth, so it renders only
 *                 in regions marked as operator control or MCP provenance and
 *                 never contributes a pipeline node, a colour or a verdict.
 *
 *   ui            Pure presentation: selection, the SSE handle.
 *
 * There is no second telemetry channel of any kind.
 */

'use strict';

/* ===================== SYSTEM TRUTH (audit only) ===================== */

const auditState = {
  events: [],
  seen: new Set(),
  lastSeq: 0,
  counts: { events: 0 },
  /* risk_id -> everything known about that opportunity, each field traced to
   * the event that supplied it. Written only inside tally(). */
  opportunities: {},
  order: [],
  /* Cross-references so a grant lifecycle event, which carries only
   * grant_id/request_id, can be attributed to its opportunity. Both are built
   * from the grant.reserved / request.received events themselves. */
  riskByRequest: {},
  riskByGrant: {}
};

/* =============== INTEGRATION CONTROL STATE (marked in the UI) ========= */

const controlState = { session: null, links: [], transport: null, mcp: null, modeCheck: null };

/* ========================== PRESENTATION ============================= */

const ui = { selected: null, source: null };

/* ===================================================================== */

const NODE_FOR_TYPE = {
  'payment.risk_detected': 'risk',
  'request.received': 'request',
  'request.denied_on_scope': 'scope',
  'grant.reserved': 'reserve',
  'grant.executing': 'execute',
  'grant.confirmed': 'settle',
  'grant.rolled_back': 'settle',
  'grant.expired': 'settle'
};

/* A denial's reason code names the stage that produced it. `scope.*` is the
 * registry; `allocation.*` is the allocator's comparative outcome; everything
 * else is a hard policy rule or a budget. Read off the event, never guessed. */
function stageForReason(rc) {
  if (!rc) return 'policy';
  if (rc.indexOf('scope.') === 0) return 'scope';
  if (rc.indexOf('allocation.') === 0) return 'allocate';
  return 'policy';
}

function classify(ev) {
  const t = ev.event_type, rc = ev.reason_code || '';
  if (t === 'request.denied_on_scope') return 'scope';
  if (t === 'grant.rolled_back') return 'rolled';
  if (t === 'grant.reserved' || t === 'grant.confirmed') return 'granted';
  if (t === 'decision.denied' || t === 'decision.deferred') {
    return rc.indexOf('scope.') === 0 ? 'scope' : 'policy';
  }
  return 'pending';
}

/* ---------- the ONLY writer of auditState ---------- */

function ingestAuditEvents(rows) {
  for (const ev of rows) {
    if (auditState.seen.has(ev.event_id)) continue;
    auditState.seen.add(ev.event_id);
    auditState.events.push(ev);
    if (ev.seq > auditState.lastSeq) auditState.lastSeq = ev.seq;
    tally(ev);
    renderEventRow(ev);
    litNode(ev);
  }
  renderCases();
  document.getElementById('event-count').textContent = auditState.counts.events;
}

function opportunity(riskId) {
  if (!auditState.opportunities[riskId]) {
    auditState.opportunities[riskId] = {
      risk_id: riskId, payment_id: null, amount_paise: null, root_cause: null,
      failure_code: null, method: null, transport: null, operation: null,
      customer_id: null, decision: null, reason_code: null, expected_net_paise: null,
      channel: null, intent: null, grant_state: null, request_id: null
    };
    auditState.order.push(riskId);
  }
  return auditState.opportunities[riskId];
}

function tally(ev) {
  const p = ev.payload || {};
  auditState.counts.events += 1;

  if (p.request_id && p.risk_id) auditState.riskByRequest[p.request_id] = p.risk_id;
  if (p.grant_id && p.risk_id) auditState.riskByGrant[p.grant_id] = p.risk_id;

  let riskId = p.risk_id;
  if (!riskId && p.grant_id) riskId = auditState.riskByGrant[p.grant_id];
  if (!riskId && p.request_id) riskId = auditState.riskByRequest[p.request_id];
  if (!riskId) return;

  const o = opportunity(riskId);

  if (ev.event_type === 'payment.risk_detected') {
    o.payment_id = p.payment_id;
    o.amount_paise = p.amount_paise;
    o.root_cause = p.root_cause;
    o.failure_code = p.failure_code || p.context_code;
    o.method = p.method;
    o.transport = p.transport;
    o.operation = p.operation;
    o.customer_id = p.customer_id;
  }
  if (ev.event_type === 'request.received') o.request_id = p.request_id;
  if (ev.event_type === 'request.denied_on_scope') {
    o.decision = 'DENIED'; o.reason_code = ev.reason_code;
  }
  if (ev.event_type === 'decision.denied' || ev.event_type === 'decision.deferred') {
    o.decision = ev.event_type === 'decision.denied' ? 'DENIED' : 'DEFERRED';
    o.reason_code = ev.reason_code;
    if (p.expected_net_paise !== undefined) o.expected_net_paise = p.expected_net_paise;
    if (p.amount_paise !== undefined && o.amount_paise === null) o.amount_paise = p.amount_paise;
  }
  if (ev.event_type === 'grant.reserved') {
    o.decision = 'GRANTED'; o.reason_code = null;
    o.channel = p.channel; o.intent = p.intent; o.grant_state = 'RESERVED';
  }
  if (ev.event_type === 'grant.executing') o.grant_state = 'EXECUTING';
  if (ev.event_type === 'grant.confirmed') o.grant_state = 'CONFIRMED';
  if (ev.event_type === 'grant.rolled_back') { o.grant_state = 'ROLLED_BACK'; o.decision = 'ROLLED BACK'; }
}

/* ---------- rendering the two cases, entirely from auditState ---------- */

function rupees(paise) {
  if (paise === null || paise === undefined) return '—';
  const sign = paise < 0 ? '−' : '';
  return sign + '₹' + (Math.abs(paise) / 100).toLocaleString('en-IN',
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const CASE_CLASS = {
  'GRANTED': 'is-granted', 'DENIED': 'is-denied',
  'DEFERRED': 'is-deferred', 'ROLLED BACK': 'is-rolled'
};

function labelForTransport(t) {
  if (t === 'mcp') return 'Razorpay MCP Server';
  if (t === 'rest_api') return 'Razorpay Test API';
  if (t === 'webhook') return 'Razorpay webhook';
  return t || '—';
}

function set(id, text) { document.getElementById(id).textContent = text; }

/* Slot 0 is the headline case, slot 1 the contrast — insertion order in the
 * chain, which follows the order the links were created and polled. */
function renderCases() {
  ['a', 'b'].forEach((key, slot) => {
    const riskId = auditState.order[slot];
    const card = document.getElementById('case-' + key);
    if (!riskId) return;
    const o = auditState.opportunities[riskId];

    card.className = 'ccase ' + (CASE_CLASS[o.decision] || '');
    if (o.amount_paise !== null) set(key + '-amount', rupees(o.amount_paise));
    set(key + '-payment', o.payment_id || '—');
    set(key + '-cause', o.root_cause ? o.root_cause.replace(/_/g, ' ') : '—');
    set(key + '-verdict', o.decision || 'deciding…');
    set(key + '-reason', o.reason_code || (o.decision === 'GRANTED' ? 'clears the economic bar' : '—'));

    const net = document.getElementById(key + '-net');
    if (o.expected_net_paise === null || o.expected_net_paise === undefined) {
      net.textContent = o.decision === 'GRANTED' ? 'positive' : '—';
      net.className = o.decision === 'GRANTED' ? 'good' : '';
    } else {
      net.textContent = rupees(o.expected_net_paise);
      net.className = o.expected_net_paise < 0 ? 'bad' : 'good';
    }

    let recovery = '—';
    if (o.grant_state === 'CONFIRMED') recovery = 'Executed → CONFIRMED';
    else if (o.grant_state === 'ROLLED_BACK') recovery = 'Rolled back — budget released';
    else if (o.grant_state) recovery = o.grant_state;
    else if (o.decision === 'DENIED') recovery = 'Not attempted';
    else if (o.decision === 'DEFERRED') recovery = 'Deferred to a later window';
    set(key + '-recovery', recovery);

    if (o.transport) {
      set(key + '-note', 'Read from Razorpay via ' + labelForTransport(o.transport)
        + ', then decided by the shipped allocator.');
    }
  });
}

/* ---------- the trace ---------- */

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function renderEventRow(ev) {
  const feed = document.getElementById('feed');
  feed.className = 'feed';
  const row = el('div', 'ev ' + classify(ev));
  row.appendChild(el('span', 'seq', '#' + ev.seq));
  row.appendChild(el('span', 'ty', ev.event_type));
  row.appendChild(el('span', 'rc', ev.reason_code || ''));
  row.tabIndex = 0;
  row.onclick = () => select(ev, row);
  row.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(ev, row); } };
  feed.appendChild(row);
  feed.scrollTop = feed.scrollHeight;
}

function litNode(ev) {
  let node = NODE_FOR_TYPE[ev.event_type];
  if (ev.event_type === 'decision.denied' || ev.event_type === 'decision.deferred') {
    node = stageForReason(ev.reason_code);
  }
  if (!node) return;
  if (ev.event_type === 'payment.risk_detected') {
    const first = document.querySelector('#pipeline li[data-node="detect"]');
    if (first && !first.className) first.className = 'lit lit-pending';
  }
  const li = document.querySelector('#pipeline li[data-node="' + node + '"]');
  if (!li) return;
  li.className = 'lit lit-' + classify(ev);
  /* A later stage implies the earlier ones admitted the candidate. That is
   * read off THIS event, not assumed: a grant.reserved cannot exist unless
   * scope, hard policy and allocation all passed, and an `allocation.*`
   * denial cannot exist unless scope and hard policy passed. */
  const priorFor = {
    policy: ['detect', 'risk', 'request', 'scope'],
    allocate: ['detect', 'risk', 'request', 'scope', 'policy'],
    reserve: ['detect', 'risk', 'request', 'scope', 'policy', 'allocate'],
    execute: ['detect', 'risk', 'request', 'scope', 'policy', 'allocate', 'reserve'],
    settle: ['detect', 'risk', 'request', 'scope', 'policy', 'allocate', 'reserve', 'execute']
  };
  (priorFor[node] || []).forEach(prior => {
    const n = document.querySelector('#pipeline li[data-node="' + prior + '"]');
    if (n && !n.className) n.className = 'lit lit-granted';
  });
}

function select(ev, row) {
  document.querySelectorAll('.ev.sel').forEach(x => x.classList.remove('sel'));
  row.classList.add('sel');
  ui.selected = ev;
  document.getElementById('raw').textContent = JSON.stringify(ev, null, 2);
  const rid = ev.payload && ev.payload.request_id;
  if (rid) explainRequest(rid);
  else {
    document.getElementById('explanation').className = 'explain faint';
    document.getElementById('explanation').textContent =
      'This event carries no request_id, so there is no per-request explanation for it.';
    document.getElementById('explain-events').textContent = '';
  }
}

async function explainRequest(requestId) {
  const box = document.getElementById('explanation');
  box.className = 'explain faint';
  box.textContent = 'explaining ' + requestId + '…';
  try {
    const r = await fetch('/api/integrations/razorpay/explain/request/' + requestId);
    const j = await r.json();
    if (!r.ok) { box.textContent = j.detail || ('HTTP ' + r.status); return; }
    box.className = 'explain';
    box.textContent = j.explanation;
    document.getElementById('explain-events').textContent =
      j.events.map(e => '#' + e.seq + '  ' + e.event_type + '  ' + (e.reason_code || '')).join('\n');
  } catch (e) { box.textContent = String(e); }
}

/* ---------- the SSE stream: the ONLY source of trace data ---------- */

function onSseMessage(msg) {
  let ev;
  try { ev = JSON.parse(msg.data); } catch (e) { return; }
  if (auditState.lastSeq && ev.seq > auditState.lastSeq + 1) {
    backfillFrom(auditState.lastSeq, ev);
    return;
  }
  ingestAuditEvents([ev]);
}

async function backfillFrom(afterSeq, pendingEvent) {
  try {
    const r = await fetch('/api/integrations/razorpay/events?after_seq=' + afterSeq + '&limit=1000');
    if (r.ok) ingestAuditEvents(await r.json());
  } catch (e) { /* the next frame retries the repair */ }
  if (pendingEvent) ingestAuditEvents([pendingEvent]);
}

function openStream() {
  if (ui.source) ui.source.close();
  const src = new EventSource('/api/integrations/razorpay/stream?after_seq=' + auditState.lastSeq);
  ui.source = src;
  src.addEventListener('audit', onSseMessage);
  src.addEventListener('end', () => { src.close(); ui.source = null; });
  src.onerror = () => { if (ui.source) { ui.source.close(); ui.source = null; } };
}

/* ---------- integration control state (never merged into the trace) --- */

function renderMcp() {
  const m = controlState.mcp, t = controlState.transport;
  const light = document.getElementById('light-mcp');
  const tag = document.getElementById('mcp-tag');

  if (m && m.reachable) {
    set('mcp-server', m.server.name);
    set('mcp-version', m.server.version);
    set('mcp-tools', String(m.tools.length));
    light.className = 'light light-on';
    light.querySelector('.dot').className = 'dot dot-live';
    set('light-mcp-text', 'MCP CONNECTED');
    tag.className = 'tag tag-live-rzp';
    tag.textContent = 'Live · Razorpay MCP';
    document.querySelectorAll('#mcp-ops li').forEach(li => {
      const offered = m.tools.indexOf(li.dataset.op) !== -1;
      li.className = offered ? 'on' : '';
      li.querySelector('.ok').textContent = offered ? '✓' : '—';
    });
  } else if (m) {
    set('mcp-server', 'not reachable');
    set('mcp-version', '—');
    set('mcp-tools', '—');
    light.className = 'light light-warn';
    light.querySelector('.dot').className = 'dot dot-off';
    set('light-mcp-text', 'MCP UNAVAILABLE — REST FALLBACK');
    tag.className = 'tag tag-arch';
    tag.textContent = 'Fallback · Razorpay Test API';
  }

  /* PREFERRED is configuration; PERFORMED is what a transport actually did.
   * Showing only the former beside the word "in use" would overclaim before a
   * single call had been made. */
  set('mcp-transport', t ? labelForTransport(t.preferred_transport) : '—');

  const links = controlState.links || [];
  const latest = links.length ? links[links.length - 1] : null;
  const last = document.getElementById('mcp-last');
  if (latest && latest.provenance) {
    last.textContent = latest.provenance.operation + ' via ' + labelForTransport(latest.provenance.transport);
    last.className = latest.provenance.transport === 'mcp' ? 'good' : 'warnc';
  } else {
    last.textContent = 'none yet';
    last.className = '';
  }

  const check = document.getElementById('mcp-modecheck');
  if (controlState.modeCheck) {
    check.textContent = controlState.modeCheck.same_test_ledger ? 'verified' : 'not verified';
    check.className = controlState.modeCheck.same_test_ledger ? 'good' : 'warnc';
  }

  const fb = document.getElementById('mcp-fallback');
  const fallen = (controlState.links || []).filter(l => l.fallback_reason);
  if (fallen.length) {
    fb.className = 'note note-warn';
    fb.textContent = 'MCP was not used for at least one operation: ' + fallen[0].fallback_reason;
  } else if (m && !m.reachable && m.reason) {
    fb.className = 'note note-warn';
    fb.textContent = m.reason;
  } else {
    fb.className = 'note note-warn hide';
    fb.textContent = '';
  }
}

function renderLinks() {
  const box = document.getElementById('link-list');
  box.innerHTML = '';
  (controlState.links || []).forEach(link => {
    const row = el('div', 'linkrow');
    row.appendChild(el('span', 'role', link.role));
    row.appendChild(el('span', null, link.payment_link_id));
    row.appendChild(el('span', null, rupees(link.amount_paise)));
    row.appendChild(el('span', null, 'via ' + labelForTransport(link.provenance.transport)));
    box.appendChild(row);

    /* The checkout button on the matching case card. It opens the REAL
     * Razorpay test checkout — this page never imitates it. */
    const key = link.role === 'contrast' ? 'b' : 'a';
    const btn = document.getElementById(key + '-open');
    if (link.short_url) { btn.href = link.short_url; btn.className = 'btn btn-sm cc-open'; }
    const amount = document.getElementById(key + '-amount');
    if (!auditState.order.length) amount.textContent = rupees(link.amount_paise);
  });
}

function setSamparkLight(active, text) {
  const light = document.getElementById('light-sam');
  light.className = active ? 'light light-on' : 'light';
  light.querySelector('.dot').className = active ? 'dot dot-live' : 'dot dot-off';
  set('light-sam-text', text);
}

function stepOut(id, text, isErr) {
  const n = document.getElementById(id);
  n.textContent = text;
  n.className = 'st-out' + (isErr ? ' err' : ' ok');
}

async function refreshState() {
  try {
    const s = await (await fetch('/api/integrations/razorpay/state')).json();
    controlState.session = s.session_id;
    controlState.links = s.payment_links || [];
    controlState.transport = s.transport;
    setSamparkLight(!!s.active, s.active ? 'SAMPARK ONLINE' : 'SAMPARK IDLE');
    renderMcp();
    renderLinks();
    /* A page reload during a demo would otherwise show an empty trace beside a
     * live session. Reopening the AUDIT stream replays it from seq 0, because a
     * freshly loaded page has lastSeq = 0. Nothing from this /state response is
     * copied into auditState — that is what keeps the two stores separate. */
    if (s.active && !ui.source) openStream();
  } catch (e) { /* leave the last known control state */ }
}

async function refreshHealth() {
  try {
    const h = await (await fetch('/api/integrations/razorpay/health?probe=true')).json();
    controlState.transport = h.transport;
    controlState.mcp = h.mcp_probe || null;
    controlState.modeCheck = h.mcp_test_mode_check || null;
    renderMcp();
  } catch (e) { /* health is best-effort; the page works without it */ }
}

function toast(msg, isErr) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'show' + (isErr ? ' err' : '');
  setTimeout(() => { t.className = ''; }, 6000);
}

async function post(path, body) {
  const r = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {})
  });
  return { ok: r.ok, status: r.status, body: await r.json() };
}

/* ---------- buttons ---------- */

document.getElementById('btn-session').onclick = async () => {
  const r = await post('/api/integrations/razorpay/session');
  if (!r.ok) { stepOut('out-session', r.body.detail || ('HTTP ' + r.status), true); return; }
  resetView();
  stepOut('out-session', 'session ' + r.body.session_id + '\nschema ' + r.body.demo_schema
    + '\nagent payment_retry_agent registered'
    + (r.body.model_degraded
        ? '\nscorer ' + r.body.scorer + ' — the uplift model is unavailable on this data, and that is logged'
        : ''));
  await refreshState();
  openStream();
};

async function createLink(role, outId, amountInr) {
  stepOut(outId, 'creating a real Razorpay Test Mode payment link…');
  const body = amountInr ? { role: role, amount_inr: amountInr } : { role: role };
  const r = await post('/api/integrations/razorpay/payment-link', body);
  if (!r.ok) { stepOut(outId, r.body.detail || ('HTTP ' + r.status), true); return; }
  const link = r.body;
  const out = document.getElementById(outId);
  out.textContent = '';
  out.className = 'st-out ok';
  out.appendChild(el('div', null, link.payment_link_id + '  ' + rupees(link.amount_paise)
    + '\nvia ' + labelForTransport(link.provenance.transport)));
  if (link.short_url) {
    const a = el('a', null, 'Open Razorpay Test Checkout ↗');
    a.href = link.short_url; a.target = '_blank'; a.rel = 'noopener noreferrer';
    out.appendChild(a);
  }
  if (link.fallback_reason) out.appendChild(el('div', 'warn', 'MCP not used: ' + link.fallback_reason));
  await refreshState();
}

document.getElementById('btn-link').onclick = () => createLink('headline', 'out-link', null);

document.getElementById('btn-contrast').onclick = () => {
  const raw = document.getElementById('contrast-amount').value;
  createLink('contrast', 'out-contrast', raw ? parseInt(raw, 10) : null);
};

document.getElementById('btn-refresh').onclick = async () => {
  const r = await fetch('/api/integrations/razorpay/payment-link');
  const j = await r.json();
  if (!r.ok) { stepOut('out-refresh', j.detail || ('HTTP ' + r.status), true); return; }
  stepOut('out-refresh', (j.links || []).map(l =>
    l.role + '  ' + l.payment_link_id + '\n  status ' + (l.status || '?') + ' · '
    + (l.attempts || []).length + ' attempt(s), '
    + (l.attempts || []).filter(a => a.status === 'failed').length + ' failed'
    + ' · read via ' + labelForTransport(l.read_provenance.transport)).join('\n'));
};

document.getElementById('btn-ingest').onclick = async () => {
  stepOut('out-ingest', 'asking Razorpay for failed attempts…');
  const r = await post('/api/integrations/razorpay/ingest');
  if (!r.ok) { stepOut('out-ingest', r.body.detail || ('HTTP ' + r.status), true); return; }
  if (!r.body.ingested) { stepOut('out-ingest', r.body.reason, true); return; }
  const lines = r.body.results.map(res => {
    const d = res.delivery;
    return res.role + '  ' + res.payment_id + ' → ' + res.outcome
      + (res.reason_code ? '\n  ' + res.reason_code : '')
      + (res.duplicate ? '\n  DUPLICATE — no second decision' : '')
      + '\n  matched by ' + (res.matcher || 'webhook')
      + (d ? '\n  ' + d.channel + ' attempts=' + d.attempts
             + (d.rolled_back ? ' ROLLED BACK' : ' delivered') : '');
  });
  (r.body.skipped || []).forEach(s => lines.push(s.role + '  ' + s.payment_link_id + '\n  ' + s.reason));
  stepOut('out-ingest', lines.join('\n'));
  openStream();
};

document.getElementById('btn-provider-fail').onclick = async () => {
  const r = await post('/api/integrations/razorpay/provider-failure', { mode: 'hard_down' });
  toast(r.ok ? 'Provider armed (' + r.body.mode + ') — the next send fails and the grant rolls back'
             : (r.body.detail || ('HTTP ' + r.status)), !r.ok);
};

document.getElementById('btn-verify').onclick = async () => {
  const r = await fetch('/api/integrations/razorpay/verify');
  const j = await r.json();
  if (!r.ok) { stepOut('out-verify', j.detail || ('HTTP ' + r.status), true); return; }
  document.getElementById('raw').textContent = j.summary;
  stepOut('out-verify', (j.valid ? 'chain VALID' : 'chain INVALID')
    + '\n' + j.event_count + ' events · genesis ' + j.genesis_ok + ' · linkage ' + j.linkage_ok
    + '\nhead ' + (j.head_hash || '').slice(0, 24) + '…', !j.valid);
};

document.getElementById('btn-reset').onclick = async () => {
  if (ui.source) { ui.source.close(); ui.source = null; }
  await post('/api/integrations/razorpay/reset');
  resetView();
  ['out-session', 'out-link', 'out-contrast', 'out-refresh', 'out-ingest', 'out-verify']
    .forEach(id => { const n = document.getElementById(id); n.textContent = ''; n.className = 'st-out'; });
  toast('Reset — demo schema dropped');
  refreshState();
};

function resetView() {
  auditState.events = []; auditState.seen = new Set(); auditState.lastSeq = 0;
  auditState.counts.events = 0;
  auditState.opportunities = {}; auditState.order = [];
  auditState.riskByRequest = {}; auditState.riskByGrant = {};

  const feed = document.getElementById('feed');
  feed.innerHTML = '';
  feed.className = 'feed empty';
  feed.textContent = 'Nothing yet — run the test on the right.';
  document.querySelectorAll('#pipeline li').forEach(x => { x.className = ''; });
  set('event-count', '0');
  document.getElementById('explanation').className = 'explain faint';
  document.getElementById('explanation').textContent = 'Click an event carrying a request_id.';
  document.getElementById('explain-events').textContent = '';

  /* Both case cards back to "not yet decided". Leaving a previous run's
   * verdict on screen after a reset would be a small lie about what the page
   * currently knows. */
  ['a', 'b'].forEach(key => {
    document.getElementById('case-' + key).className = 'ccase';
    set(key + '-verdict', 'awaiting decision');
    ['payment', 'cause', 'reason', 'net', 'recovery'].forEach(f => set(key + '-' + f, '—'));
    document.getElementById(key + '-net').className = '';
  });
  renderLinks();
}

refreshState();
refreshHealth();
