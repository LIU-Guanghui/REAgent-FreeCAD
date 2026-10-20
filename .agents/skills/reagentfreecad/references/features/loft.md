# Loft

Use Loft for a transition between ordered, evidenced section profiles. Place each section as one closed boundary on its measured plane; order and orientation can change surface shape and twist.

Infer neither intermediate sections nor inner contours from the outer silhouette alone, and do not assume automatic twist correction.

<!-- reagentfreecad:example:begin -->
## MCP request template

Two caller-ordered closed same-Body sections on distinct planes explain the transition.

Use this Python dictionary as a parameter template for the named MCP tool. Bind every variable below to actual returned values and measurements before constructing arguments, then pass arguments to that tool. Choose other modes from current capabilities.

Tool: `PartDesign_AdditiveLoft`

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "session_id": session_id,
    "body_name": body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
    "feature_name": feature_name,
    "profile_sketch_name": profile_sketch_name,
    "section_sketch_name": section_sketch_name,
}
```

- `session_id`: Current returned session ID.
- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `target_revision`: Current Target revision returned by observation/state.
- `feature_name`: Choose an unused native name for this new feature.
- `profile_sketch_name`: Actual same-Body closed profile Sketch name.
- `section_sketch_name`: Actual second ordered section on a distinct plane.

Check ordered section links and the native result.
<!-- reagentfreecad:example:end -->
