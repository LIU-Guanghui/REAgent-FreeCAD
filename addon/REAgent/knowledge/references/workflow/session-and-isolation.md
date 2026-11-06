# Session ownership and recovery

## Bind documents and evidence

A session owns two exact Document objects in separate GUI processes; it cannot adopt a same-named replacement. Binding establishes ownership, not a geometry revision.

Keep Source evidence on the session's STEP revision; start a new session when the file changes. Resume needs fresh evidence and does not certify the checkpoint's historical Source version.

Native `agent_undo`/`agent_redo` require a session-owned transaction at the exact stack top. Closing or reopening invalidates that ownership; journal entries cannot restore it. Omission from the bounded evidence list in `agent_get_state` does not expire earlier session evidence.

## Pause and continue through conversation

Each new ordinary message uses the Target action scope in current framework facts. Answer a question, accept guidance or make the requested Target change; a question is not permission to modify the model or resume the earlier plan. Use current Target observations and native references for changes. Source comparisons, parameter audits and new screenshots are outside this scope; direct the user to `/compare` for a manual geometric or paired-view comparison.

When a successful native edit receipt confirms the commit, requested parameters and result validity, use it to report the Target change without an extra observation. Read again only if the current user question or a later write needs information that existing evidence cannot supply because it is missing, stale or insufficient. Staleness alone does not require a read. An edit receipt is not a new observation receipt.

When unfinished reconstruction is paused, handle the message first and ask once whether to continue reconstruction. Ask at most once in the same pause episode. Unanswered questions, a new subject, further Target work, Save, Compare or another Pause do not restart that episode or justify asking again. An explicit refusal ends prompting; the user can still ask to continue later. Only actual continuation followed by another pause starts a new episode. Do not ask after ordinary changes to an already delivered reconstruction.

Only the latest real user message can authorize continuation. Interpret its meaning in context, not by matching a keyword. If it explicitly agrees to continue, use the available continuation control with its discovered schema; do not ask for the same agreement again. Your own question, older consent and summaries are not new consent. One message may include both a change and consent to continue; carry its guidance forward once. Otherwise finish this Target reply and wait.

After continuation is adopted, inspect current Target as needed and briefly explain the relevant findings and updated plan before writing. Preserve deliberate user changes rather than applying an obsolete reconstruction plan over them. Use the existing revision-bound Target evidence and native references; re-observe only when needed evidence is stale or insufficient. The scope change, the user's intended design, model validity and agreement with Source remain distinct facts.

## Handle unknown outcomes

Preserve the error and operation identity. Unknown Target writes block mutations; uncertain Source restoration stops modeling. Non-view observations and validation without parameter audit remain readable, but safe reads neither clear uncertainty nor prove rollback. Do not blindly repeat a lost write.

The user can end the task through `agent_close_session` even when modeling is blocked. Ending releases each confirmed original binding and retains open documents, native history and unknown-state facts; it neither saves nor discards geometry. Unreachable roles and incomplete logs remain pending, and another explicit End task can retry original cleanup without resuming modeling. Native window closing belongs to FreeCAD.

For restoration evidence and native-log limits, see [transactions and errors](../foundations/transactions-errors.md).
