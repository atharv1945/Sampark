"""Phase-4-local reason-code vocabulary — Design Lock §15.

Namespaced strings, not a closed global enum — consistent with
CONTRACTS.md's GrantDecision.reason_code (`str | None`, no ReasonCode
vocabulary approved yet) and with Phase 3's nine `scope.*` codes
(sampark/registry/reason_codes.py), which this module does not
duplicate or re-declare.

`FACT_UNAVAILABLE_*` are METRIC KEYS, not decision reason codes. An
unevaluated rule is not a denial — it never appears in a
GrantDecision.reason_code. They are listed here only so the metrics
layer has one place to import them from.
"""

from __future__ import annotations

# policy.* — hard regulatory / policy filters
OPT_OUT_ACTIVE = "policy.opt_out_active"  # DENY
CONSENT_SCOPE_MISSING = "policy.consent_scope_missing"  # DENY
CONSENT_SCOPE_EXPIRED = "policy.consent_scope_expired"  # DENY
QUIET_HOURS = "policy.quiet_hours"  # DEFER
DLT_TEMPLATE_UNAVAILABLE = "policy.dlt_template_unavailable"  # DENY

# budget.* — shared scarce resources
CONTACT_CAP_24H = "budget.contact_cap_24h"  # DEFER
CONTACT_CAP_7D = "budget.contact_cap_7d"  # DEFER
CONTACT_SLOT_TAKEN = "budget.contact_slot_taken"  # DEFER
MERCHANT_MARGIN_EXHAUSTED = "budget.merchant_margin_exhausted"  # DEFER
CUSTOMER_MARGIN_EXHAUSTED = "budget.customer_margin_exhausted"  # DEFER

# interlock.* — mutually exclusive cross-agent states
INTERLOCK_DISPUTE_OPEN = "interlock.dispute_open"  # DENY
INTERLOCK_ACTIVE_GRANT_IN_WINDOW = "interlock.active_grant_in_window"  # DEFER

# allocation.* — comparative outcomes
NEGATIVE_EXPECTED_NET = "allocation.negative_expected_net"  # DENY
LOST_TO_HIGHER_EXPECTED_NET = "allocation.lost_to_higher_expected_net"  # DEFER
DEFERRAL_EXHAUSTED = "allocation.deferral_exhausted"  # DENY

# fact_unavailable.* — METRIC KEYS ONLY. Never a GrantDecision.reason_code.
FACT_UNAVAILABLE_CONSENT_SCOPE = "fact_unavailable.consent_scope"
FACT_UNAVAILABLE_RTO_FLAG = "fact_unavailable.rto_flag"
FACT_UNAVAILABLE_REFUND_IN_FLIGHT = "fact_unavailable.refund_in_flight"
FACT_UNAVAILABLE_FRAUD_REVIEW = "fact_unavailable.fraud_review"
FACT_UNAVAILABLE_MANDATE_CANCELLATION = "fact_unavailable.mandate_cancellation"

# Reason codes that DEFER (require next_eligible_at != None).
DEFER_REASON_CODES = frozenset(
    {
        QUIET_HOURS,
        CONTACT_CAP_24H,
        CONTACT_CAP_7D,
        CONTACT_SLOT_TAKEN,
        MERCHANT_MARGIN_EXHAUSTED,
        CUSTOMER_MARGIN_EXHAUSTED,
        INTERLOCK_ACTIVE_GRANT_IN_WINDOW,
        LOST_TO_HIGHER_EXPECTED_NET,
    }
)

# Reason codes that DENY (next_eligible_at must be None).
DENY_REASON_CODES = frozenset(
    {
        OPT_OUT_ACTIVE,
        CONSENT_SCOPE_MISSING,
        CONSENT_SCOPE_EXPIRED,
        DLT_TEMPLATE_UNAVAILABLE,
        INTERLOCK_DISPUTE_OPEN,
        NEGATIVE_EXPECTED_NET,
        DEFERRAL_EXHAUSTED,
    }
)

FACT_UNAVAILABLE_METRIC_KEYS = frozenset(
    {
        FACT_UNAVAILABLE_CONSENT_SCOPE,
        FACT_UNAVAILABLE_RTO_FLAG,
        FACT_UNAVAILABLE_REFUND_IN_FLIGHT,
        FACT_UNAVAILABLE_FRAUD_REVIEW,
        FACT_UNAVAILABLE_MANDATE_CANCELLATION,
    }
)
