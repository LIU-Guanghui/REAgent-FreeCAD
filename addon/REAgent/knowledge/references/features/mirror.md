# Mirrored

Use Mirrored for identified, ordered native features reflected about a stable plane. Whole-model symmetry alone does not establish that history; resolve the intended plane in the current Target state.

<!-- reagentfreecad:example:begin -->
## Native command argument template

Choose actual same-Body seed features and an explicit plane supported by symmetry evidence.

Use this Python dictionary as a parameter template for the named native command. Bind every variable below to actual returned values and measurements, then submit the discovered command and this dictionary through agent_execute_command. Discover its exact schema on an earlier completed request. Choose other modes from current capabilities.

Native command: `PartDesign_Mirrored`

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "body_name": body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
    "feature_name": feature_name,
    "predecessor_name": predecessor_name,
    "source_feature_names": source_feature_names,
    "mirror_plane": {
        "kind": "origin_plane",
        "role": mirror_plane_role,
    },
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `target_revision`: Current Target revision matching the referenced Target observation.
- `feature_name`: Choose an unused native name for this new feature.
- `predecessor_name`: Actual current solid Tip of the receiving Body.
- `source_feature_names`: Actual caller-ordered additive/subtractive seed feature names.
- `mirror_plane_role`: Chosen same-Body Origin plane matching the observed symmetry.

Check ordered source links, plane and resulting instances.
<!-- reagentfreecad:example:end -->
