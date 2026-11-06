# Body ownership

Keep one main Body for the intended single-solid result. Create a separate tool Body when an independent solid history is needed before a deliberate Boolean combines it with the main result.

An empty or disconnected tool Body is an `in_progress` intermediate. Judge completion from the actual dependency chain and Result.

<!-- reagentfreecad:example:begin -->
## Native command argument template

Use when the construction needs a new independent tool Body.

Use this Python dictionary as a parameter template for the named native command. Bind every variable below to actual returned values and measurements, then submit the discovered command and this dictionary through agent_execute_command. Discover its exact schema on an earlier completed request. Choose other modes from current capabilities.

Native command: `PartDesign_Body`

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "body_name": new_body_name,
    "intent": intent,
    "evidence": [source_evidence_id, target_evidence_id],
    "expected_revision": target_revision,
}
```

- `new_body_name`: Choose an unused native name for the new tool Body.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `target_revision`: Current Target revision matching the referenced Target observation.

Use the returned Body name and updated revision in later calls.
<!-- reagentfreecad:example:end -->
