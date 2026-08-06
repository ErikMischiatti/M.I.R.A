"""UI-independent shared vocabulary.

This package owns the types that cognition, actions, orchestration and the UI
all need to agree on. It depends on nothing else in `mira` and must never
import a GUI toolkit, so it stays usable by a future non-desktop embodiment.

Import symbols from their defining module (`mira.domain.state`,
`mira.domain.models`) rather than from this package, so each symbol has a
single import path.

The boundary is enforced by scripts/check_layering.py, which states the
rules in full. (Proposed ADR 0001/0002 cover this decision but are not yet
recorded in the repository.)
"""
