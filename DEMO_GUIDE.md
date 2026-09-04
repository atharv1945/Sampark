# SAMPARK — demo runbook

**For someone who did not build this.** Follow it top to bottom and you will
have a working demo and enough understanding to narrate it on camera.

Nothing here requires reading the source. Where a step explains *why*, that
explanation is the thing to say out loud while recording.

---

## Contents

1. [What you are about to show](#1-what-you-are-about-to-show)
2. [One-time setup](#2-one-time-setup)
3. [Start the demo](#3-start-the-demo)
4. [Page 1 — `/` Overview](#4-page-1----overview)
5. [Page 2 — `/live` Razorpay Test (the main event)](#5-page-2--live-razorpay-test-the-main-event)
6. [Page 3 — `/system` System Simulation](#6-page-3--system-system-simulation)
7. [Optional: the rollback](#7-optional-show-the-rollback)
8. [Recording script with timings](#8-recording-script-with-timings)
9. [Answers to the questions you will be asked](#9-answers-to-the-questions-you-will-be-asked)
10. [Troubleshooting](#10-troubleshooting)
11. [Rules — do not say these things](#11-rules--do-not-say-these-things)

---

## 1. What you are about to show

One sentence:

> A Razorpay payment failed. SAMPARK detected the revenue at risk, decided
> whether recovery was worth attempting **and permitted**, chose a bounded
> intervention, and recorded exactly what happened.

The demo's punchline is **counter-intuitive and it is the whole point**:

- A **₹1,000** failed payment is **DENIED**. Recovering it is not worth what it
  costs.
- A **₹4,000** failed payment is **GRANTED**, executed, confirmed.

Same failure, same code, different economics. That is the answer to *"why can't
Razorpay just retry everything?"*

**Three pages, three questions:**

| Page | URL | Answers |
|---|---|---|
| Overview | `/` | Why should Razorpay care? |
| Live Razorpay Test | `/live` | Does this really connect to Razorpay? |
| System Simulation | `/system` | Does the engineering actually work? |

---

## 2. One-time setup

### 2.1 What you need

- **Docker Desktop** (runs PostgreSQL + Redis)
- **Python 3.11** — pinned; the test suite fails loudly on anything else
- A **Razorpay test account** (`rzp_test_` keys)
- Chrome or Edge

### 2.2 Check the repo is ready

```bash
cd "d:/Desktop Data/ML/Projects/Sampark"
.venv/Scripts/python.exe --version      # must print Python 3.11.x
```

If there is no `.venv`:

```bash
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

### 2.3 Fill in `.env`

`.env` is **gitignored** and holds real credentials. `.env.example` is the
tracked template. If `.env` does not exist, copy the template and fill it in.

The demo reads these:

| Variable | Needed for | If missing |
|---|---|---|
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | **everything** | the app refuses to start |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | `/live` | cannot create payment links |
| `RAZORPAY_MCP_TOKEN` | the MCP label | falls back to the REST test API, **labelled** |
| `RAZORPAY_WEBHOOK_SECRET` | the webhook route | that route refuses requests |
| `RAZORPAY_DEMO_AMOUNT_INR` | headline amount | defaults to `1000` |
| `RAZORPAY_CONTRAST_AMOUNT_INR` | contrast amount | defaults to `4000` |

> `RAZORPAY_KEY_ID` **must** start with `rzp_test_`. The code refuses anything
> else — a live key cannot be used even by accident.

### 2.4 Start the database and apply the schema

```bash
docker compose up -d
```

Wait until it reports healthy (10–20 seconds). The schema is applied **once**,
by hand — never automatically:

```bash
psql "postgresql://USER:PASSWORD@localhost:PORT/DB" -f sampark/schema.sql
```

If `public.audit_events` already exists, this is already done. Skip it.

### 2.5 Preflight

```bash
.venv/Scripts/python.exe scripts/run_demo.py --check
```

You want to see:

```
  ok   PostgreSQL   reachable · protected chain has 560 events
  ok   Razorpay     rzp_test_ key present (test mode enforced in code)
  ok   Razorpay MCP razorpay-mcp-server 1.0.0 · 42 tools offered
  ok   Test-mode    cross-check verified — MCP is on the same test ledger…
  ok   Webhook      secret set — POST /webhook will verify signatures
  ok   Amounts      headline Rs 1,000 · contrast Rs 4,000
```

`warn` lines are fine — the demo still runs, it just says on screen that it fell
back. A `FAIL` line tells you exactly what to fix.

---

## 3. Start the demo

```bash
.venv/Scripts/python.exe scripts/run_demo.py
```

It prints the preflight, then the three URLs, then serves on port 8000.

> **Why not plain `uvicorn ui.app:app`?** That works too, but only if you have
> already exported the environment variables in your shell. The app reads the
> process environment, not `.env`, so a bare `uvicorn` dies at startup with a
> `RuntimeError` about missing `POSTGRES_*`. `run_demo.py` loads `.env` for you.

Leave this terminal running. Open <http://127.0.0.1:8000>.

---

## 4. Page 1 — `/` Overview

**Nothing to click.** This page renders **no system state at all** — no network
calls, no live data. Every number on it is a committed result file, quoted and
sourced on screen. Scroll through it.

**What to point at, in order:**

| Section | What it is | Say this |
|---|---|---|
| **Hero** — "Recover smarter. Not harder." | The product in one sentence, plus the flow diagram | "SAMPARK is a decision layer for failed payments." |
| **The problem** | The five things a recovery attempt costs | "Retrying isn't free. It spends a contact, agent capacity, discount budget, and the customer's patience." |
| **Why not just retry?** | Naive flow vs SAMPARK flow, side by side | "A naive system treats every failure identically." |
| **The two cases** | ₹1,000 DENIED / ₹4,000 GRANTED, with the break-even scale | "Break-even is about ₹1,978. The ₹1,000 payment is below it." |
| **Where it fits** | Razorpay provides / SAMPARK adds | "SAMPARK doesn't replace anything. It adds a decision layer." |
| **Agents** | Identity → scope → limits → allocation | "Agents are automated workers. Several can want the same customer." |
| **Evidence** | −48.5 % contacts · −8.2 % revenue · 1.687× per contact · 0.00 % ML | **Read the −8.2 % out loud.** |
| **Honesty** | The four provenance labels + limitations | "Everything on these pages is labelled real, simulated, or not demonstrated." |

**The most important thing on this page** is the evidence row. Say it plainly:

> "We recovered **less** total money — 8.2 % less. We did it with half the
> contacts, so revenue per contact went up 1.69×. And the ML models contributed
> exactly zero, because the uplift model is honestly unavailable on this data.
> We're reporting that against ourselves."

That is the single most credible thing in the whole demo. Do not skip it.

---

## 5. Page 2 — `/live` Razorpay Test (the main event)

Click **Live Razorpay Test** in the top navigation.

### What you are looking at, before touching anything

**Top strip — three status lights:**

| Light | Meaning |
|---|---|
| **● TEST MODE** | Always on. No real money can move. |
| **● MCP CONNECTED** | Green = the Razorpay MCP Server answered. Amber = unreachable, REST fallback will be used and labelled. |
| **● SAMPARK IDLE / ONLINE** | Whether a session is open. |

**Right rail — "Razorpay MCP Server" panel.** Shows the server name, version,
tool count (42), environment, preferred transport, and the **test-mode
cross-check**. The tick list is the five tools SAMPARK uses. A tick means the
server *offers* that tool — not that it has been called.

**Bottom right — "What is real on this page".** Five labels. Learn these; a
judge will ask.

**Left — two case cards**, both reading *awaiting decision*. They will fill in
from the audit log.

### The six steps

Work down the right rail. Each has a button.

---

#### Step 1 — Click **Start**

**What it does:** creates a throwaway PostgreSQL schema and registers the
recovery agent (`payment_retry_agent`) with its Ed25519 key.

**What you see:** the SAMPARK light turns green; the output shows a session id
and a schema name like `sampark_demo_1788…`.

**Say:** "This opens an isolated database schema. The protected audit chain —
560 events from earlier phases — is never written to."

> You will also see `scorer HeuristicScorer — the uplift model is unavailable on
> this data, and that is logged`. That is deliberate honesty, not an error.

---

#### Step 2 — Click **Create** (the ₹1,000 payment)

**What it does:** creates a **real payment link in your Razorpay test account**,
preferring the Razorpay MCP Server.

**What you see:** a `plink_…` id, the amount, `via Razorpay MCP Server`, and a
link — **Open Razorpay Test Checkout ↗**.

**Say:** "That's a real Razorpay test-mode payment link, created through the
Razorpay MCP Server. SMS and email notifications are switched off — this demo
never contacts a real person."

> If it says *via Razorpay Test API* instead, MCP was unavailable and the
> fallback reason is printed. That is the system being honest. Do not claim MCP.

---

#### Step 3 — Click **Create** (the contrast payment)

Leave the amount box at **4000**.

**What it does:** the same thing at a higher amount.

**Say:** "A second real payment, above the economic threshold. This is what
makes the comparison visible."

---

#### Step 4 — Make them fail (**this is the manual part**)

For **each** of the two links:

1. Click **Open Razorpay Test Checkout ↗** — a real Razorpay page opens.
2. Enter any email/phone when asked.
3. Choose **Card**.
4. Enter a Razorpay **test** card:
   - Failing card: **`4100 2800 0009 0000`**
   - Any future expiry, any CVV
5. Razorpay shows a **simulator screen with Success / Failure buttons** —
   click **Failure**.

> Reference: <https://razorpay.com/docs/payments/payments/test-card-details/>
> The simplest reliable route is: any test card → click **Failure** on the
> simulator screen. Razorpay produces the failure; nothing in SAMPARK fakes it.

Back on the SAMPARK page, click **Check status**. You should see
`2 attempt(s), 2 failed` across the two links.

**Say:** "Both payments failed in Razorpay's test environment. SAMPARK reads the
real error code back — it doesn't invent one."

---

#### Step 5 — Click **Detect & decide** ← **the moment**

**What it does:** reads each failed payment back from Razorpay, normalises it
into a risk item, resolves the customer from hashed phone/email, and runs the
**unmodified** decision path: scope → policy → scoring → allocation → grant
issuance → execution.

**What you see:**

- The **left pipeline** lights up stage by stage.
- The **two case cards** resolve:

| | Case A | Case B |
|---|---|---|
| Amount | ₹1,000.00 | ₹4,000.00 |
| Verdict | **DENIED** (red) | **GRANTED** (green) |
| Reason | `allocation.negative_expected_net` | clears the economic bar |
| Expected net | **−₹267.74** | positive |
| Recovery | Not attempted | Executed → CONFIRMED |

- The **audit trace** fills with ~10 events.

**Say — this is the key narration:**

> "Same failure code. Same code path. Different answer.
>
> The ₹1,000 recovery was **declined** — expected net **minus ₹267**. Spending
> this customer's one available contact on it would cost more in future
> recoveries than it could return. So SAMPARK sends nothing and spends no budget.
>
> The ₹4,000 one clears the bar, so it's granted, executed and confirmed.
>
> SAMPARK is not picking the bigger payment. It's comparing expected **net**
> value against what a contact costs the future."

---

#### Step 6 — Click **Verify chain**

**What it does:** recomputes every hash and checks the linkage.

**What you see:** `chain VALID · 10 events · genesis true · linkage true · head …`

**Then click any row in the audit trace.** The raw event appears on the right,
and below it a plain-English explanation generated from the log — no LLM.

**Say:** "Every decision is a hash-chained event. You can verify it, and you can
ask the log why any single request ended the way it did."

Click **Reset** when you are done. It drops the demo schema.

---

## 6. Page 3 — `/system` System Simulation

Click **System Simulation** in the navigation.

**First, read the purple strip:** *SYNTHETIC SIMULATION — no Razorpay data.*
Say that out loud. This page is SAMPARK's own seeded world.

**Why it exists:** one payment shows a decision. It cannot show *contention* —
four authorised agents wanting the same customer in the same window — which is
the actual problem.

### Steps

1. Click **Run replay**. A deterministic ~40-second replay starts.
2. **Watch the DENIALS panel** — it is deliberately the loudest thing on screen.
3. **Watch COMPLIANCE HELD** at the bottom left: quiet-hour violations,
   contact-cap breaches, scope violations — all **0**, and they stay 0.
4. Optionally **Fire** a chaos control from the right panel (7 available:
   kill the model, revoke an agent key, force a provider timeout, …).
5. Click **Verify chain** at the end.

**What the replay demonstrates:** a provider timeout and rollback · a rogue
agent denied on scope, then struck, then revoked · the model killed mid-run with
a logged fallback · compliance holding at zero throughout.

**Say:** "This is synthetic — the customers and amounts come from our committed
seeded generator, not from Razorpay. It's where the failure and recovery
mechanisms are demonstrated at scale. Recovery drops; compliance does not."

---

## 7. Optional: show the rollback

On `/live`, **before** step 5, click **Force provider timeout**.

Then run **Detect & decide**. The granted payment will now show
**ROLLED BACK** instead of CONFIRMED, and `grant.rolled_back` appears in the
trace.

**Say:** "The channel failed. The grant rolled back, the budget and the
customer's contact slot were both released, and the retry is idempotent — no
double-send. Then verify the chain: still valid."

---

## 8. Recording script with timings

Target ~4 minutes. Rehearse once with everything already set up, then record.

| Time | Page | Action | Line to say |
|---|---|---|---|
| 0:00–0:25 | `/` | Hero + the problem | "A failed payment isn't automatically worth chasing. Every retry spends a contact, budget, and the customer's patience." |
| 0:25–0:50 | `/` | Two cases + break-even | "₹1,000 gets declined. ₹4,000 gets funded. Break-even is about ₹1,978." |
| 0:50–1:05 | `/` | Evidence row | "We recovered 8.2 % **less** money, with half the contacts. ML contributed zero. We report that against ourselves." |
| 1:05–1:20 | `/live` | Status lights + MCP panel | "Test mode. Connected to the Razorpay MCP Server — 42 tools, test-mode verified." |
| 1:20–1:45 | `/live` | Start, Create ×2 | "Two real Razorpay test payment links, created through MCP." |
| 1:45–2:15 | Razorpay | Pay both with a failing test card | "This is the real Razorpay test checkout. I'm choosing Failure." |
| 2:15–2:50 | `/live` | **Detect & decide** | The key narration from step 5. |
| 2:50–3:05 | `/live` | Verify chain + click a row | "Hash-chained, verifiable, and it explains itself." |
| 3:05–3:45 | `/system` | Run replay | "Synthetic — this is where contention, rollback, a rogue agent and a model failure are shown at scale." |
| 3:45–4:00 | `/` | Close on the boundary | "Razorpay handles the payment. SAMPARK handles the recovery decision." |

**Recording tips**

- Record at **1920×1080**, browser maximised, zoom at 100 %.
- Do steps 1–3 **before** recording if you want a tighter video — then the
  recording starts with links already created.
- Have both Razorpay checkout tabs open in advance.
- Hide bookmarks and notifications.
- If a take goes wrong, click **Reset** and start from step 1.

---

## 9. Answers to the questions you will be asked

**"Is this actually connected to Razorpay?"**
Yes. The payment links, payments, failures and error codes all come from
Razorpay's test environment. Point at the MCP panel — server name, version, 42
tools, test-mode cross-check verified.

**"Is this real money?"**
No. Razorpay **Test Mode** only. The code refuses any key that isn't
`rzp_test_`, and there is no live-mode option in the codebase at all.

**"Why did it refuse the ₹1,000?"**
Because expected net value is negative — minus ₹267. Contacting this customer
costs about ₹541 in future recovery value, so a failed payment only clears
break-even above roughly ₹1,978. Nothing was tuned to produce that.

**"So it just picks bigger payments?"**
No. It compares expected **net** value, and the cost side depends on what else
that customer already has open. A smaller payment from a customer with several
open items can outrank a larger isolated one.

**"Does it recover more money?"**
**No** — 8.2 % less in our experiment. It recovers with about half the contacts,
so revenue per contact is 1.69× higher. It optimises efficiency, not volume.

**"Where's the AI?"**
There are model seams, and they are tested. On this data the uplift model is
**unavailable** — there's no untreated control group to learn from — so its
contribution is exactly 0.00 %. The advantage comes from selection and
allocation. The system logs a degradation event and falls back to the frozen
heuristic rather than pretending.

**"Can you prove what happened?"**
Yes — click **Verify chain**. Every decision is a hash-chained event; the hash
is recomputed from the event, never read from a stored column.

**"What about the webhook?"**
Implemented and tested against genuine HMAC-SHA256 signatures. Razorpay has
never delivered to it over the internet, because a local demo has no public URL.
It is labelled **Architectural capability** on screen, not "demonstrated".

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: SAMPARK demo needs Postgres env vars` | you ran bare `uvicorn` without exporting env | use `python scripts/run_demo.py` |
| Preflight: `PostgreSQL configured, but not reachable` | Docker Desktop is not running | start Docker Desktop, wait for the whale icon to settle, then `docker compose up -d` |
| `failed to connect to the docker API … dockerDesktopLinuxEngine` | same | as above |
| Preflight: `the schema is not applied` | fresh database | `psql … -f sampark/schema.sql` |
| MCP light amber, "REST fallback" | `RAZORPAY_MCP_TOKEN` missing or the server is down | fine — the page says so. Add the token to `.env` to use MCP. |
| Test-mode cross-check "NOT verified" | MCP and REST keys are on different accounts, or the test ledger is empty | create one link first, or set `RAZORPAY_MCP_SKIP_LEDGER_CHECK=1` deliberately |
| **Detect & decide** says "no failed payment attempt observed" | nobody has paid yet, or the payment succeeded | reopen the checkout and choose **Failure** |
| Both cards say *awaiting decision* after a reload | the page reopens the stream automatically; if not, click **Detect & decide** again | harmless — the decision is already in the chain |
| A stray row in `public.budget_windows` after killing the test suite | a background thread outlived a `SIGKILL`ed pytest | inert; delete it, or ignore |
| Low disk on C: | Docker becomes unstable below ~5 GB | free space before long test runs |

---

## 11. Rules — do not say these things

The UI is careful about this and there are tests enforcing it. Keep your
narration consistent with the screen.

**Never say:**

- ❌ "Razorpay uses SAMPARK" — they do not.
- ❌ "This is deployed in production" — it is not.
- ❌ "SAMPARK recovers more revenue" — it recovers **less**, and that is in the UI.
- ❌ "Our ML model improves recovery" — it contributed **0.00 %**.
- ❌ "Razorpay doesn't have anything like this" — we make no claim about their
  internal systems.
- ❌ "This is real money" — Test Mode only.
- ❌ Calling the `/system` replay real Razorpay data — it is synthetic.

**Say instead:**

- ✅ "a **proposed** integration"
- ✅ "**Razorpay Test Mode**"
- ✅ "**prototype**"
- ✅ "**synthetic simulation**" (for `/system`)
- ✅ "Razorpay handles the payment. SAMPARK handles the recovery decision."

---

## Appendix — running it without a browser

Useful for checking the Razorpay side quickly, or if the UI is misbehaving.

```bash
# read-only: what the MCP server offers + test-mode cross-check
.venv/Scripts/python.exe scripts/verify_razorpay_product_flow.py --probe

# create one ₹1,000 test-mode link (prints the checkout URL)
.venv/Scripts/python.exe scripts/verify_razorpay_product_flow.py --create-link

# ...pay it with a failing test card, then:
.venv/Scripts/python.exe scripts/verify_razorpay_product_flow.py --decide plink_XXXXXXXX

# every link on the account and how many attempts failed
.venv/Scripts/python.exe scripts/verify_razorpay_product_flow.py --status
```

These make **real** calls to Razorpay's test API and are deliberately not part
of the test suite.

**Confidence checks:**

```bash
.venv/Scripts/python.exe -m sampark.audit.verify   # → VALID: True, 560 events
.venv/Scripts/python.exe -X utf8 -m sim.gate       # → GATE: PASS
```

Both read committed evidence and change nothing.
