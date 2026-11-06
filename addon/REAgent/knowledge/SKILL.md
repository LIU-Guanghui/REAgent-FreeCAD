# REAgentFreeCAD

Use REAgentFreeCAD for editable STEP reconstruction and review in two user-started, UI-bound FreeCAD windows. Source geometry is read-only; model in Target.

Read current commands and schemas through `agent_get_capabilities`. Knowledge cards and FreeCAD type names do not establish API support; if a required command is absent, report the gap and stop that step.

Choose the construction, dimensions, further observations, acceptance and rework from evidence. Avoid fixed feature orders and acceptance scores. Stop modeling when write or restoration state is unknown; follow [session recovery](references/workflow/session-and-isolation.md).

## Read as needed

- [Conversation and session control](references/workflow/session-and-isolation.md): read before handling a paused or follow-up message.
- [Workflow](references/workflow/entry.md): complete initial observation before modeling, then compare and deliver.
- [Feature guide](references/features/entry.md): choose an editable construction and its relevant family card.
- [Foundations](references/foundations/entry.md): resolve a topology, reference, tolerance or document-state question.
