"""Measurement layer — the apparatus, not the system under test.

Nothing in this package may import from `topologies/` or `core.agents`. That
constraint is what makes the capture layer topology-agnostic, and it is checkable:

    grep -rE "^(from|import) (topologies|core\.agents)" measurement/   # must be empty

If that grep ever returns a line, a topology has leaked into the instrumentation and
cross-condition numbers are no longer comparable.
"""
