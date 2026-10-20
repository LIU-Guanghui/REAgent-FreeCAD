# Mirrored

Use Mirrored for identified, ordered native features reflected about a stable plane. Whole-model symmetry alone does not establish that history; resolve the intended plane in the current Target state.

<!-- reagentfreecad:example:begin -->
## MCP request template

Choose actual same-Body seed features and an explicit plane supported by symmetry evidence.

Use this Python dictionary as a parameter template for the named MCP tool. Bind every variable below to actual returned values and measurements before constructing arguments, then pass arguments to that tool. Choose other modes from current capabilities.

Tool: `PartDesign_Mirrored`

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
    "source_feature_names": source_feature_names,
    "mirror_plane": {
        "kind": "origin_plane",
        "role": mirror_plane_role,
    },
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
- `source_feature_names`: Actual caller-ordered additive/subtractive seed feature names.
- `mirror_plane_role`: Chosen same-Body Origin plane matching the observed symmetry.

Check ordered source links, plane and resulting instances.
<!-- reagentfreecad:example:end -->
