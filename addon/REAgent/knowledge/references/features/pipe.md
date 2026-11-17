# Pipe

Use Pipe when a section carried along a continuous, unbranched path explains the material. Place the section near its start and determine path direction, section orientation and corner transport.

Check tight bends and closed loops for self-intersection. Do not silently join separate paths or substitute a similar straight extrusion.

<!-- reagentfreecad:example:begin -->
## Native command argument template

A closed section and one continuous same-Body line/arc path explain this sweep; establish the start and direction.

Bind the variables to actual readbacks and measurements. Read the command schema with `agent_get_capabilities` and pass this dictionary to `agent_execute_command`.

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "command": "PartDesign_AdditivePipe",
    "arguments": {
        "body_name": body_name,
        "intent": intent,
        "evidence": [source_evidence_id, target_evidence_id],
        "feature_name": feature_name,
        "profile_sketch_name": profile_sketch_name,
        "path_sketch_name": path_sketch_name,
    },
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `feature_name`: Choose an unused native name for this new feature.
- `profile_sketch_name`: Actual same-Body closed profile Sketch name.
- `path_sketch_name`: Actual same-Body continuous whole-path Sketch name.

Check profile/path links, fixed native transport modes and the result.
<!-- reagentfreecad:example:end -->
