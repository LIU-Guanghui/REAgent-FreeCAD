# Session ownership and recovery

## Bind documents and evidence

A session owns two exact Document objects in separate GUI processes; it cannot adopt a same-named replacement. Binding establishes ownership, not a geometry revision.

Keep Source evidence on the session's STEP revision; start a new session when the file changes. Resume needs fresh evidence and does not certify the checkpoint's historical Source version.

Native `agent_undo`/`agent_redo` require a session-owned transaction at the exact stack top. Closing or reopening invalidates that ownership; journal entries cannot restore it. Omission from the bounded evidence list in `agent_get_state` does not expire earlier session evidence.

## Handle unknown outcomes

Preserve the error and operation identity. Unknown Target writes block mutations; uncertain Source restoration stops modeling. Non-view observations and validation without parameter audit remain readable, but safe reads neither clear uncertainty nor prove rollback. Do not blindly repeat a lost write.

Only explicit discard close through `agent_close_session` may mutate a blocked session. Preserve desired changes through a safe user GUI flow before discard, and retain each role's close result. Discarding unverified state is not restoration.

For restoration evidence and native-log limits, see [transactions and errors](../foundations/transactions-errors.md).
