# Transactions and errors

An abort or undo return does not prove restoration. Check document objects, Result, Body Tip, native history and recompute as relevant. Distinguish input rejection, native failure, invalid geometry, recovery and saving.

Use reported error class, stage, side effect and retry guidance. Follow [session recovery](../workflow/session-and-isolation.md) for unknown outcomes and discard boundaries.

`agent_read_freecad_log` supplies bounded native diagnostics. Shared, missing or empty logs cannot establish call attribution, model equivalence or CAD/LLM duration; the journal records process facts.
