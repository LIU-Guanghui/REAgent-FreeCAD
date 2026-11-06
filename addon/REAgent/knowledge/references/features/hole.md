# Hole

Use Hole when Sketch centers and a common drilling specification explain the removal. Establish every opening, shared direction, contiguous cut and exit or bottom; one successful opening does not prove the whole set.

Relate direction to the profile plane and material side. Distinguish the cylindrical wall's end from the conical tip, and inspect the entry side for hidden countersinks or counterbores. If manufacturing intent exceeds supported Hole modes, consider Pocket or a Boolean tool.

<!-- reagentfreecad:example:begin -->
## Native command argument template

An owned circle-only profile and prior solid support this finite angled-tip countersunk Hole; establish entry and material direction.

Use this Python dictionary as a parameter template for the named native command. Bind every variable below to actual returned values and measurements, then submit the discovered command and this dictionary through agent_execute_command. Discover its exact schema on an earlier completed request. Choose other modes from current capabilities.

Native command: `PartDesign_Hole`

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "body_name": body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
    "feature_name": feature_name,
    "sketch_name": profile_sketch_name,
    "diameter_mm": hole_diameter_mm,
    "depth_mode": "dimension",
    "depth_mm": hole_depth_mm,
    "drill_point": "angled",
    "drill_point_angle_deg": drill_angle_deg,
    "mouth_mode": "countersink",
    "mouth_diameter_mm": mouth_diameter_mm,
    "mouth_angle_deg": mouth_angle_deg,
    "reversed": reverse_direction,
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `target_revision`: Current Target revision matching the referenced Target observation.
- `feature_name`: Choose an unused native name for this new feature.
- `profile_sketch_name`: Actual same-Body closed profile Sketch name.
- `hole_diameter_mm`: Measured shaft diameter, mm.
- `hole_depth_mm`: Measured Sketch-plane-to-tip depth, mm, for this angled mode.
- `drill_angle_deg`: Measured included drill-tip angle, degrees.
- `mouth_diameter_mm`: Measured entrance diameter greater than the shaft diameter, mm.
- `mouth_angle_deg`: Measured included countersink angle, degrees.
- `reverse_direction`: Choose from this feature's actual frame and material/rotation direction.

Check native modes and direction plus each intended opening/bottom in the actual geometry.
<!-- reagentfreecad:example:end -->
