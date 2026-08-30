/* SAMPARK Phase 8 frontend — spec §12.1, §12.2.
 *
 * THE TRACE-INTEGRITY RULE, implemented rather than asserted:
 *
 *   "The UI renders the audit log and nothing else."
 *
 * There are exactly THREE state stores in this file, and they are never
 * merged:
 *
 *   auditState        SYSTEM TRUTH. Written by ingestAuditEvents() and by
 *                     nothing else, ever. ingestAuditEvents() is called from
 *                     exactly two places — onSseMessage() (the /api/stream
 *                     SSE handler) and backfillFrom() (the /api/events gap
 *                     repair). Both carry nothing but rows out of the
 *                     audit_events table.
 *
 *   controlState      DEMO CONTROL STATE. Run status, chaos arming, the
 *                     compression ratio. Real, but not system truth, so it
 *                     renders only in the header and the chaos panel — both
 *                     visually marked — and never contributes a trace node,
 *                     a colour, or a counter.
 *
 *   ui                Pure presentation: selection, scroll. Touches nothing.
 *
 * There is no emit_demo_event(), no websocket, and no second channel of any
 * kind. tests/test_ui_renders_only_audit_events.py enforces all of the above
 * statically, so a later change that blurs the line fails the suite.
 */

'use strict';

/* ===================== SYSTEM TRUTH (audit only) ===================== */

const auditState = {
  events: [],
  seen: new Set(),     // event_id -> de-duplication (uuid5, stable across replays)
  lastSeq: 0,          // transport cursor ONLY; never logical identity
  counts: { events: 0, requests: 0, granted: 0, denied: 0, deferred: 0,
            rolled: 0, strikes: 0, revoked: 0,
            /* Compliance counters. Spec §12.3: "Recovery drops; compliance
             * does not. That distinction is the whole design philosophy."
             * Derived ENTIRELY from fields already on the streamed audit
             * events (grant.reserved.send_after / customer_id / window_id,
             * request.denied_on_scope.agent_id), so they remain audit-derived
             * system truth and stay inside auditState. Nothing is fetched or
             * assumed to produce them. */
            quiet_violations: 0, cap_breaches: 0, honest_scope_violations: 0 }
};

/* The agent whose misbehaviour the demo scripts. Every OTHER agent is a
 * well-behaved one, so a scope denial attributed to any of them would be a
 * real compliance failure rather than the demonstration working. */
const ROGUE_AGENT_ID = 'third_party_recovery_agent';

/* TCCCPR 2018 blackout, 21:00-09:00 IST. Audit timestamps are canonical UTC
 * (sampark.audit.canonical.iso_utc_micros), so shift by +05:30 to read the
 * IST hour the policy is actually written against. */
function isQuietHoursIST(isoUtc) {
  const ist = new Date(new Date(isoUtc).getTime() + (5 * 60 + 30) * 60000);
  const h = ist.getUTCHours();
  return h >= 21 || h < 9;
}

/* One capacity-consuming grant per customer per window is the Phase 4 cap.
 * A second reservation for the same pair would be a real breach. */
const grantedSlots = new Set();

/* ================= DEMO CONTROL STATE (marked in the UI) ============= */

const controlState = { state: 'idle', runId: null, seed: null, badge: null, chaos: [] };

/* ========================== PRESENTATION ============================= */

const ui = { selected: null, source: null };

/* ===================================================================== */

const NODE_FOR_TYPE = {
  'request.received': 'request',
  'request.denied_on_scope': 'scope',
  'decision.denied': 'policy',
  'decision.deferred': 'policy',
  'grant.reserved': 'reserve',
  'grant.executing': 'execute',
  'grant.confirmed': 'settle',
  'grant.rolled_back': 'settle',
  'grant.expired': 'settle',
  'agent.registered': 'registry',
  'agent.struck': 'registry',
  'agent.revoked': 'registry',
  'model.degraded': 'registry',
  'contact.opt_out': 'settle'
};

/* Four states, one meaning each (spec §12.2). Derived ONLY from the event's
 * own type and reason_code — never from anything the UI decided. */
function classify(ev) {
  const t = ev.event_type, rc = ev.reason_code || '';
  if (t === 'request.denied_on_scope') return 'scope';
  if (t === 'grant.rolled_back') return 'rolled';
  if (t === 'grant.reserved' || t === 'grant.confirmed') return 'granted';
  if (t === 'decision.denied' || t === 'decision.deferred') {
    return rc.startsWith('scope.') ? 'scope' : 'policy';
  }
  if (t === 'agent.struck' || t === 'agent.revoked') return 'policy';
  return 'pending';
}

function isDenial(ev) {
  const k = classify(ev);
  return k === 'scope' || k === 'policy' || k === 'rolled';
}

/* ---------- the ONLY writer of auditState ---------- */

function ingestAuditEvents(rows) {
  for (const ev of rows) {
    if (auditState.seen.has(ev.event_id)) continue;   // duplicate SSE delivery
    auditState.seen.add(ev.event_id);
    auditState.events.push(ev);
    if (ev.seq > auditState.lastSeq) auditState.lastSeq = ev.seq;
    tally(ev);
    renderEventRow(ev);
    litNode(ev);
  }
  renderMetrics();
}

