# Observation and views

Use B-Rep measurements for dimensions, topology, material side and references; images for layout, silhouette, symmetry and occlusion. Images cannot override invalid geometry or supply unmeasured precision.

## Inspect views

Follow the [initial workflow](entry.md), then choose views for remaining questions. Inspect returned pixels or saved images; use registered single images when board details are too small. Retain role, evidence ID and camera metadata, and state observations and unresolved questions. Use returned artifact paths and preserve existing images.

Prefer lit surfaces with visible B-Rep edges. Shading can clarify smooth transitions; wireframe can answer layout questions. Display-mode metadata does not certify pixel appearance, and seams may be representation boundaries.

For Source/Target comparison, match pose, framing, physical scale and display state. Equal image resolutions do not imply equal scales. Standard views follow FreeCAD; corner signs locate the eye, opposite camera forward. Use entry-side or section views for hidden openings, then measure material side and depth.

## Scope queries

A Target entity query describes the named object's Shape, which may differ from the whole Result. Use bounded subsets and pagination; obtain writable selections through [native references](../foundations/persistent-references.md).

Residual regions can locate missing or excess material; scalar volume cannot. When evidence conflicts, check object state, units, frame and camera before changing the hypothesis.

## Check restoration

Paired/focus views require empty native redo. Discard views without confirmed restoration of presentation, temporary objects, active document and model state. A camera/display failure invalidates the view without undoing a confirmed model commit; uncertain model or Result restoration follows [session recovery](session-and-isolation.md).
