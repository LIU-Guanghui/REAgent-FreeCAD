# Observation and views

Use B-Rep measurements for dimensions, topology, material side and references; images for layout, silhouette, symmetry and occlusion. Images cannot override invalid geometry or supply unmeasured precision.

## Inspect views

Follow the [initial workflow](entry.md), then choose views for remaining questions. Inspect the delivered final images; reload the registered complete group or request a focused observation when details are too small. Choose sufficient native sampling and final output size for a new capture; enlarging an output cap alone adds no detail. Use the context controls to retain loaded groups needed for further work. Preserve evidence identity, role, revision and camera context, and state observations and unresolved questions. A historical Target image does not establish its current geometry or writable references.

Prefer lit surfaces with visible B-Rep edges. Shading can clarify smooth transitions; wireframe can answer layout questions. Display-mode metadata does not certify pixel appearance, and seams may be representation boundaries.

For Source/Target comparison, match pose, framing, physical scale and display state. Equal image resolutions do not imply equal scales. Standard views follow FreeCAD; corner signs locate the eye, opposite camera forward. Use entry-side or section views for hidden openings, then measure material side and depth.

## Scope queries

A Target entity query describes the named object's Shape, which may differ from the whole Result. Use bounded subsets and pagination; obtain writable selections through [native references](../foundations/persistent-references.md).

Views require renderable geometry. The default Target entity query requires a current Tip. Source faces and edges are subelements of the reference. Use capability descriptions or the returned available fields to select entity fields. Read surface and curve values within their corresponding fields.

Cite evidence for the current Target and the bound Source required by the workflow. The `evidence_id` from `agent_get_state` or a confirmed write provides overall Target state, including an empty Body. Observe the current geometry for measurements and native references. Cite adopted observations for delivery.

Residual regions can locate missing or excess material; scalar volume cannot. When evidence conflicts, check object state, units, frame and camera before changing the hypothesis.

## Check restoration

Paired/focus views require empty native redo. Discard views without confirmed restoration of presentation, temporary objects, active document and model state. A camera/display failure invalidates the view without undoing a confirmed model commit; uncertain model or Result restoration follows [session recovery](session-and-isolation.md).

<!-- reagentfreecad:example:begin -->
## Observation and write sequence

This STP example starts after the workflow's initial Source inspection. Choose real Source entities and read the command schema with `agent_get_capabilities`. The box illustrates argument binding.

Pass each dictionary to its named tool. Bind result to the successful returned payload before building a dependent call. Bind the feature variables using actual measurements and owned names from the primitive card.

### 1. `agent_observe`

```python
arguments = {
    "role": "source",
    "kind": "entity",
    "faces": ["Face1"],
    "fields": ["name", "area_mm2", "bounding_box_mm", "center_mm", "surface"],
}
```

```python
source_evidence_id = result['observation_id']
```

### 2. `agent_observe`

```python
arguments = {
    "role": "target",
    "kind": "global",
}
```

```python
target_evidence_id = result['observation_id']
```

### 3. `agent_execute_command`

```python
arguments = {
    "command": "PartDesign_AdditiveBox",
    "arguments": {
        "body_name": body_name,
        "intent": intent,
        "evidence": [source_evidence_id, target_evidence_id],
        "feature_name": feature_name,
        "length_mm": length_mm,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "position_mm": body_position_mm,
    },
}
```

```python
target_evidence_id = result['evidence_id']
```

The final assignment replaces the previous Target ID. Build the next write's `evidence` from `[source_evidence_id, target_evidence_id]` with the updated ID. Observe the updated Target for any specific references or measurements needed next.
<!-- reagentfreecad:example:end -->
