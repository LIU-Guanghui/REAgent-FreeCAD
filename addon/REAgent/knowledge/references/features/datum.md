# Datum and coordinate systems

Use a Datum for a shared stable plane, axis, point or local frame. Keep origin, handed axes, rigid placement and parent reference distinct from intrinsic feature dimensions.

Symmetric shapes may admit several intrinsic frames; after choosing one, record the remaining rigid transform. Confirm downstream reference support and check world-space geometry separately. A Datum supplies a reference, not a solid result.

<!-- reagentfreecad:example:begin -->
## Native command argument template

This example attaches to the receiving Body's own XY plane; choose the intended support and frame.

Use this Python dictionary as a parameter template for the named native command. Bind every variable below to actual returned values and measurements, then submit the discovered command and this dictionary through agent_execute_command. Discover its exact schema on an earlier completed request. Choose other modes from current capabilities.

Native command: `Part_DatumPlane`

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "body_name": body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
    "datum_name": datum_name,
    "mode": "attached",
    "reference": {
        "kind": "origin_plane",
        "role": "XY_Plane",
    },
    "position_mm": datum_attachment_offset_mm,
    "axis": datum_attachment_axis,
    "angle_deg": datum_attachment_angle_deg,
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `target_revision`: Current Target revision matching the referenced Target observation.
- `datum_name`: Choose an unused native Datum name.
- `datum_attachment_offset_mm`: Measured translation in the Datum's attachment coordinate system, mm.
- `datum_attachment_axis`: Chosen nonzero rotation axis in the Datum's attachment coordinate system.
- `datum_attachment_angle_deg`: Measured rotation about datum_attachment_axis, degrees.

Check returned support, attachment frame and placement; the Datum is not a solid.
<!-- reagentfreecad:example:end -->
