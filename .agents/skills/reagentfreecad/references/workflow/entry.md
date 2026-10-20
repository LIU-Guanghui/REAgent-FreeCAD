# Reconstruction workflow

## Start with Source

Check bindings with `agent_ping`, then open or resume with `agent_start_session`. For ordinary reconstruction, request Source `initial_views` and inspect both boards before choosing features or modeling: six orthogonal and eight corner views at one scale.

If delivery exceeds limits, lower resolution and retry all fourteen directions. Stop the model task if required views remain unusable. Geometry-only work requires explicit task permission declared at session start; image failure does not grant it.

## Model from evidence

Use [observation](observation.md) to resolve shape, occlusion and dimensional questions, and the [feature guide](../features/entry.md) to choose a construction. After history changes, refresh affected Target measurements and references. `agent_get_state` reports current document, history and session evidence.

Use [session recovery](session-and-isolation.md) for rework or unknown outcomes.

## Compare and deliver

Follow [validation](validation.md) before accepting or saving. Report geometric agreements, unresolved differences, native readiness and save facts separately.
