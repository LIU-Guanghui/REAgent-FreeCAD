# Thickness

Identify the opening faces, cavity, wall thickness and material side. Choose inward or outward shelling to preserve the intended envelope, checking narrow gaps, tight curvature and neighbouring details.

Corner treatment and feature order affect the material. Diagnose native failures or shape differences, and explain any change of openings, wall side or construction method.

<!-- reagentfreecad:example:begin -->
## Native command argument template

Current opening-face references, wall measurements and material side support hollowing.

Use this Python dictionary as a parameter template for the named native command. Bind every variable below to actual returned values and measurements, then submit the discovered command and this dictionary through agent_execute_command. Discover its exact schema on an earlier completed request. Choose other modes from current capabilities.

Native command: `PartDesign_Thickness`

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "body_name": body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
    "feature_name": feature_name,
    "predecessor_name": predecessor_name,
    "face_refs": opening_face_refs,
    "thickness_mm": thickness_mm,
    "direction": thickness_direction,
    "join": "arc",
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `target_revision`: Current Target revision matching the referenced Target observation.
- `feature_name`: Choose an unused native name for this new feature.
- `predecessor_name`: Actual current solid Tip of the receiving Body.
- `opening_face_refs`: Copy current exact thickness_opening face references.
- `thickness_mm`: Measured wall thickness, mm.
- `thickness_direction`: Choose inward/outward from the observed material side.

Check actual openings, wall side, thickness, join and native result.
<!-- reagentfreecad:example:end -->
