# Chamfer

Use adjacent-face evidence to identify a straight bevel and its predecessor edges. Choose equal or two-distance intent, and determine the two-distance direction from the faces.

Check distances against local clearance and neighbouring features. A two-distance bevel can resemble a distance-and-angle result without preserving its parameter intent; report that distinction when it matters.

<!-- reagentfreecad:example:begin -->
## MCP request template

Current resolved edges and two measured adjacent-face distances support two_distances. For equal_distance, omit size2_mm and flip_direction.

Use this Python dictionary as a parameter template for the named MCP tool. Bind every variable below to actual returned values and measurements before constructing arguments, then pass arguments to that tool. Choose other modes from current capabilities.

Tool: `PartDesign_Chamfer`

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
    "edge_refs": edge_refs,
    "mode": "two_distances",
    "size_mm": chamfer_size_mm,
    "size2_mm": chamfer_size2_mm,
    "flip_direction": flip_direction,
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
- `edge_refs`: Copy complete exact edge references resolved for this current predecessor.
- `chamfer_size_mm`: Measured first chamfer distance, mm.
- `chamfer_size2_mm`: Measured second chamfer distance, mm.
- `flip_direction`: Set True or False for native FlipDirection in two_distances; verify the adjacent-face distance assignment.

Check native mode, side assignment, both sizes and Base links.
<!-- reagentfreecad:example:end -->
