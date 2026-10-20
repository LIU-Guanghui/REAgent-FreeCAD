"""Session evidence adaptation for explicit Source/Target observations."""

import uuid

from ..log_summary import observation_summary
from ..view_delivery import prepare_view_content
from ..contract import IMAGE_OBSERVATION_KINDS, validate_observation_role
from ..errors import PublicError


def run(name, args, record, started, ctx):
    _call = ctx.call
    _emit = ctx.emit
    if name == "agent_observe":
        context = ctx.call_context.get()
        if context is None:
            raise RuntimeError("Observation evidence requires an MCP response context")
        query = args["request"]
        validate_observation_role(query["kind"], query["query"], args["role"])
        value = _call(record, "agent_observe", args["role"], kind=query["kind"], query=query["query"])
        revision = value.get("revision")
        if not isinstance(revision, str) or not revision:
            raise RuntimeError("Bound observation did not return a model revision")
        if args["role"] == "source" and revision != record["source_revision"]:
            raise PublicError("source_revision_mismatch", "public_response", "Source observation differs from the bound STEP revision",
                              side_effect=context["side_effect"])
        if "paired_revisions" in value and value["paired_revisions"]["source_revision"] != record["source_revision"]:
            raise PublicError("source_revision_mismatch", "public_response", "Paired Source snapshot differs from the bound STEP revision; use a new session for the chosen source",
                              side_effect=context["side_effect"])
        evidence_id = "evidence-" + uuid.uuid4().hex[:20]
        fact = {"role": args["role"], "revision": revision, "kind": query["kind"]}
        if query["kind"] in IMAGE_OBSERVATION_KINDS:
            delivery, content = prepare_view_content(value, record["artifacts"], evidence_id, args["role"])
            context["image_content"] = content
            value = {**value, "image_delivery": delivery}
            fact["image_delivery"] = delivery
            if query["kind"] == "initial_views":
                fact["initial_views"] = value["initial_views"]
        # Captured facts stay local until the MCP success response is constructed.
        context["pending_evidence"] = (record, evidence_id, fact)
        value = {**value, "evidence_id": evidence_id, "purpose": args.get("purpose")}
        _emit(record, "observation.completed", name, observation_summary(query, value), started, role=args["role"])
        return value
    raise ValueError("Unknown tool in workflow group")
