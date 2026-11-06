"""Trusted action scope checks, independent of model-supplied intent text."""

from REAgent.protocol.observations import IMAGE_OBSERVATION_KINDS
from REAgent.protocol.operations import OPERATION_SPECS, request_side_effect
from REAgent.rebuild.tools import MODEL_TOOLS


def allowed_tools(scope=None):
    names = set(MODEL_TOOLS)
    if scope is not None:
        excluded = ("agent_validate", "agent_finish_reconstruction") if scope == "target_edit" else ("agent_resume_reconstruction",)
        names.difference_update(excluded)
    return names


def guard(record, name, args, context):
    if not context:
        return
    if context["binding"] != record["session_id"]:
        raise ValueError("Execution context belongs to a different task")
    if context["actor"] != "model":
        return
    if context["purpose"] == "report":
        raise ValueError("This delivered run can only publish its final report")
    if "delivery-" + context["run_id"] in record["delivery_receipts"] and name != "agent_finish_reconstruction":
        raise ValueError("This reconstruction run already delivered; a new user action is required")
    if name not in allowed_tools(context["scope"]):
        raise ValueError("Tool is outside the current action scope; use /compare for Source comparisons")
    query = args.get("request", {})
    kind = query.get("kind")
    if context["scope"] == "target_edit":
        if name == "agent_observe" and (args.get("role") != "target" or kind in (*IMAGE_OBSERVATION_KINDS, "residual")):
            raise ValueError("This action allows only non-image Target observations; use /compare for images")
    elif (name != "agent_observe" and name in OPERATION_SPECS
          and request_side_effect(name, args) != "none"):
        seen = context["observations"]
        initial = record["initial_source_views"] or {}
        if (seen.get("global", {}).get("revision") != record["source_revision"]
                or record["observation_policy"] == "visual" and (
                    not initial.get("evidence_id")
                    or seen.get("initial_views", {}).get("evidence_id") != initial["evidence_id"])):
            raise ValueError("Source global facts and complete initial PNG must enter an adopted model request before modeling")
