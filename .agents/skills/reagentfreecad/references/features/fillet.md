# Fillet

A constant-radius tangent transition can support Fillet intent; an isolated torus, cylinder or sphere cannot identify its history. Match predecessor edges and radius, checking local clearance, edge lengths, corners and neighbouring blends.

Boolean and Fillet generally do not commute. Use surviving support faces and material action to choose their order. A rounded revolved profile may be a better explanation when the evidence supports it.

<!-- reagentfreecad:example:begin -->
## MCP request template

Resolve the intended edge set on the current solid predecessor with dressup_edge use.

Use this Python dictionary as a parameter template for the named MCP tool. Bind every variable below to actual returned values and measurements before constructing arguments, then pass arguments to that tool. Choose other modes from current capabilities.

Tool: `PartDesign_Fillet`

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
    "radius_mm": fillet_radius_mm,
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
- `fillet_radius_mm`: Measured blend radius, mm.

Check the exact native Base links, radius and valid resulting Tip.
<!-- reagentfreecad:example:end -->
