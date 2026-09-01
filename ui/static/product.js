/* SAMPARK product surface — the Razorpay Test Mode flow.
 *
 * THE TRACE-INTEGRITY RULE (spec §12.1), implemented here exactly as it is
 * in app.js:
 *
 *   "The UI renders the audit log and nothing else."
 *
 * Three state stores, never merged:
 *
 *   auditState    SYSTEM TRUTH. Written by ingestAuditEvents() and by nothing
 *                 else, ever. ingestAuditEvents() is called from exactly two
 *                 functions — onSseMessage() (the SSE handler) and
 *                 backfillFrom() (the gap repair). Both carry nothing but
 *                 rows out of the audit_events table. The hero card, the
 *                 comparison table, the pipeline and the trace are rendered
 *                 from this and only this. Notably `expected_net_paise` — the
 *                 number that explains the whole decision — is READ OFF the
 *                 decision event, never recomputed in the browser.
 *
 *   controlState  INTEGRATION CONTROL STATE. Session id, payment links,
 *                 transport labels, gateway fallback reasons, step output.
 *                 Real, but not system truth, so it renders only inside
 *                 regions marked "integration control" and never contributes
 *                 a pipeline node, a colour or a decision.
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

const controlState = { session: null, links: [], transport: null, mcp: null };

/* ========================== PRESENTATION ============================= */

const ui = { selected: null, source: null };

/* ===================================================================== */

const NODE_FOR_TYPE = {
  'payment.risk_detected': 'detect',
  'request.received': 'request',
  'request.denied_on_scope': 'scope',
  'grant.reserved': 'reserve',
  'grant.executing': 'execute',
  'grant.confirmed': 'settle',
  'grant.rolled_back': 'settle',
  'grant.expired': 'settle'
};

/* A denial's reason code names the stage that produced it. `allocation.*` is
 * a comparative outcome from the allocator; `scope.*` is the registry;
 * everything else is a hard policy rule or a budget. Read off the event. */
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
  renderHero();
  renderComparison();
  document.getElementById('event-count').textContent = auditState.counts.events;
}

