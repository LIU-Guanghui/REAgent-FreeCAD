# Fillet

A constant-radius tangent transition can support Fillet intent; an isolated torus, cylinder or sphere cannot identify its history. Match predecessor edges and radius, checking local clearance, edge lengths, corners and neighbouring blends.

Boolean and Fillet generally do not commute. Use surviving support faces and material action to choose their order. A rounded revolved profile may be a better explanation when the evidence supports it.

<!-- reagentfreecad:example:begin -->
## Native command argument template

Resolve the intended edge set on the current solid predecessor with dressup_edge use.

Bind the variables to actual readbacks and measurements. Read the command schema with `agent_get_capabilities` and pass this dictionary to `agent_execute_command`.

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "command": "PartDesign_Fillet",
    "arguments": {
        "body_name": body_name,
        "intent": intent,
        "evidence": [source_evidence_id, target_evidence_id],
        "feature_name": feature_name,
        "predecessor_name": predecessor_name,
        "edge_refs": edge_refs,
        "radius_mm": fillet_radius_mm,
    },
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `feature_name`: Choose an unused native name for this new feature.
- `predecessor_name`: Actual current solid Tip of the receiving Body.
- `edge_refs`: Copy complete exact edge references resolved for this current predecessor.
- `fillet_radius_mm`: Measured blend radius, mm.

Check the exact native Base links, radius and valid resulting Tip.
<!-- reagentfreecad:example:end -->
