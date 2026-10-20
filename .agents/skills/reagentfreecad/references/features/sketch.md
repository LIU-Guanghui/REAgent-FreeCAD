# Sketch

Choose a stable plane and material side for the measured profile. Connect reused endpoints with explicit coincidence constraints; equal coordinates do not preserve connections during edits. Use independent position, direction and geometric constraints, explaining intended remaining freedom before consumption.

Resolve invalid or redundant constraints from solver diagnostics and confirmed rollback while preserving required relations. Use supported session history to rework consumed sketches.

Check connections, degeneracy and self-intersections before forming a solid; solver status or a current solid alone does not verify editability. A Sketch without its generator remains `in_progress`.

<!-- reagentfreecad:example:begin -->
## MCP request template

The owned Sketch is empty; convert measured coordinates into its local XY frame before using the same center values for geometry and DistanceX/DistanceY.

Use this Python dictionary as a parameter template for the named MCP tool. Bind every variable below to actual returned values and measurements before constructing arguments, then pass arguments to that tool. Choose other modes from current capabilities.

Tool: `Sketcher_EditSketch`

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "session_id": session_id,
    "body_name": body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
    "sketch_name": sketch_name,
    "geometry": [{
        "kind": "circle",
        "id": "section_circle",
        "center": [profile_center_x_mm, profile_center_y_mm],
        "radius_mm": profile_radius_mm,
    }],
    "constraints": [{
        "kind": "DistanceX",
        "point": {
            "geometry_id": "section_circle",
            "position": "center",
        },
        "value": profile_center_x_mm,
    }, {
        "kind": "DistanceY",
        "point": {
            "geometry_id": "section_circle",
            "position": "center",
        },
        "value": profile_center_y_mm,
    }, {
        "kind": "Diameter",
        "geometry_id": "section_circle",
        "value": profile_diameter_mm,
    }],
}
```

- `session_id`: Current returned session ID.
- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `target_revision`: Current Target revision returned by observation/state.
- `sketch_name`: Actual owned empty Sketch name returned by creation/state.
- `profile_center_x_mm`: Signed circle center X relative to this Sketch's own origin and X axis, mm; convert coordinates from other frames first.
- `profile_center_y_mm`: Signed circle center Y relative to this Sketch's own origin and Y axis, mm; convert coordinates from other frames first.
- `profile_radius_mm`: Measured circle radius, mm; same circle as profile_diameter_mm.
- `profile_diameter_mm`: Measured diameter of that circle, mm.

Check actual solver diagnostics and the intended profile before generating a feature.
<!-- reagentfreecad:example:end -->
