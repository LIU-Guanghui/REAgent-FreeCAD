# Session ownership and recovery

## Bind documents and evidence

A session owns the exact Target Document and, for STP, STL or DXF, a read-only reference Document in the separate Source GUI. Do not adopt a same-named replacement. Keep file evidence tied to the task's selected revision. Changing the input requires a new task.

Native `agent_undo`/`agent_redo` require a session-owned transaction at the exact stack top. Closing or reopening invalidates that ownership; journal entries cannot restore it. Omission from the bounded evidence list in `agent_get_state` does not expire earlier session evidence.

## Pause and continue through conversation

Each new ordinary message uses the Target action scope in current framework facts. Answer a question, accept guidance or make the requested Target change; a question is not permission to modify the model or resume the earlier plan. Use current Target observations and native references for changes. The user can run `/compare` for paired views of STP and STL tasks. STP also offers geometry checks.

When a successful native edit receipt confirms the commit, requested parameters and result validity, use it to report the Target change without an extra observation. Read again only if the current user question or a later write needs information that existing evidence cannot supply because it is missing, stale or insufficient. Staleness alone does not require a read. An edit receipt is not a new observation receipt.

When unfinished reconstruction is paused, handle the message first and ask once whether to continue reconstruction. Ask at most once in the same pause episode. Unanswered questions, a new subject, further Target work, Save, Compare or another Pause do not restart that episode or justify asking again. An explicit refusal ends prompting; the user can still ask to continue later. Only actual continuation followed by another pause starts a new episode. Do not ask after ordinary changes to an already delivered reconstruction.

Only the latest real user message can authorize continuation. Interpret its meaning in context, not by matching a keyword. If it explicitly agrees to continue, call the available continuation control and proceed with the new reconstruction action. Your own question, older consent and summaries are not new consent. One message may include both a change and consent to continue; carry its guidance forward once. Otherwise finish this Target reply and wait.

After continuation is adopted, inspect current Target as needed and briefly explain the relevant findings and updated plan before writing. Preserve deliberate user changes rather than applying an obsolete reconstruction plan over them. Use the existing revision-bound Target evidence and native references; re-observe only when needed evidence is stale or insufficient. The scope change, the user's intended design, model validity and agreement with Source remain distinct facts.

## Handle unknown outcomes

Preserve the error and operation identity. Unknown Target writes block mutations; uncertain Source restoration stops modeling. Non-view observations and validation without parameter audit remain readable, but safe reads neither clear uncertainty nor prove rollback. Do not blindly repeat a lost write.

The user can click **End task** even when modeling is blocked. Ending releases original bindings as cleanup is confirmed and preserves open documents, geometry, native history, and records of unknown outcomes. If Target cleanup cannot be confirmed, REAgent drops the local Target binding and records the uncertainty. Clicking **End task** again continues the original cleanup, including pending Source cleanup and journal writes eligible for retry. FreeCAD handles native window closing.

For restoration evidence and native-log limits, see [transactions and errors](../foundations/transactions-errors.md).
