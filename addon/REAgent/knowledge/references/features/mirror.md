# Mirrored

Use Mirrored for identified, ordered native features reflected about a stable plane. Whole-model symmetry alone does not establish that history; resolve the intended plane in the current Target state.

<!-- reagentfreecad:example:begin -->
## Native command argument template

Choose actual same-Body seed features and an explicit plane supported by symmetry evidence.

Bind the variables to actual readbacks and measurements. Read the command schema with `agent_get_capabilities` and pass this dictionary to `agent_execute_command`.

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "command": "PartDesign_Mirrored",
    "arguments": {
        "body_name": body_name,
        "intent": intent,
        "evidence": [source_evidence_id, target_evidence_id],
        "feature_name": feature_name,
        "predecessor_name": predecessor_name,
        "source_feature_names": source_feature_names,
        "mirror_plane": {
            "kind": "origin_plane",
            "role": mirror_plane_role,
        },
    },
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `feature_name`: Choose an unused native name for this new feature.
- `predecessor_name`: Actual current solid Tip of the receiving Body.
- `source_feature_names`: Actual caller-ordered additive/subtractive seed feature names.
- `mirror_plane_role`: Chosen same-Body Origin plane matching the observed symmetry.

Check ordered source links, plane and resulting instances.
<!-- reagentfreecad:example:end -->
