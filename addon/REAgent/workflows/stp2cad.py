"""STP-specific evidence and delivery requirements."""

from REAgent.protocol.definitions import NATIVE_SOURCE_EXTENSIONS, validation_items
from .spec import Workflow, require_initial_views, require_source_global


def require_reference(record, observations):
    require_source_global(record, observations)
    if record["observation_policy"] == "visual":
        require_initial_views(record, observations)


def require_evidence(record, facts):
    if record["observation_policy"] == "visual":
        require_initial_views(record)
    if not any(fact["role"] == "source" for fact in facts):
        raise ValueError("STP modeling requires a bound Source observation and current Target evidence")


def delivery(record, args, observations, revision):
    comparison = record["recent_validation"]
    pair = record["recent_paired_views"]
    if (comparison.get("actor") != "model" or comparison.get("operation_id") != args["validation_operation_id"]
            or pair.get("evidence_id") != args["paired_evidence_id"]):
        raise ValueError("Reference the current model comparison and complete paired board: "
                         + str({"validation_operation_id": comparison.get("operation_id"),
                                "paired_evidence_id": pair.get("evidence_id")}))
    geometry_receipt = observations.get("geometry_comparison", {})
    image_receipt = observations.get("paired_views", {})
    if (geometry_receipt.get("operation_id") != comparison["operation_id"]
            or image_receipt.get("evidence_id") != pair["evidence_id"]
            or geometry_receipt.get("scope") != "model_request_completed"
            or image_receipt.get("scope") != "model_request_completed"):
        raise ValueError("The current geometry result and complete PNG board must enter an adopted action request")
    registered = record["evidence_facts"].get(pair["evidence_id"], {})
    registration = registered.get("image_registration") or {}
    revisions = {"source_revision": record["source_revision"], "target_revision": revision}
    if not revision:
        raise ValueError("Current Target revision is unavailable; delivery cannot be confirmed")
    if not registration:
        raise ValueError("Paired image evidence is not registered to the current task")
    if registration.get("actor") != "model":
        raise ValueError("Paired image evidence is not registered as a model capture")
    if registered.get("image_delivery", {}).get("status") != "prepared":
        raise ValueError("Paired image evidence is not prepared for delivery")
    if image_receipt.get("document") != record["target_document"]:
        raise ValueError("Adopted paired image receipt does not name the current Target document")
    identity = registration.get("paired_revisions") or {}
    if not identity:
        raise ValueError("Paired image evidence has no revision identity")
    if identity.get("source_revision") != revisions["source_revision"]:
        raise ValueError("Paired image Source revision is missing or differs from the bound Source")
    if identity.get("target_revision") != revisions["target_revision"]:
        raise ValueError("Paired image Target revision is missing or differs from the current Target")
    items = validation_items(comparison.get("requested", {}))
    if not items:
        raise ValueError("Delivery needs at least one requested geometric comparison")
    facts = comparison.get("facts", {})
    provenance = record.get("_recent_validation_provenance") or {}
    limitations = []
    available = False
    expected = {"document": record["target_document"], **revisions}
    if (geometry_receipt.get("document") != expected["document"]
            or geometry_receipt.get("revision") != revision):
        raise ValueError("Geometry receipt differs from the current Target document or revision")
    selected = [*items, *(["parameter_audit"] if comparison["requested"].get("parameter_audit") else [])]
    for item in selected:
        row = facts.get(item, {})
        if row.get("status") in (None, "pending", "running", "not_requested"):
            raise ValueError("Every selected delivery check must have an actual terminal result")
        dependency = provenance.get(item)
        required = expected if item != "parameter_audit" else {"document": expected["document"], "target_revision": revision}
        if not dependency or any(dependency.get(key) != value for key, value in required.items()):
            raise ValueError("A selected check has stale or unknown dependency revisions: " + item)
        usable = row.get("available") is True if item != "parameter_audit" else row.get("restored") is True
        if item in items and usable:
            available = True
        if not usable or row.get("status") in ("failed", "unavailable", "error"):
            limitations.append({"check": item, "status": row["status"], "reason": row.get("reason")})
    if not available:
        raise ValueError("All selected geometric comparisons are unavailable; no delivery receipt was issued")
    return {**revisions, "validation_operation_id": comparison["operation_id"],
            "paired_evidence_id": pair["evidence_id"], "checks": facts, "limitations": limitations}


TASK = Workflow(
    "stp2cad", "STP to CAD", "Reconstruct the input STP as one editable parametric part.",
    file_label="STP file",
    file_filter="STP (" + " ".join("*" + suffix for suffix in NATIVE_SOURCE_EXTENSIONS["stp"]) + ")",
    native_source="stp",
    require_reference=require_reference, require_evidence=require_evidence, delivery=delivery,
)
