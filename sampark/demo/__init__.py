"""SAMPARK Phase 8 — the demo surface's BACKEND capability (spec §12).

This package holds everything the live demo needs that is not HTTP: schema
isolation, the deterministic scenario, the simulated clock, the fake
provider, the stage-two rate-ceiling enforcement, the killable scorer, the
chaos controls, and the runner that drives them. `ui/` is a thin transport
shell over this package and owns no business logic of its own — delete
`ui/` and every behaviour Phase 8 claims is still here, and still tested.

**Nothing in this package re-implements a decision.** The runner calls
Phase 3's `evaluate_scope`, Phase 4's `mediate_window` /
`filter_and_allocate` / `allocate_window` / `issue_grant`, and Phase 4's
`execute_grant` / `confirm_grant` / `rollback_grant`, all unmodified. The
genuinely new logic here is exactly the three things the repository did not
have and spec §12.3 requires:

    provider.py     a channel provider that can fail (the Phase 2 mock
                    always succeeded, so no failure path existed at all)
    enforcement.py  the STAGE-TWO rate-ceiling gate — max_requests_per_hour
                    was declared, persisted and CHECK-constrained since
                    Phase 3 but read by no evaluation code anywhere
    scorer_kill.py  a runtime-killable wrapper around the Phase 6 Scorer seam

Everything else here is orchestration and isolation.
"""
