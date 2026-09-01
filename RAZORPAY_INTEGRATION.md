# Razorpay integration — what is real, what is proposed, what is simulated

This document is the honest record of the Razorpay product layer. It is
deliberately specific about the boundary between three different things,
because the difference is the whole point:

| | Status |
|---|---|
| **Razorpay Test Mode integration** | **Real.** Real API, real test-mode payment links, real payments, real failure codes. |
| **SAMPARK's decision layer** | **Real, and unchanged.** The same committed code every Phase 4–9 result was produced by. |
| **The scale/failure simulation** | **Synthetic**, and labelled synthetic wherever it appears. |
| **Deployment inside Razorpay** | **Does not exist.** This is a proposed integration. |

Three claims this repository does **not** make: Razorpay does not use SAMPARK;
SAMPARK is not deployed in Razorpay production; no real money moves anywhere in
this project.

---

## 1. What the integration does

```
Razorpay Test Mode                     SAMPARK
──────────────────                     ───────
payment link  ─────┐
                   │
customer pays ─────┤
with a failing     │
test card          │
                   ▼
payment.failed ────────────────►  Razorpay adapter
  pay_xxx                          · verify (webhook HMAC) or read back (MCP/REST)
  amount, error_code               · normalise → RiskItem
  contact, email                   · hash contact → resolve to one CUSTOMER
                                        │
                                        ▼
                                   payment.risk_detected      ← audit event
                                        │
                                        ▼
                                   agent raises a signed request  (Ed25519)
                                        │
                                        ▼
                                   registry · capability scope
                                        │
                                        ▼
                                   hard policy · consent, DLT template,
                                        interlocks, quiet hours, contact caps
                                        │
                                        ▼
                                   score & allocate  (expected net value)
                                        │
                                        ▼
                                   reserve (SERIALIZABLE) → execute → confirm
                                        │                            └→ or rollback
                                        ▼
                                   hash-chained audit log + explanation
```

Everything below `Razorpay adapter` is code this integration did not modify.

---

## 2. Razorpay MCP Server — exactly what was available and what was used

### What was available

The Razorpay MCP Server at `https://mcp.razorpay.com/mcp` was queried
read-only (`initialize` + `tools/list`) and reported itself as
`razorpay-mcp-server 1.0.0` offering **42 tools**. The full list is whatever
`tools/list` returns at the time you ask — reproduce it yourself with:

```bash
python scripts/verify_razorpay_product_flow.py --probe
```

That command prints the live tool count and the subset SAMPARK uses. It
fabricates nothing: if the server cannot be reached it says so and reports
zero tools.

### What SAMPARK actually calls

Four of the forty-two:

| Tool | Used for | Direction |
|---|---|---|
| `create_payment_link` | creating the ₹1,000 test-mode payment link | write |
| `fetch_payment_link` | reading the link's status and its attempts | read |
| `fetch_payment` | reading the full failed-payment entity, with `error_code` | read |
| `fetch_all_payments` | the fallback matcher, via `notes.sampark_reference` | read |

`fetch_all_payment_links` is used only by the test-mode cross-check below.

### What was actually exercised, and when

- **A ₹1,000 test-mode payment link was created through the MCP Server** during
  this integration's build. `create_payment_link` returned
  `plink_TWaH0xmcDtjrxY` at `https://rzp.io/rzp/7ytIgC19`, `amount 100000`,
  `currency INR`, `status created`. The provenance recorded for it was
  `transport: mcp`, `operation: create_payment_link`,
  `detail: razorpay-mcp-server:1.0.0`.
- **The read tools** were exercised through the same client during the probe
  and the ledger cross-check.
- **`initiate_payment` (S2S) was NOT used.** Producing a *failed* payment
  requires a human at the checkout with a failing test card, and S2S is a
  gated account feature. Nothing in this repository fabricates a payment
  failure.

### What remains API/webhook-based rather than MCP

- The **webhook receiver** is not MCP and could not be: Razorpay delivers
  webhooks over plain HTTP with an HMAC signature. It is labelled
  `transport: webhook`.
- When `RAZORPAY_MCP_TOKEN` is absent, **every** operation falls back to the
  Razorpay REST test API and is labelled `transport: rest_api`, with the
  reason for the fallback shown on screen.

### Why the MCP label cannot be wrong

A `Provenance` carrying `transport: mcp` can only be built from an
`McpCallReceipt`, and that receipt is constructed in **exactly one place** in
the repository: `RazorpayMcpClient.call_tool`, after a JSON-RPC response
carrying no `error` and no `isError`.
`tests/integrations/test_provenance.py` asserts that call-site count across the
whole tree by AST, and asserts the receipt is minted *after* the `tools/call`
and *after* the error guard. A fallback to REST therefore cannot keep an MCP
label, and the frontend cannot invent one
(`tests/ui/test_product_surface.py::test_the_frontend_never_invents_a_transport_label`).

