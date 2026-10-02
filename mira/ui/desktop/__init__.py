"""Window-system integration for desktop surfaces, kept behind small adapters.

Nothing in the Qt-free presentation or domain model imports this package;
only the windows that need a platform behaviour do, through the adapter
interface each module defines.
"""
