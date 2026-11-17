# Thickness

Identify the opening faces, cavity, wall thickness and material side. Choose inward or outward shelling to preserve the intended envelope, checking narrow gaps, tight curvature and neighbouring details.

Corner treatment and feature order affect the material. Diagnose native failures or shape differences, and explain any change of openings, wall side or construction method.

<!-- reagentfreecad:example:begin -->
## Native command argument template

Current opening-face references, wall measurements and material side support hollowing.

Bind the variables to actual readbacks and measurements. Read the command schema with `agent_get_capabilities` and pass this dictionary to `agent_execute_command`.

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "command": "PartDesign_Thickness",
    "arguments": {
        "body_name": body_name,
        "intent": intent,
        "evidence": [source_evidence_id, target_evidence_id],
        "feature_name": feature_name,
        "predecessor_name": predecessor_name,
        "face_refs": opening_face_refs,
        "thickness_mm": thickness_mm,
        "direction": thickness_direction,
        "join": "arc",
    },
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `feature_name`: Choose an unused native name for this new feature.
- `predecessor_name`: Actual current solid Tip of the receiving Body.
- `opening_face_refs`: Copy current exact thickness_opening face references.
- `thickness_mm`: Measured wall thickness, mm.
- `thickness_direction`: Choose inward/outward from the observed material side.

Check actual openings, wall side, thickness, join and native result.
<!-- reagentfreecad:example:end -->
