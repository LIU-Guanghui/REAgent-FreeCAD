"""Shared session evidence guard for dedicated native feature adapters."""

from REAgent.cad.errors import PublicError
from REAgent.cad.workflow_context import TARGET_STATE_EVIDENCE, evidence_issue


def validate_vertex_evidence(reference, args, record):
    if not reference or reference.get("kind") != "feature_vertex":
        return
    evidence = record["evidence_facts"]
    if not any(
        fact["role"] == "target" and fact["kind"] == "feature"
        and fact["revision"] == args["expected_revision"] and fact["body"] == args["body_name"]
        and fact["object"] == reference["feature_name"]
        and reference["vertex_index"] in fact.get("vertex_indices", ())
        for fact in (evidence[item] for item in args["evidence"])
    ):
        raise ValueError("Observe the requested VertexN on the named Target feature in this Body and revision")


def validate_domain_evidence(args, record, *, scope):
    """Bind a native write to the current revision of its cited Target evidence."""
    if record["blocked"]:
        raise PublicError("write_blocked", "public_preflight", "Target write outcome is unknown; only safe reads or explicit close are allowed", side_effect="unknown")
    if record["source_basis_unknown"]:
        raise ValueError("Source restoration is unknown; do not continue modeling in this session")
    facts = record["evidence_facts"]
    evidence = args["evidence"]
    if len(evidence) != len(set(evidence)):
        raise ValueError("Native command evidence IDs must be distinct")
    current = {"target": record.get("current_target_revision"), "source": record["source_revision"]}
    cited = []
    for index, evidence_id in enumerate(evidence):
        fact = facts.get(evidence_id)
        role = fact["role"] if fact is not None else "target"
        document, revision = record[role + "_document"], current[role]
        issue = evidence_issue(fact, role, document, revision)
        if issue is not None:
            path = f"evidence[{index}]"
            hint = "Use evidence for the current model."
            if fact is not None and fact["kind"] == TARGET_STATE_EVIDENCE:
                state_id = next(
                    (item for item, known in reversed(facts.items())
                     if known["kind"] == TARGET_STATE_EVIDENCE
                     and evidence_issue(known, role, document, revision) is None),
                    None,
                )
                hint = (
                    f"Remove this entry and cite the current Target state ID {state_id} once."
                    if state_id is not None else
                    "Remove this entry. Use agent_get_state if current Target evidence is missing."
                )
            elif fact is not None and role == "target":
                hint = "Remove this entry and observe the current Target for any geometry still needed."
            error = PublicError("invalid_request", "public_validation",
                                f"{path} cites {evidence_id}, which {issue}. {hint}")
            error.field_path = path
            raise error
        cited.append(fact)
    require_evidence = record["workflow"].require_evidence
    if scope != "target_edit" and require_evidence is not None:
        require_evidence(record, cited)
    for fact in cited:
        if fact["role"] == "target":
            return fact["revision"]
    error = PublicError("invalid_request", "public_validation",
                        "Include a current Target evidence ID. Use agent_get_state if Target state evidence is missing.")
    error.field_path = "evidence"
    raise error
