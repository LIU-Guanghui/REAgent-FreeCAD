# Revolution and Groove

Use coherent coaxial surfaces and radial end faces to infer a meridian profile for rotational addition or removal. Choose an explicit Sketch or owned reference axis, sweep angle and material side.

Reject axis-crossing profiles; an uncertain axis is not permission for a free-vector guess.

<!-- reagentfreecad:example:begin -->
## MCP request template

The closed profile and chosen Sketch axis explain the measured rotational shape.

Use this Python dictionary as a parameter template for the named MCP tool. Bind every variable below to actual returned values and measurements before constructing arguments, then pass arguments to that tool. Choose other modes from current capabilities.

Tool: `PartDesign_Revolution`

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "session_id": session_id,
    "body_name": body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
    "feature_name": feature_name,
    "sketch_name": profile_sketch_name,
    "axis": {
        "kind": "sketch_axis",
        "axis": rotation_axis_role,
    },
    "angle_deg": rotation_angle_deg,
    "reversed": reverse_direction,
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
- `rotation_axis_role`: Chosen horizontal/vertical axis of the actual profile Sketch.
- `rotation_angle_deg`: Measured sweep angle about the chosen axis, degrees.
- `reverse_direction`: Choose from this feature's actual frame and material/rotation direction.

Check the actual axis link, angle, direction and resulting Tip.
<!-- reagentfreecad:example:end -->
