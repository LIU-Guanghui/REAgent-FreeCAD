# Draft

Choose wall faces and a neutral plane whose dimensions should remain fixed. Determine angle and reversal from Source taper, adjacent faces and extraction direction.

Blends can obscure walls or make drafting fail. Use surrounding geometry and dependencies to decide feature order; diagnose failures before changing faces or reordering features.

<!-- reagentfreecad:example:begin -->
## MCP request template

Resolve current draft_target faces and explicitly choose the neutral plane and material direction.

Use this Python dictionary as a parameter template for the named MCP tool. Bind every variable below to actual returned values and measurements before constructing arguments, then pass arguments to that tool. Choose other modes from current capabilities.

Tool: `PartDesign_Draft`

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
    "face_refs": draft_face_refs,
    "neutral_plane": {
        "kind": "origin_plane",
        "role": neutral_plane_role,
    },
    "angle_deg": draft_angle_deg,
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
- `draft_face_refs`: Copy current exact draft_target face references.
- `neutral_plane_role`: Chosen same-Body Origin plane defining the neutral level.
- `draft_angle_deg`: Measured draft angle and material side, degrees.
- `reverse_direction`: Choose from this feature's actual frame and material/rotation direction.

Check native face/neutral references, angle, direction and result.
<!-- reagentfreecad:example:end -->
