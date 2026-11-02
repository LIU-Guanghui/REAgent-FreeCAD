"""Session evidence adaptation for explicit Source/Target observations."""

import uuid

from REAgent.rebuild.log_summary import observation_summary
from REAgent.rebuild.images import prepare_view_content, read_registered_images
from REAgent.protocol.observations import IMAGE_OBSERVATION_KINDS, validate_observation_role
from REAgent.rebuild.errors import PublicError


def run(name, args, record, started, ctx):
    _call = ctx.call
    _emit = ctx.emit
    if name == "agent_read_evidence":
        registration = record["evidence_facts"].get(args["evidence_id"], {}).get("image_registration")
        if registration is None:
            raise ValueError("Image evidence is not registered to the current task")
        delivery, content, previews = read_registered_images(
            registration, args["artifact_key"])
        context = ctx.call_context.get()
        context["image_content"] = content
        value = {"evidence_id": args["evidence_id"], "role": registration["role"],
                 "revision": registration["revision"], "paired_revisions": registration.get("paired_revisions"),
                 "initial_views": registration.get("initial_views"), "image_delivery": delivery,
                 "image_previews": previews, "capture_id": registration["capture_id"],
                 "capture_number": registration["capture_number"], "historical": True,
                 "source_basis": "registered_capture_revision", "cad_access": False}
        _emit(record, "observation.read_back", name, {"evidence_id": args["evidence_id"],
              "artifact_key": args["artifact_key"], "image_count": len(content), "cad_access": False},
              started, role=registration["role"])
        return value
    if name == "agent_observe":
        context = ctx.call_context.get()
        if context is None:
            raise RuntimeError("Observation evidence requires an Controller response context")
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
            delivery, content, registration, previews = prepare_view_content(
                value, record["artifacts"], evidence_id, args["role"])
            context["image_content"] = content
            value = {**value, "image_delivery": delivery, "image_previews": previews}
            fact["image_delivery"] = delivery
            if registration is not None:
                fact["image_registration"] = registration
            if query["kind"] == "initial_views":
                fact["initial_views"] = value["initial_views"]
        # Captured facts stay local until the Controller success response is constructed.
        context["pending_evidence"] = (record, evidence_id, fact)
        value = {**value, "evidence_id": evidence_id, "purpose": args.get("purpose")}
        _emit(record, "observation.completed", name, observation_summary(query, value), started, role=args["role"])
        return value
    raise ValueError("Unknown tool in workflow group")


def contribute():
    return {name: run for name in ('agent_observe', 'agent_read_evidence')}
