# Reconstruction workflow

## Start with Source

In a reconstruction action, the user fixes the source and output paths. Check bindings with `agent_ping`. Obtain Source global facts and `initial_views`, and inspect both boards before choosing features or modeling: six orthogonal and eight corner views at one scale. The global facts and complete PNG set must enter an adopted action request before native modeling. Follow-up conversation uses [session control](session-and-isolation.md).

If delivery exceeds limits, lower resolution and retry all fourteen directions. Stop the model task if required views remain unusable. Geometry-only work requires explicit task permission declared at session start; image failure does not grant it.

## Model from evidence

For paused or follow-up conversation and changes of action scope, use [session control](session-and-isolation.md).

In reconstruction, obtain any missing Target observation before the first native feature write; an empty workspace supports a [global observation](observation.md), and applicable observations can be reused.

Use [observation](observation.md) to resolve shape, occlusion and dimensional questions, and the [feature guide](../features/entry.md) to choose a construction. Reusing affected Target measurements or references after history changes requires current evidence. `agent_get_state` reports current document, history and session evidence.

For a requested change, read the current Target object and its native references before editing. Before a later write, obtain any needed evidence that is missing, stale or insufficient. Follow [session control](session-and-isolation.md) for Target edit reports, manual comparison and recovery.

## Compare and deliver

Use [validation](validation.md) to judge geometric agreement before presenting the result. Distinguish intentional Target changes from differences still under investigation. Acceptance, saving and ending the task belong to the user's controls.
