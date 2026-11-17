# Boolean

Use Fuse, Cut or Common when combining distinct owned solid Bodies explains the history. Choose the receiving Body, ordered tools and operation explicitly; preserve each tool's editable history.

On native failure, inspect operand validity, material contact and self-intersection before revising the construction.

<!-- reagentfreecad:example:begin -->
## Native command argument template

The main receiving Body and exactly one registered solid tool Body support the intended intersection.

Bind the variables to actual readbacks and measurements. Read the command schema with `agent_get_capabilities` and pass this dictionary to `agent_execute_command`.

```python
# Bind all variables to actual returned values and measurements before use.
arguments = {
    "command": "PartDesign_Boolean",
    "arguments": {
        "body_name": body_name,
        "intent": intent,
        "evidence": [source_evidence_id, target_evidence_id],
        "feature_name": feature_name,
        "mode": "Common",
        "tool_body_names": tool_body_names,
    },
}
```

- `body_name`: Actual owned receiving Body name.
- `intent`: Explain this operation's measured geometric purpose.
- `source_evidence_id`: Source evidence actually issued in this session.
- `target_evidence_id`: Current applicable Target evidence actually issued in this session.
- `feature_name`: Choose an unused native name for this new feature.
- `tool_body_names`: Actual registered tool Body names; this Common example uses exactly one.

Check actual operands, native operation and resulting receiving Body Tip.
<!-- reagentfreecad:example:end -->
