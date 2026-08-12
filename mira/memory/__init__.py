"""What the assistant retains within a session.

Owns the episodic history and its bound, the last inferred intent, and the
free-form session context. Depends only on `mira.domain` — specifically
`UserInput`, `IntentResult` and `BrainResponse`, since a stored message is built
from them — so cognition and actions can reach it without reaching into
orchestration.

Separate from `mira.messaging` on purpose, and the reason is enforceable rather
than stylistic: the store must not be able to import the bus, and the bus must
not be able to import the store. Merged into one package they would be the same
layer, and the checker could no longer say that. `mira.domain` is not the home
for the same reason it is not the home for the bus.

Deliberately not owned here: persistence and privacy policy. If a storage
backend is added, its port belongs here and the implementation in
`mira/adapters/`, following the scheduler port.

Import from the defining module (`mira.memory.session_memory`) so each symbol
has a single import path. See scripts/check_layering.py for the enforced rules.
"""