function tally(ev) {
  const c = auditState.counts;
  c.events += 1;
  if (ev.event_type === 'request.received') c.requests += 1;
  if (ev.event_type === 'grant.reserved') c.granted += 1;
  if (ev.event_type === 'decision.denied' || ev.event_type === 'request.denied_on_scope') c.denied += 1;
  if (ev.event_type === 'decision.deferred') c.deferred += 1;
  if (ev.event_type === 'grant.rolled_back') c.rolled += 1;
  if (ev.event_type === 'agent.struck') c.strikes += 1;
  if (ev.event_type === 'agent.revoked') c.revoked += 1;

  if (ev.event_type === 'grant.reserved') {
    if (isQuietHoursIST(ev.payload.send_after)) c.quiet_violations += 1;
    const slot = ev.payload.customer_id + '|' + ev.payload.window_id;
    if (grantedSlots.has(slot)) c.cap_breaches += 1;
    grantedSlots.add(slot);
  }
  if (ev.event_type === 'request.denied_on_scope' && ev.payload.agent_id !== ROGUE_AGENT_ID) {
    c.honest_scope_violations += 1;
  }
}

/* ---------- rendering ---------- */

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function renderEventRow(ev) {
  const kind = classify(ev);
  const row = el('div', 'ev ' + kind);
  row.appendChild(el('span', 'seq', '#' + ev.seq));
  row.appendChild(el('span', 'ty', ev.event_type));
  row.appendChild(el('span', 'rc', ev.reason_code || ''));
  row.onclick = () => select(ev, row);

  const feed = document.getElementById('feed');
  feed.appendChild(row);
  feed.scrollTop = feed.scrollHeight;

  if (isDenial(ev)) {
    const loud = el('div', 'ev ' + kind);
    loud.appendChild(el('span', 'seq', '#' + ev.seq));
    loud.appendChild(el('span', 'ty', ev.event_type));
    loud.appendChild(el('span', 'rc', ev.reason_code || ''));
    loud.onclick = () => select(ev, loud);
    const d = document.getElementById('denials');
    d.appendChild(loud);
    d.scrollTop = d.scrollHeight;
    document.getElementById('denial-count').textContent =
      auditState.counts.denied + auditState.counts.deferred + auditState.counts.rolled;
  }
}

let litTimer = null;
function litNode(ev) {
  const node = NODE_FOR_TYPE[ev.event_type];
  if (!node) return;
  const li = document.querySelector('#pipeline li[data-node="' + node + '"]');
  if (!li) return;
  li.className = 'lit lit-' + classify(ev);
  clearTimeout(litTimer);
  litTimer = setTimeout(() => {
    document.querySelectorAll('#pipeline li').forEach(x => { x.className = ''; });
  }, 1400);
}