---

## 3. Test mode, and how that is checked rather than assumed

The REST path is test-mode **by construction**:
`sampark.integrations.razorpay.RazorpayConfig.from_env` refuses any key id that
does not begin `rzp_test_`, and `sampark.integrations.provenance.Environment`
has no `LIVE` member at all — a live-mode operation cannot be described by this
code.

The MCP token carries no such marker, so before any MCP **write** the gateway
runs `assert_same_test_ledger()`: it lists payment links through both
transports and requires a non-empty intersection of ids. A match proves both
credentials see one merchant ledger, and the REST side of that ledger is
test-mode by construction. If they disagree — or if the ledger is empty and
there is nothing to compare — **MCP writes are withheld** and the product falls
back to REST, labelled.

On a brand-new test account, `RAZORPAY_MCP_SKIP_LEDGER_CHECK=1` overrides this
deliberately.

---

## 4. The webhook — exactly what is validated

`POST /api/integrations/razorpay/webhook`

Razorpay signs a webhook by computing `HMAC-SHA256(raw_request_body,
webhook_secret)` and sending the lowercase hex digest in `X-Razorpay-Signature`.
That is the whole mechanism Razorpay offers and the whole mechanism implemented
here, compared with `hmac.compare_digest`.

**Verified:** the body was produced by someone holding the webhook secret, and
was not altered in transit.

**Not verified** — stated because a claim beyond this would be false:

- *who* sent it (there is no client certificate);
- *when* it was sent — Razorpay's signature covers no timestamp, so a captured
  valid body is replayable. That is why idempotency exists rather than a
  timestamp check Razorpay's format does not carry;
- that the merchant account matches.

No mechanism the real Razorpay webhook format does not support is invented.

**Failure handling**, each its own status code:

| Condition | Response |
|---|---|
| no `RAZORPAY_WEBHOOK_SECRET` configured | `401` — refused, never trusted |
| missing / malformed / wrong signature | `401` — refused, chain untouched |
| verified but not an event envelope | `422` — a format change, not an attack |
| verified event this adapter ignores | `202`, `ingested: false`, with the reason |
| verified `payment.failed`, no open session | `202`, `ingested: false` — so Razorpay's retry loop is not driven by a demo that is not listening |
| duplicate delivery | `202`, `duplicate: true` — no second decision, grant or send |

---

## 5. Idempotency — three independent layers

A redelivered Razorpay event can repeat at three different granularities, so
there are three guards, and none depends on the others:

1. **Event level** — `WebhookEnvelope.idempotency_key`, Razorpay's own
   `x-razorpay-event-id` when present, otherwise `event:entity_id`. The session
   refuses a key it has already processed.
2. **Payment level** — `RazorpayProductRun.ingest` is keyed on `payment_id` and
   returns the first outcome unchanged. This also covers a second *poll*, which
   carries no event id at all.
3. **Chain level** — `event_id = uuid5(NS_AUDIT, "payment.risk_detected:<payment_id>")`,
   so `chain.append` returns `AlreadyAppended` and the chain never records one
   payment twice, even if both guards above were bypassed.

Plus the pre-existing ones this integration reuses rather than reimplements:
`issue_grant` is idempotent on `request_id`, and the mock provider's send is
idempotent on `grant_id` — which is what makes "no double-send on retry" true.

---

## 6. The finding that shapes the demo

**A ₹1,000 failed payment is declined**, with
`allocation.negative_expected_net`. This is not a bug and nothing was tuned to
produce it. It falls out of the **frozen Phase 4 constants**:

```
expected_net = p̂ × amount − channel_cost − expected_incentive − fatigue_cost
```

