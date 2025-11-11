"""Legacy import guard for ``app.registry.agents``.

This module was previously a compatibility shim that re-exported agent registry
helpers. All callers should import from the concrete ``app.registry.agents.*``
modules instead (for example ``app.registry.agents.store.AgentStore``).

Keeping this sentinel in place ensures we fail loudly if any forgotten imports
remain.
"""

raise ImportError(
    "Importing from 'app.registry' as a module is no longer supported. "
    "Update imports to use the explicit 'app.registry.agents.*' modules."
)
