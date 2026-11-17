# Loft

Use Loft for a transition between ordered, evidenced section profiles. Place each section as one closed boundary on its measured plane; order and orientation can change surface shape and twist.

Infer neither intermediate sections nor inner contours from the outer silhouette alone, and do not assume automatic twist correction.

<!-- reagentfreecad:example:begin -->
## Native command argument template

Two caller-ordered closed same-Body sections on distinct planes explain the transition.

Bind the variables to actual readbacks and measurements. Read the command schema with `agent_get_capabilities` and pass this dictionary to `agent_execute_command`.

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "command": "PartDesign_AdditiveLoft",
    "arguments": {
        "body_name": body_name,
        "intent": intent,
        "evidence": [source_evidence_id, target_evidence_id],
        "feature_name": feature_name,
        "profile_sketch_name": profile_sketch_name,
        "section_sketch_name": section_sketch_name,
    },
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `feature_name`: Choose an unused native name for this new feature.
- `profile_sketch_name`: Actual same-Body closed profile Sketch name.
- `section_sketch_name`: Actual second ordered section on a distinct plane.

Check ordered section links and the native result.
<!-- reagentfreecad:example:end -->
