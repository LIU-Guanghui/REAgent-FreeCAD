# Observation and views

Use B-Rep measurements for dimensions, topology, material side and references; images for layout, silhouette, symmetry and occlusion. Images cannot override invalid geometry or supply unmeasured precision.

## Inspect views

Follow the [initial workflow](entry.md), then choose views for remaining questions. Inspect the delivered final images; reload the registered complete group or request a focused observation when details are too small. Choose sufficient native sampling and final output size for a new capture; enlarging an output cap alone adds no detail. Explicitly retain loaded groups needed across further work using the discovered context controls. Preserve evidence identity, role, revision and camera context, and state observations and unresolved questions. A historical Target image does not establish its current geometry or writable references.

Prefer lit surfaces with visible B-Rep edges. Shading can clarify smooth transitions; wireframe can answer layout questions. Display-mode metadata does not certify pixel appearance, and seams may be representation boundaries.

For Source/Target comparison, match pose, framing, physical scale and display state. Equal image resolutions do not imply equal scales. Standard views follow FreeCAD; corner signs locate the eye, opposite camera forward. Use entry-side or section views for hidden openings, then measure material side and depth.

## Scope queries

A Target entity query describes the named object's Shape, which may differ from the whole Result. Use bounded subsets and pagination; obtain writable selections through [native references](../foundations/persistent-references.md).

Before the first feature, observe the current Target workspace with a global query: an empty Body can issue state evidence without a Result. Views require renderable geometry; the default Target entity query requires a current Tip. Source faces/edges are subelements, not named Target objects. Discover actual entity fields from capabilities or the returned available fields; nested surface/curve values are not top-level keys.

Bind each write to actual successful Source and Target observation receipts. After a write or history change, refresh Target evidence/references for its current revision before the next write. State and validation reads do not issue observation evidence. Never substitute another role or invent evidence to bypass an unavailable query or unknown restoration.

Residual regions can locate missing or excess material; scalar volume cannot. When evidence conflicts, check object state, units, frame and camera before changing the hypothesis.

## Check restoration

Paired/focus views require empty native redo. Discard views without confirmed restoration of presentation, temporary objects, active document and model state. A camera/display failure invalidates the view without undoing a confirmed model commit; uncertain model or Result restoration follows [session recovery](session-and-isolation.md).

<!-- reagentfreecad:example:begin -->
## Observation and write sequence

Parameter templates for separate tool turns, not a FreeCAD console script. Complete Source global/initial views and inspect all directions first; discover the primitive capability before writing. Face1 illustrates an actual Source handle: choose the measured subset (or edges) for the task. A box is only a parameter example, not a prescribed construction.

Submit observation dictionaries to agent_observe. For the native write in step 3, submit the discovered command and its dictionary through agent_execute_command. Wait for success, bind result to that returned payload, then run the binding lines before building the next dictionary. Never batch dependent calls. Other feature variables come from actual measurements and owned names as described in the primitive card. An empty Target supports the global query in step 2.

### 1. `agent_observe`

```python
arguments = {
    "role": "source",
    "request": {
        "kind": "entity",
        "query": {
            "faces": ["Face1"],
            "edges": [],
            "fields": ["name", "area_mm2", "bounding_box_mm", "center_mm", "surface"],
        },
    },
}
```

```python
source_evidence_id = result['evidence_id']
```

### 2. `agent_observe`

```python
arguments = {
    "role": "target",
    "request": {
        "kind": "global",
        "query": {},
    },
}
```

```python
target_evidence_id = result['evidence_id']
target_revision = result['revision']
```

### 3. `PartDesign_AdditiveBox`

```python
arguments = {
    "body_name": body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
    "feature_name": feature_name,
    "length_mm": length_mm,
    "width_mm": width_mm,
    "height_mm": height_mm,
    "position_mm": body_position_mm,
}
```

```python
# Check the actual commit result before requesting fresh Target evidence.
```

### 4. `agent_observe`

```python
arguments = {
    "role": "target",
    "request": {
        "kind": "global",
        "query": {},
    },
}
```

```python
target_evidence_id = result['evidence_id']
target_revision = result['revision']
```

Use the refreshed Target receipt for the next write. State/validation do not replace it. If a call fails, inspect its actual reason before choosing another request.
<!-- reagentfreecad:example:end -->
