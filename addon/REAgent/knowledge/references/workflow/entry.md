# Reconstruction workflow

## Start with Source

The user starts or resumes the task and fixes its source and output paths. Check bindings with `agent_ping`. For ordinary reconstruction, request Source `initial_views` and inspect both boards before choosing features or modeling: six orthogonal and eight corner views at one scale.

If delivery exceeds limits, lower resolution and retry all fourteen directions. Stop the model task if required views remain unusable. Geometry-only work requires explicit task permission declared at session start; image failure does not grant it.

## Model from evidence

Use [observation](observation.md) to resolve shape, occlusion and dimensional questions, and the [feature guide](../features/entry.md) to choose a construction. After history changes, refresh affected Target measurements and references. `agent_get_state` reports current document, history and session evidence.

Use [session recovery](session-and-isolation.md) for rework or unknown outcomes.

## Compare and deliver

Use [validation](validation.md) to judge geometric agreement before presenting the result. Keep the final reply brief: give an overall feature-tree summary, actual unresolved issues and actual save facts. Omit individual feature dimensions, placements, detailed proofs and numerical comparison tables unless asked. Acceptance, saving and ending the task belong to the user's controls.
