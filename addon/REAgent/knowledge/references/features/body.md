# Body ownership

Keep one main Body for the intended single-solid result. Create a separate tool Body when an independent solid history is needed before a deliberate Boolean combines it with the main result.

An empty or disconnected tool Body is an `in_progress` intermediate. Judge completion from the actual dependency chain and Result.

<!-- reagentfreecad:example:begin -->
## Native command argument template

Use when the construction needs a new independent tool Body.

Bind the variables to actual readbacks and measurements. Read the command schema with `agent_get_capabilities` and pass this dictionary to `agent_execute_command`.

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "command": "PartDesign_Body",
    "arguments": {
        "body_name": new_body_name,
        "intent": intent,
        "evidence": [source_evidence_id, target_evidence_id],
    },
}
```

- `new_body_name`: Choose an unused native name for the new tool Body.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.

Use the returned Body name and confirmed commit evidence for the next write.
<!-- reagentfreecad:example:end -->