function opportunity(riskId) {
  if (!auditState.opportunities[riskId]) {
    auditState.opportunities[riskId] = {
      risk_id: riskId, payment_id: null, amount_paise: null, root_cause: null,
      failure_code: null, method: null, transport: null, operation: null,
      customer_id: null, decision: null, reason_code: null,
      expected_net_paise: null, channel: null, intent: null,
      incentive_ceiling_paise: null, send_after: null, grant_state: null,
      request_id: null
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
    o.decision = 'GRANTED';
    o.reason_code = null;
    o.channel = p.channel;
    o.intent = p.intent;
    o.incentive_ceiling_paise = p.incentive_ceiling_paise;
    o.send_after = p.send_after;
    o.grant_state = 'RESERVED';
  }
  if (ev.event_type === 'grant.executing') o.grant_state = 'EXECUTING';
  if (ev.event_type === 'grant.confirmed') o.grant_state = 'CONFIRMED';
  if (ev.event_type === 'grant.rolled_back') { o.grant_state = 'ROLLED_BACK'; o.decision = 'ROLLED BACK'; }
}

/* ---------- rendering, entirely from auditState ---------- */

function rupees(paise) {
  if (paise === null || paise === undefined) return '₹—';
  const sign = paise < 0 ? '−' : '';
  return sign + '₹' + (Math.abs(paise) / 100).toLocaleString('en-IN',
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const DECISION_CLASS = {
  'GRANTED': 'hero-granted', 'DENIED': 'hero-denied',
  'DEFERRED': 'hero-deferred', 'ROLLED BACK': 'hero-rolled'
};

function labelForTransport(t) {
  if (t === 'mcp') return 'Razorpay MCP Server';
  if (t === 'rest_api') return 'Razorpay Test API';
  if (t === 'webhook') return 'Razorpay webhook';
  return t || '—';
}

/* The hero is the FIRST opportunity the chain recorded, which is the
 * headline payment: the product surface creates and polls the headline link
 * before any contrast link. */
function heroOpportunity() {
  return auditState.order.length ? auditState.opportunities[auditState.order[0]] : null;
}

function renderHero() {
  const card = document.getElementById('hero');
  const o = heroOpportunity();
  if (!o) { card.className = 'card hero-idle'; return; }

  const amount = document.getElementById('hero-amount');
  amount.className = o.amount_paise === null || o.amount_paise === undefined ? 'idle' : '';
  amount.textContent = amount.className === 'idle'
    ? 'awaiting a failed payment' : rupees(o.amount_paise);
  document.getElementById('hero-payment').textContent = o.payment_id || '—';
  document.getElementById('hero-source').textContent =
    'Razorpay Test Mode · via ' + labelForTransport(o.transport);

  document.getElementById('hero-rootcause').textContent =
    o.root_cause ? o.root_cause.replace(/_/g, ' ') : '—';
  document.getElementById('hero-failcode').textContent =
    (o.failure_code || 'waiting') + (o.method ? ' · ' + o.method : '');

  document.getElementById('hero-decision').textContent = o.decision || 'deciding…';
  document.getElementById('hero-reason').textContent =
    o.reason_code || (o.decision === 'GRANTED' ? 'admitted by every hard rule, then won its window'
                                               : 'the allocator has not run');

  const iv = document.getElementById('hero-intervention');
  const ivd = document.getElementById('hero-intervention-detail');
  if (o.decision === 'GRANTED' || o.grant_state) {
    iv.textContent = (o.intent || 'contact').replace(/_/g, ' ') + ' · ' + (o.channel || '');
    ivd.textContent = 'incentive ceiling ' + rupees(o.incentive_ceiling_paise)
      + ' · ' + (o.grant_state || '');
  } else if (o.decision) {
    iv.textContent = 'none — deliberately';
    ivd.textContent = o.expected_net_paise !== null && o.expected_net_paise !== undefined
      ? ('expected net ' + rupees(o.expected_net_paise)
         + ' · no grant issued, nothing sent, no budget spent')
      : 'no grant issued, nothing sent, no budget spent';
  }
  card.className = 'card ' + (DECISION_CLASS[o.decision] || 'hero-idle');
}

function renderComparison() {
  const box = document.getElementById('comparison');
  if (!auditState.order.length) { box.className = 'muted small'; return; }
  box.className = '';
  box.innerHTML = '';

  const table = el('table', 'cmp');
  const head = el('tr');
  ['Payment', 'At risk', 'Why it failed', 'Expected net', 'Decision'].forEach(h => {
    head.appendChild(el('th', null, h));
  });
  table.appendChild(head);

  auditState.order.forEach(riskId => {
    const o = auditState.opportunities[riskId];
    const tr = el('tr', 'cmp-' + (o.decision === 'GRANTED' ? 'granted'
      : o.decision === 'ROLLED BACK' ? 'rolled' : o.decision ? 'denied' : 'pending'));
    tr.appendChild(el('td', 'mono', o.payment_id || '—'));
    tr.appendChild(el('td', 'num', rupees(o.amount_paise)));
    tr.appendChild(el('td', null, (o.root_cause || '—').replace(/_/g, ' ')));
    tr.appendChild(el('td', 'num ' + (o.expected_net_paise < 0 ? 'bad' : 'good'),
      o.expected_net_paise === null || o.expected_net_paise === undefined
        ? (o.decision === 'GRANTED' ? 'positive' : '—') : rupees(o.expected_net_paise)));
    const d = el('td', 'mono');
    d.appendChild(el('b', null, o.decision || 'deciding…'));
    if (o.reason_code) d.appendChild(el('div', 'small', o.reason_code));
    tr.appendChild(d);
    table.appendChild(tr);
  });
  box.appendChild(table);
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function renderEventRow(ev) {
  const feed = document.getElementById('feed');
  feed.className = '';
  const row = el('div', 'ev ' + classify(ev));
  row.appendChild(el('span', 'seq', '#' + ev.seq));
  row.appendChild(el('span', 'ty', ev.event_type));
  row.appendChild(el('span', 'rc', ev.reason_code || ''));
  row.onclick = () => select(ev, row);
  feed.appendChild(row);
  feed.scrollTop = feed.scrollHeight;
}

function litNode(ev) {
  let node = NODE_FOR_TYPE[ev.event_type];
  if (ev.event_type === 'decision.denied' || ev.event_type === 'decision.deferred') {
    node = stageForReason(ev.reason_code);
  }
  if (!node) return;
  const li = document.querySelector('#pipeline li[data-node="' + node + '"]');
  if (!li) return;
  li.className = 'lit lit-' + classify(ev);
  /* Reaching a later stage means the earlier ones admitted the candidate.
   * That is read off THIS event, not assumed: a grant.reserved cannot exist
   * unless scope, hard policy and allocation all passed, and an
   * `allocation.*` denial cannot exist unless scope and hard policy passed. */
  const priorFor = {
    allocate: ['request', 'scope', 'policy'],
    reserve: ['request', 'scope', 'policy', 'allocate'],
    execute: ['request', 'scope', 'policy', 'allocate', 'reserve'],
    settle: ['request', 'scope', 'policy', 'allocate', 'reserve', 'execute'],
    policy: ['request', 'scope']
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
    document.getElementById('explanation').className = 'muted';
    document.getElementById('explanation').textContent =
      'This event carries no request_id, so there is no per-request explanation for it.';
    document.getElementById('explain-events').textContent = '';
  }
}

async function explainRequest(requestId) {
  const box = document.getElementById('explanation');
  box.className = 'muted';
  box.textContent = 'explaining ' + requestId + '…';
  try {
    const r = await fetch('/api/integrations/razorpay/explain/request/' + requestId);
    const j = await r.json();
    if (!r.ok) { box.textContent = j.detail || ('HTTP ' + r.status); return; }
    box.className = '';
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

function renderIntegration() {
  const t = controlState.transport;
  const links = controlState.links || [];
  const latest = links.length ? links[links.length - 1] : null;

  document.getElementById('badge-transport').textContent =
    t ? ('prefers ' + labelForTransport(t.preferred_transport)) : 'transport —';
  document.getElementById('int-amount').textContent =
    t ? (rupees(t.amount_paise) + ' headline · ' + rupees(t.contrast_amount_paise) + ' contrast') : '—';

  const prov = latest && latest.provenance;
  document.getElementById('int-transport').textContent =
    prov ? labelForTransport(prov.transport) : (t ? 'not yet used' : '—');
  document.getElementById('int-operation').textContent = prov ? prov.operation : '—';

  const refs = document.getElementById('int-reference');
  refs.textContent = links.length
    ? links.map(l => l.role + ' ' + l.payment_link_id + ' (' + rupees(l.amount_paise) + ')').join('  |  ')
    : '—';

  const fb = document.getElementById('int-fallback');
  const fallback = links.filter(l => l.fallback_reason);
  if (fallback.length) {
    fb.className = 'note warn';
    fb.textContent = 'MCP was not used for at least one operation: ' + fallback[0].fallback_reason;
  } else {
    fb.className = 'note hidden';
    fb.textContent = '';
  }
}

function renderMcpCapability() {
  const box = document.getElementById('mcp-capability');
  const m = controlState.mcp;
  if (!m) { box.textContent = ''; return; }
  if (!m.reachable) {
    box.className = 'note warn';
    box.textContent = 'Razorpay MCP Server not reachable from this process: ' + (m.reason || 'unknown')
      + '. The REST test API is used instead, and every transport label above says so.';
    return;
  }
  box.className = 'note';
  box.textContent = 'Razorpay MCP Server reachable (' + m.server.name + ' ' + m.server.version + '): '
    + m.tools.length + ' tools offered; SAMPARK uses ' + m.tools_used_by_sampark.join(', ') + '.';
}

function stepOut(id, text, isErr) {
  const n = document.getElementById(id);
  n.textContent = text;
  n.className = 'step-out' + (isErr ? ' err' : ' ok');
}

async function refreshState() {
  try {
    const s = await (await fetch('/api/integrations/razorpay/state')).json();
    controlState.session = s.session_id;
    controlState.links = s.payment_links || [];
    controlState.transport = s.transport;
    document.getElementById('badge-session').textContent =
      s.active ? ('session ' + s.session_id + ' · ' + s.demo_schema) : 'no session';
    renderIntegration();
    /* A page reload during a demo would otherwise show an empty trace beside a
     * live session. Reopening the stream replays it from seq 0, because a
     * freshly loaded page has lastSeq = 0. Note this opens the AUDIT stream —
     * it does not copy anything out of this /state response into auditState,
     * which is what keeps the two stores separate. */
    if (s.active && !ui.source) openStream();
  } catch (e) { /* leave the last known control state */ }
}

async function refreshHealth() {
  try {
    const h = await (await fetch('/api/integrations/razorpay/health?probe=true')).json();
    controlState.transport = h.transport;
    controlState.mcp = h.mcp_probe || null;
    renderIntegration();
    renderMcpCapability();
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

function renderLinkOut(outId, link) {
  const out = document.getElementById(outId);
  out.textContent = '';
  out.className = 'step-out ok';
  out.appendChild(el('div', null, link.payment_link_id + ' · ' + rupees(link.amount_paise)
    + ' ' + link.currency + ' · via ' + labelForTransport(link.provenance.transport)));
  if (link.short_url) {
    const a = el('a', 'linkout', 'Open the Razorpay test checkout →');
    a.href = link.short_url; a.target = '_blank'; a.rel = 'noopener noreferrer';
    out.appendChild(a);
  }
  if (link.fallback_reason) out.appendChild(el('div', 'warn', 'MCP not used: ' + link.fallback_reason));
}

/* ---------- buttons ---------- */

document.getElementById('btn-session').onclick = async () => {
  const r = await post('/api/integrations/razorpay/session');
  if (!r.ok) { stepOut('out-session', r.body.detail || ('HTTP ' + r.status), true); return; }
  resetView();
  stepOut('out-session', 'session ' + r.body.session_id + ' · schema ' + r.body.demo_schema
    + ' · agent payment_retry_agent registered'
    + (r.body.model_degraded ? ' · scorer ' + r.body.scorer
        + ' (uplift model unavailable on this data — logged, not hidden)' : ''));
  await refreshState();
  openStream();
};

document.getElementById('btn-link').onclick = async () => {
  stepOut('out-link', 'creating the headline Razorpay Test Mode payment link…');
  const r = await post('/api/integrations/razorpay/payment-link', { role: 'headline' });
  if (!r.ok) { stepOut('out-link', r.body.detail || ('HTTP ' + r.status), true); return; }
  renderLinkOut('out-link', r.body);
  await refreshState();
};

document.getElementById('btn-contrast').onclick = async () => {
  const raw = document.getElementById('contrast-amount').value;
  const amount = raw ? parseInt(raw, 10) : null;
  stepOut('out-contrast', 'creating the contrast payment link…');
  const r = await post('/api/integrations/razorpay/payment-link',
    amount ? { role: 'contrast', amount_inr: amount } : { role: 'contrast' });
  if (!r.ok) { stepOut('out-contrast', r.body.detail || ('HTTP ' + r.status), true); return; }
  renderLinkOut('out-contrast', r.body);
  await refreshState();
};

document.getElementById('btn-refresh').onclick = async () => {
  const r = await fetch('/api/integrations/razorpay/payment-link');
  const j = await r.json();
  if (!r.ok) { stepOut('out-refresh', j.detail || ('HTTP ' + r.status), true); return; }
  stepOut('out-refresh', (j.links || []).map(l =>
    l.role + ' ' + l.payment_link_id + ': status ' + (l.status || '?') + ', '
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
    return res.role + ' ' + res.payment_id + ' → ' + res.outcome
      + (res.reason_code ? ' (' + res.reason_code + ')' : '')
      + (res.duplicate ? ' · DUPLICATE, no second decision' : '')
      + ' · matched by ' + (res.matcher || 'webhook')
      + ' · windows ' + (res.windows_evaluated || []).join(', ')
      + (d ? ' · ' + d.channel + ' attempts=' + d.attempts
             + (d.rolled_back ? ' ROLLED BACK' : ' delivered') : '');
  });
  (r.body.skipped || []).forEach(s => lines.push(s.role + ' ' + s.payment_link_id + ': ' + s.reason));
  stepOut('out-ingest', lines.join('\n'));
  openStream();
};

document.getElementById('btn-provider-fail').onclick = async () => {
  const r = await post('/api/integrations/razorpay/provider-failure', { mode: 'hard_down' });
  toast(r.ok ? 'provider armed (' + r.body.mode + ') — the next send fails, the grant rolls back'
             : (r.body.detail || ('HTTP ' + r.status)), !r.ok);
};

document.getElementById('btn-verify').onclick = async () => {
  const r = await fetch('/api/integrations/razorpay/verify');
  const j = await r.json();
  if (!r.ok) { stepOut('out-verify', j.detail || ('HTTP ' + r.status), true); return; }
  document.getElementById('raw').textContent = j.summary;
  stepOut('out-verify', (j.valid ? 'chain VALID · ' : 'chain INVALID · ') + j.event_count
    + ' events · genesis ' + j.genesis_ok + ' · linkage ' + j.linkage_ok
    + ' · head ' + (j.head_hash || '').slice(0, 16) + '…', !j.valid);
};

document.getElementById('btn-reset').onclick = async () => {
  if (ui.source) { ui.source.close(); ui.source = null; }
  await post('/api/integrations/razorpay/reset');
  resetView();
  ['out-session', 'out-link', 'out-contrast', 'out-refresh', 'out-ingest', 'out-verify']
    .forEach(id => { const n = document.getElementById(id); n.textContent = ''; n.className = 'step-out'; });
  toast('reset · demo schema dropped');
  refreshState();
};

function resetView() {
  auditState.events = []; auditState.seen = new Set(); auditState.lastSeq = 0;
  auditState.counts.events = 0;
  auditState.opportunities = {}; auditState.order = [];
  auditState.riskByRequest = {}; auditState.riskByGrant = {};
  const feed = document.getElementById('feed');
  feed.innerHTML = '';
  feed.className = 'feed-empty-hint';
  feed.textContent = 'Nothing yet. Start a session on the right.';
  document.querySelectorAll('#pipeline li').forEach(x => { x.className = ''; });
  document.getElementById('event-count').textContent = '0';
  document.getElementById('explanation').className = 'muted';
  document.getElementById('explanation').textContent = 'Click an event carrying a request_id.';
  document.getElementById('explain-events').textContent = '';
  const cmp = document.getElementById('comparison');
  cmp.innerHTML = 'No opportunities yet.';
  cmp.className = 'muted small';
  document.getElementById('hero').className = 'card hero-idle';
  const amount = document.getElementById('hero-amount');
  amount.className = 'idle';
  amount.textContent = 'awaiting a failed payment';
  /* Every hero field, including the small detail lines under each heading.
     Leaving those showing the previous payment's failure code after a Reset
     is a small lie about what the page currently knows. */
  const IDLE = {
    'hero-source': 'Razorpay Test Mode', 'hero-payment': 'no payment yet',
    'hero-rootcause': '—', 'hero-failcode': 'waiting for a failed payment',
    'hero-decision': '—', 'hero-reason': 'the allocator has not run',
    'hero-intervention': '—', 'hero-intervention-detail': 'no grant issued'
  };
  for (const id of Object.keys(IDLE)) document.getElementById(id).textContent = IDLE[id];
}

refreshState();
refreshHealth();
