"""Phase 8 scope-contract package.

A *scope contract* is the durable artifact produced by the Hazırlık (Prepare)
screen. It pins which tables are in the basket, how they are projected, how
they join, which filters are locked (pinned) vs adjustable (interactive), and
the per-table cached/lazy routing decision.

Sub-phase 8.a ships the data model, validators, S3 persistence, the routing
decision algorithm, and the Sunum-side enforcement seams (patch validator +
variable resolution). The Hazırlık UI (8.b) and the Oracle fetch path (8.d)
build on top of these primitives.

See ``docs/PHASE_8_SPEC.md`` §2–§4 for the wire format and the locked design
decisions.
"""
