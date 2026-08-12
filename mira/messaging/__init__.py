"""In-process notification.

Owns subscription and synchronous fan-out. Depends on nothing else in `mira`.

The bus itself carries no authority: it holds no application state, and payloads
are values rather than commands. What a subscriber does on receipt is the
subscriber's business — `EmbodiedBehavior` does set expressive state from an
event today, and that is its decision, not the bus's.

Separate from `mira.memory` on purpose: neither may import the other, and only a
separate package lets the checker enforce it. Not an asynchronous bus, and it
does not become one here.

If a transport or deferred-delivery implementation is added, its port belongs
here and the implementation in `mira/adapters/`, following the scheduler port.

Import from the defining module (`mira.messaging.events`) so each symbol has a
single import path. See scripts/check_layering.py for the enforced rules.
"""