function renderMetrics() {
  const c = auditState.counts;
  for (const k of Object.keys(c)) {
    const n = document.getElementById('m-' + k);
    if (n) n.textContent = c[k];
  }
  // A compliance counter is only reassuring if a NON-zero value would be
  // impossible to miss. Zero stays quiet; anything else turns red.
  for (const k of ['quiet_violations', 'cap_breaches', 'honest_scope_violations']) {
    const cell = document.getElementById('m-' + k);
    if (cell && cell.parentElement) {
      cell.parentElement.className = c[k] === 0 ? 'ok' : 'bad';
    }
  }
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

/* ---------- explainability: reuses sampark.audit.explain via the API ---- */

async function explainRequest(requestId) {
  const box = document.getElementById('explanation');
  box.className = 'muted';
  box.textContent = 'explaining ' + requestId + '...';
  try {
    const r = await fetch('/api/explain/request/' + requestId);
    const j = await r.json();
    if (!r.ok) { box.textContent = j.detail || ('HTTP ' + r.status); return; }
    box.className = '';
    box.textContent = j.explanation;
    // The raw events the sentence was derived FROM, so it can be checked
    // against the record rather than trusted.
    document.getElementById('explain-events').textContent =
      j.events.map(e => '#' + e.seq + '  ' + e.event_type + '  ' + (e.reason_code || '')).join('\n');
  } catch (e) { box.textContent = String(e); }
}

/* ---------- the SSE stream: the ONLY source of trace data ---------- */

function onSseMessage(msg) {
  let ev;
  try { ev = JSON.parse(msg.data); } catch (e) { return; }
  // Gap detection: seq is contiguous within one chain. A hole means frames
  // were missed, so repair from /api/events BEFORE rendering further rather
  // than silently drawing an incomplete trace.
  if (auditState.lastSeq && ev.seq > auditState.lastSeq + 1) {
    backfillFrom(auditState.lastSeq, ev);
    return;
  }
  ingestAuditEvents([ev]);
}

async function backfillFrom(afterSeq, pendingEvent) {
  try {
    const r = await fetch('/api/events?after_seq=' + afterSeq + '&limit=1000');
    if (r.ok) ingestAuditEvents(await r.json());
  } catch (e) { /* the next frame will retry the repair */ }
  if (pendingEvent) ingestAuditEvents([pendingEvent]);
}

function openStream() {
  if (ui.source) ui.source.close();
  const src = new EventSource('/api/stream?after_seq=' + auditState.lastSeq);
  ui.source = src;
  src.addEventListener('audit', onSseMessage);
  src.addEventListener('end', () => { src.close(); ui.source = null; refreshStatus(); });
  src.onerror = () => { /* EventSource reconnects on its own, resending Last-Event-ID */ };
}

/* ---------- demo control state (never merged into the trace) ---------- */

async function refreshStatus() {
  try {
    const s = await (await fetch('/api/status')).json();
    controlState.state = s.state || 'idle';
    controlState.runId = s.run_id;
    controlState.seed = s.seed;
    controlState.badge = s.badge_text;
    document.getElementById('badge-state').textContent =
      (s.state || 'idle') + (s.current_window ? ' · ' + s.current_window : '');
    if (s.seed !== undefined && s.seed !== null) {
      document.getElementById('badge-seed').textContent = 'seed ' + s.seed;
    }
    if (s.badge_text) document.getElementById('badge-time').textContent = s.badge_text;
  } catch (e) { /* leave the last known control state */ }
}

async function refreshChaos() {
  try {
    controlState.chaos = await (await fetch('/api/chaos')).json();
    renderChaos();
  } catch (e) { /* ignore */ }
}

function renderChaos() {
  const box = document.getElementById('chaos-list');
  box.innerHTML = '';
  controlState.chaos.forEach((c, i) => {
    const row = el('div', 'chaos' + (c.fired_count ? ' fired' : ''));
    const left = el('div');
    left.appendChild(el('span', 'nm', (i + 1) + '. ' + c.spec_name));
    left.appendChild(el('span', 'fx', c.exercises + (c.fired_count ? '  ·  fired ×' + c.fired_count : '')));
    if (c.last_effect) left.appendChild(el('span', 'fx', '→ ' + c.last_effect));
    if (c.spec_note) left.appendChild(el('span', 'note', '⚠ ' + c.spec_note));
    row.appendChild(left);
    const b = el('button', null, 'Fire');
    b.onclick = () => fireChaos(c.control_id);
    row.appendChild(b);
    box.appendChild(row);
  });
}

async function fireChaos(id) {
  try {
    const r = await fetch('/api/chaos/' + id, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    });
    const j = await r.json();
    toast(r.ok ? j.effect : ('409 · ' + (j.detail || 'not applicable now')), !r.ok);
  } catch (e) { toast(String(e), true); }
  refreshChaos();
}

function toast(msg, isErr) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'show' + (isErr ? ' err' : '');
  setTimeout(() => { t.className = ''; }, 5200);
}

/* ---------- buttons ---------- */

document.getElementById('btn-run').onclick = async () => {
  const btn = document.getElementById('btn-run');
  btn.disabled = true;
  try {
    const r = await fetch('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    });
    const j = await r.json();
    if (!r.ok) { toast(j.detail || ('HTTP ' + r.status), true); btn.disabled = false; return; }
    resetView();
    document.getElementById('badge-time').textContent = j.badge_text;
    document.getElementById('badge-seed').textContent = 'seed ' + j.seed;
    toast('replay started · ' + j.window_count + ' windows · ' + j.demo_schema);
    openStream();
    refreshChaos();
  } catch (e) { toast(String(e), true); btn.disabled = false; }
};

document.getElementById('btn-reset').onclick = async () => {
  if (ui.source) { ui.source.close(); ui.source = null; }
  await fetch('/api/reset', { method: 'POST' });
  resetView();
  document.getElementById('btn-run').disabled = false;
  toast('reset · demo schema dropped');
  refreshStatus(); refreshChaos();
};

document.getElementById('btn-verify').onclick = async () => {
  const r = await fetch('/api/verify');
  const j = await r.json();
  if (!r.ok) { toast(j.detail || ('HTTP ' + r.status), true); return; }
  document.getElementById('raw').textContent = j.summary;
  toast(j.valid ? ('chain VALID · ' + j.event_count + ' events · head ' + j.head_hash.slice(0, 16) + '…')
                : 'chain INVALID', !j.valid);
};

document.getElementById('btn-export').onclick = () => { window.location = '/api/export'; };

function resetView() {
  auditState.events = []; auditState.seen = new Set(); auditState.lastSeq = 0;
  grantedSlots.clear();
  for (const k of Object.keys(auditState.counts)) auditState.counts[k] = 0;
  document.getElementById('feed').innerHTML = '';
  document.getElementById('denials').innerHTML = '';
  document.getElementById('denial-count').textContent = '0';
  document.getElementById('raw').textContent = 'Select an event on the left.';
  document.getElementById('explanation').className = 'muted';
  document.getElementById('explanation').textContent = 'Click an event with a request_id.';
  document.getElementById('explain-events').textContent = '';
  renderMetrics();
}

refreshStatus();
refreshChaos();
setInterval(refreshStatus, 1500);
