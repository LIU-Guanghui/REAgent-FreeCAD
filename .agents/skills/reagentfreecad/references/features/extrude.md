# Pad and Pocket

A closed planar profile, parallel side rulings and cap evidence support a Pad or Pocket hypothesis. Choose the existing Sketch, material side, extent, order and merge scope from Source evidence.

Recover the full generating extent, including material later removed by cuts or dress-ups; a remaining straight wall may be shorter. Check whether the current command expresses any required symmetry, taper or face termination.

<!-- reagentfreecad:example:begin -->
## MCP request template

A same-Body closed Sketch and measured constant-direction extent support this Pad.

Use this Python dictionary as a parameter template for the named MCP tool. Bind every variable below to actual returned values and measurements before constructing arguments, then pass arguments to that tool. Choose other modes from current capabilities.

Tool: `PartDesign_Pad`

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
    "length_mm": length_mm,
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
- `length_mm`: Measured full generating length, mm; include portions later obscured by dress-ups.
- `reverse_direction`: Choose from this feature's actual frame and material/rotation direction.

Check profile, full native length, direction and Body Tip.
<!-- reagentfreecad:example:end -->
