# Pipe

Use Pipe when a section carried along a continuous, unbranched path explains the material. Place the section near its start and determine path direction, section orientation and corner transport.

Check tight bends and closed loops for self-intersection. Do not silently join separate paths or substitute a similar straight extrusion.

<!-- reagentfreecad:example:begin -->
## MCP request template

A closed section and one continuous same-Body line/arc path explain this sweep; establish the start and direction.

Use this Python dictionary as a parameter template for the named MCP tool. Bind every variable below to actual returned values and measurements before constructing arguments, then pass arguments to that tool. Choose other modes from current capabilities.

Tool: `PartDesign_AdditivePipe`

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "session_id": session_id,
    "body_name": body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
    "feature_name": feature_name,
    "profile_sketch_name": profile_sketch_name,
    "path_sketch_name": path_sketch_name,
}
```

- `session_id`: Current returned session ID.
- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `target_revision`: Current Target revision returned by observation/state.
- `feature_name`: Choose an unused native name for this new feature.
- `profile_sketch_name`: Actual same-Body closed profile Sketch name.
- `path_sketch_name`: Actual same-Body continuous whole-path Sketch name.

Check profile/path links, fixed native transport modes and the result.
<!-- reagentfreecad:example:end -->