- `p̂ = 0.2737` for `("failed_payment", "issuer_downtime")` at contact index 0
  (`sampark/allocator/calibrated.py`, calibrated from Arm A's seed-42 log)
- `channel_cost(sms) = 20 paise`
- `expected_incentive = 0` — `payment_retry_agent`'s scope ceiling is 0 bps
- `fatigue_cost = 54,120 paise` for a customer with nothing else open
  (`sampark/policy/soft/fatigue.py`: a 30-day horizon, λ = 0.13569 arrivals per
  customer-day, mean at-risk amount 387,607 paise)

Break-even is therefore **197,835 paise ≈ ₹1,978**. ₹1,000 is below it, so
SAMPARK declines to spend that customer's single contact slot on a recovery
worth less than the future recoveries it would push down the decay curve.

**This is the product argument, so it is the headline of the demo.** It is the
concrete answer to *"why can't Razorpay just retry everything?"*, and it is
arithmetic from committed evidence rather than a rule written for a demo.

Two consequences:

- The product page shows a second, clearly-labelled **contrast** payment above
  the break-even (default ₹4,000, configurable) so the grant → execute →
  confirm path is demonstrable at all. The ₹1,000 payment remains the subject.
- `tests/integrations/test_mcp_and_gateway.py::test_the_contrast_amount_is_separate_and_above_the_allocator_break_even`
  recomputes the break-even from the live constants, so if a protected constant
  ever moved, the suite notices before the page starts lying.

**Prioritisation is not "the bigger payment wins."** The threshold is expected
*net* value, and the fatigue term depends on what else that customer has open —
so a smaller payment from a customer with several open items scores differently
from the same amount in isolation. Amount alone never decides.

---

## 7. What the integration adds to the audit chain

Exactly **one** new event type: `payment.risk_detected`.

Everything after it — `request.received`, `request.denied_on_scope`,
`decision.denied`, `decision.deferred`, `grant.reserved`, `grant.executing`,
`grant.confirmed`, `grant.rolled_back` — is an existing type emitted by the
unmodified Phase 3/4/5 path, because a normalised Razorpay opportunity **is** a
`RiskItem` and nothing downstream knows or cares where it came from.

The new type exists so the **chain**, not the UI, is what says the money at risk
came from Razorpay. Without it the provenance would live only in `risk_items`
and the screen would be asserting something the audit log could not corroborate
— exactly the second-code-path failure spec §12.1 forbids.

It is unsigned (no agent asked for a payment to fail), carries no `request_id`
and no `window_id` (so it can never enter a reconstructed request timeline or a
contested-window summary), and carries **no raw phone number or email** — only
the hash-derived `customer_id`.

---

## 8. Privacy

`email` and `contact` are read from the Razorpay payment, hashed by the
existing `sampark.identity.resolution` (SHA-256 over a canonicalised value),
and discarded. Neither the returned object, the audit payload, the API
response, nor any log line carries a raw phone number or email address.

`sampark/integrations/**` contains no `print` and no logging call at all
(asserted by `tests/integrations/test_mcp_and_gateway.py`), because a stray
`print` in a transport is how a token reaches a terminal.

---

## 9. Identity: one human is one row, incrementally

`sampark.identity.resolution.resolve_customer_ids` deduplicates across a
**batch** of signals. A payment adapter receives one signal at a time, so the
customer id it derives is *provisional* — a pure function of that payment's own
hashes.

Left there, two payments from the same person carrying different email
addresses would mint two customers, and the unified at-risk ledger that every
contact budget and every fatigue term depends on would silently split.

`RazorpayProductRun._existing_customer_id` closes that: before writing, it
matches either hash against the ledger's own `customers` rows and adopts an
existing customer's id when one matches. Same union-on-a-shared-key rule,
evaluated against what is already known rather than against a batch, and it
never rewrites an id already in the ledger.

**This defect was found by its own test**
(`tests/demo/test_razorpay_product_flow.py::test_two_payments_from_one_person_share_one_contact_budget`)
and is recorded here rather than quietly fixed.

---

## 10. Failure handling

| Condition | Behaviour |
|---|---|
| missing credentials | reported as unconfigured; no network call attempted |
| invalid credentials | the transport's own error, surfaced; never retried as a different transport's success |
| MCP unavailable | fall back to REST, **labelled**, with the reason on screen |
| MCP refuses a tool | fall back to REST, labelled with the refusal reason |
| Razorpay API unavailable | `502` from the API, `GatewayUnavailable` internally — never a fabricated result |
| invalid webhook | `401`, chain untouched |
| duplicate webhook | `202`, `duplicate: true`, no second action |
| payment already processed | first outcome returned unchanged |
| unsupported payment state | `422` — a `captured` payment is not a recovery opportunity and is never coerced into one |
| network timeout | `RazorpayMcpUnavailable` / `RazorpayRestUnavailable`, distinct from a refusal |
| recovery action fails | grant **rolled back**, margin and contact slot released, `grant.rolled_back` on the chain, chain still verifies |

A failed Razorpay connection cannot corrupt the audit chain: nothing is
appended until the corresponding business action has already been persisted
(append-after-write), and every write goes to a throwaway schema.

---

## 11. Isolation

Every product-demo write goes to a throwaway
`sampark_demo_<unix_ts>_<16 hex>` schema created by `sampark.demo.isolation` —
the same mechanism Phase 8 uses, whose regex refuses any name it did not
produce. `public.audit_events` (the protected Phase 0–7 chain) is only ever
**read**, through `public_audit_fingerprint`.

Asserted live by
`tests/demo/test_razorpay_product_flow.py::test_the_protected_public_chain_is_untouched`
and `tests/ui/test_razorpay_api.py::test_the_protected_public_chain_is_unchanged_by_the_whole_api_flow`.

---

## 12. Configuration

| Variable | Purpose | Required |
|---|---|---|
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | test-mode REST key; `rzp_test_*` enforced | yes |
| `RAZORPAY_MCP_URL` | MCP endpoint (default `https://mcp.razorpay.com/mcp`) | no |
| `RAZORPAY_MCP_TOKEN` | MCP bearer token; absent ⇒ REST fallback, labelled | no |
| `RAZORPAY_MCP_SKIP_LEDGER_CHECK` | `1` to allow MCP writes on an empty test ledger | no |
| `RAZORPAY_WEBHOOK_SECRET` | webhook HMAC secret; absent ⇒ the webhook path is refused | no |
| `RAZORPAY_DEMO_AMOUNT_INR` | headline amount in whole rupees (default `1000`) | no |
| `RAZORPAY_CONTRAST_AMOUNT_INR` | contrast amount in whole rupees (default `4000`) | no |

`.env` is gitignored; `.env.example` is tracked and holds names with empty
values only. `tests/test_no_secrets_tracked.py` enforces both against
`git ls-files`.

---

## 13. API surface

All under `/api/integrations/razorpay`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | what is configured; `?probe=true` additionally calls the MCP server and reports its real tool list and the test-mode cross-check |
| `POST` | `/session` | open a session over a fresh isolated schema, register `payment_retry_agent` |
| `POST` | `/reset` | drop the schema |
| `GET` | `/state` | session control state: links, transport, notes |
| `POST` | `/payment-link` | create one test-mode link — `{role: "headline"\|"contrast", amount_inr?}` |
| `GET` | `/payment-link` | re-read every link in the session, with live status and attempt counts |
| `POST` | `/ingest` | poll every link for a failed payment and run each through the mediation layer |
| `POST` | `/webhook` | Razorpay webhook receiver (HMAC-verified) |
| `POST` | `/provider-failure` | arm the mock channel so the next send fails and the grant rolls back |
| `GET` | `/stream` | SSE over `audit_events` — calls `ui.sse.event_stream`, the same one query the system demo uses |
| `GET` | `/events` | the same rows, paged |
| `GET` | `/verify` | recompute every hash and check linkage |
| `GET` | `/explain/request/{id}` | `sampark.audit.explain`, reused — never a second engine |

---

## 13A. The three product surfaces

| Route | Page | What it renders |
|---|---|---|
| `/` | Overview | Static product narrative. **No system state at all** — no fetch, no SSE, no audit store, asserted by `tests/ui/test_product_surface.py::test_the_overview_renders_no_system_state`. Every figure is a committed `results/*.json` value, quoted and sourced on screen. |
| `/live` | Live Razorpay Test | The Razorpay Test Mode flow. Two case cards (₹1,000 headline, ₹4,000 contrast) rendered entirely from the audit chain, the MCP capability panel, the live pipeline and the audit trace. |
| `/system` | System Simulation | Phase 8's screen, unchanged. `app.js`, `styles.css` and `ui/sse.py` are byte-identical to the Phase 8 commit; `index.html` differs only by the shared navigation strip and a static orientation strip, and a test asserts no line was removed and that the addition contains no script, fetch or audit-store reference. |

All three carry the same `navbar.css`, which is scoped entirely to `.sk-*`
classes and declares no `:root` variables and no element selectors — because
Phase 8's `styles.css` declares custom properties with the same names as the
product design system and a fixed-height flex `body`, so a shared stylesheet
would silently re-style a page that is meant to be unchanged. That constraint
is itself a test.

---

## 14. Reproducing all of this

```bash
# read-only: what the MCP server offers, and whether it is on the same TEST ledger
python scripts/verify_razorpay_product_flow.py --probe

# create ONE ₹1,000 test-mode payment link
python scripts/verify_razorpay_product_flow.py --create-link

# ... open the short_url, pay with a FAILING Razorpay test card ...

# run that failed payment through SAMPARK against a throwaway schema
python scripts/verify_razorpay_product_flow.py --decide plink_XXXXXXXXXXXX
```

Razorpay test cards are documented at
<https://razorpay.com/docs/payments/payments/test-card-details/>. A failing
card is what produces the `payment.failed` this integration consumes; nothing
here simulates it.

The offline test suite covers everything that does not need a network:

```bash
python -m pytest tests/integrations tests/audit/test_payment_risk_detected_event.py \
                 tests/ui/test_product_surface.py tests/test_no_secrets_tracked.py -q
```
