"""Implementations of domain ports against concrete technologies.

Everything here depends on something external — a GUI toolkit, an operating
system facility, a wire protocol — and nothing here is imported by the domain.
Adapters may import `mira.domain`; the reverse is a layering violation.

See scripts/check_layering.py for the enforced rules.
"""
