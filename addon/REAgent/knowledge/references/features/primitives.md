# Primitives

Choose an additive or subtractive native primitive from measured surface relationships, symmetry, coaxiality and material context. A matching bounding box alone is insufficient. Select the receiving Body and its local placement or axis explicitly.

Later cuts and blends can hide original boundaries; keep uncertain history explicit and consider a Sketch-driven or composite construction when better supported.

<!-- reagentfreecad:example:begin -->
## Native command argument template

Use when measured planar bounds support a native box in this Body frame.

Use this Python dictionary as a parameter template for the named native command. Bind every variable below to actual returned values and measurements, then submit the discovered command and this dictionary through agent_execute_command. Discover its exact schema on an earlier completed request. Choose other modes from current capabilities.

Native command: `PartDesign_AdditiveBox`

```python
# Bind all variables to actual returned values and measurements before use.
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

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `target_revision`: Current Target revision matching the referenced Target observation.
- `feature_name`: Choose an unused native name for this new feature.
- `length_mm`: Measured full generating length, mm; include portions later obscured by dress-ups.
- `width_mm`: Measured intrinsic width in the chosen local frame, mm.
- `height_mm`: Measured intrinsic height in the chosen local frame, mm.
- `body_position_mm`: Measured feature position in the receiving Body's local coordinates, mm.

Check native dimensions, Body Tip and returned shape facts.
<!-- reagentfreecad:example:end -->
