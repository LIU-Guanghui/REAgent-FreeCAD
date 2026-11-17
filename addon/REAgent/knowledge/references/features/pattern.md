# Linear and Polar Pattern

Use congruence and measured translation or rotation to identify repeated native features. Establish their order, axis or direction, count, total extent or step spacing, and material action; equal spacing alone is insufficient.

Choose the receiving Body and references in the current Target state.

<!-- reagentfreecad:example:begin -->
## Native command argument template

Measured repetition supports these actual seed features, one direction and a per-step offset.

Bind the variables to actual readbacks and measurements. Read the command schema with `agent_get_capabilities` and pass this dictionary to `agent_execute_command`.

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "command": "PartDesign_LinearPattern",
    "arguments": {
        "body_name": body_name,
        "intent": intent,
        "evidence": [source_evidence_id, target_evidence_id],
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
    },
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `feature_name`: Choose an unused native name for this new feature.
- `predecessor_name`: Actual current solid Tip of the receiving Body.
- `source_feature_names`: Actual caller-ordered additive/subtractive seed feature names.
- `pattern_axis_role`: Chosen same-Body Origin axis matching the observed repetition.
- `pattern_offset_mm`: Measured per-step offset in this offset-mode example, mm.
- `occurrences`: Observed total occurrences including the original.
- `reverse_direction`: Choose from this feature's actual frame and material/rotation direction.

Check native offset semantics, total occurrences and actual instances.
<!-- reagentfreecad:example:end -->
