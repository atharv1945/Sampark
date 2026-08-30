"""SAMPARK Phase 8 demo surface — spec §12.2, §19.

Spec §19's repository layout places "SSE endpoint + single-page trace +
chaos panel" in `ui/`, and this package follows that literally.

`ui/` is a TRANSPORT SHELL. It owns no business logic: every decision,
failure mechanism and audit write lives in `sampark/demo/` (backend
capability) and in the unmodified Phase 3-7 packages it calls. Delete `ui/`
entirely and every behaviour Phase 8 claims is still implemented and still
tested — `python -m sampark.demo.cli` demonstrates all three failures with
no HTTP layer at all. That is deliberate: the UI must never be the thing
that makes a failure "work".

TRUST BOUNDARY (spec §13 "Out": no auth, no multi-tenancy). This is a LOCAL
demonstration console. It binds to 127.0.0.1, has no authentication, and
anyone who can reach the port can start, reset and inject faults. That is
acceptable only because of the isolation guarantee in
`sampark.demo.isolation`: every write goes to a throwaway
`sampark_demo_<...>` schema, and no code path in this package or in
`sampark/demo/` can write to `public`. Do not expose this port.
"""
