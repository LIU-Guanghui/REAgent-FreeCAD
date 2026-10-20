# Linear and Polar Pattern

Use congruence and measured translation or rotation to identify repeated native features. Establish their order, axis or direction, count, total extent or step spacing, and material action; equal spacing alone is insufficient.

Choose the receiving Body and references in the current Target state.

<!-- reagentfreecad:example:begin -->
## MCP request template

Measured repetition supports these actual seed features, one direction and a per-step offset.

Use this Python dictionary as a parameter template for the named MCP tool. Bind every variable below to actual returned values and measurements before constructing arguments, then pass arguments to that tool. Choose other modes from current capabilities.

Tool: `PartDesign_LinearPattern`

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "session_id": session_id,
    "body_name": body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
    "feature_name": feature_name,
    "predecessor_name": predecessor_name,
    "source_feature_names": source_feature_names,
    "axis": {
        "kind": "origin_axis",
        "role": pattern_axis_role,
    },
    "extent_mode": "offset",
    "distance_mm": pattern_offset_mm,
    "occurrences": occurrences,
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
- `predecessor_name`: Actual current solid Tip of the receiving Body.
- `source_feature_names`: Actual caller-ordered additive/subtractive seed feature names.
- `pattern_axis_role`: Chosen same-Body Origin axis matching the observed repetition.
- `pattern_offset_mm`: Measured per-step offset in this offset-mode example, mm.
- `occurrences`: Observed total occurrences including the original.
- `reverse_direction`: Choose from this feature's actual frame and material/rotation direction.

Check native offset semantics, total occurrences and actual instances.
<!-- reagentfreecad:example:end -->
