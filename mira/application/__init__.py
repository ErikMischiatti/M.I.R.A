"""Application composition: the layer that builds the graph and wires it.

Composition creates, domain executes, UI presents. Nothing here interprets
input, holds state authority, or draws anything — it decides which concrete
implementations exist, in what order they are built, and what they share.

This layer must not import `mira.ui`. The dependency runs the other way: the UI
receives a built `Application`, and `mira.main` is the only place that knows
about both.
"""
