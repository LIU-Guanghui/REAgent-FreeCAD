"""Shared session evidence guard for dedicated native feature adapters."""

from ..contract import IMAGE_OBSERVATION_KINDS, VIEW_DIRECTIONS
from ..errors import PublicError


def _image_prepared(fact):
    delivery = fact.get("image_delivery") if isinstance(fact, dict) else None
    return isinstance(delivery, dict) and delivery.get("status") == "prepared"


def validate_domain_evidence(args, record):
    """Require session-issued evidence from both roles at the claimed Target revision."""
    if record["blocked"]:
        raise PublicError("write_blocked", "public_preflight", "Target write outcome is unknown; only safe reads or explicit close are allowed", side_effect="unknown")
    if record["source_basis_unknown"]:
        raise ValueError("Source restoration is unknown; do not continue modeling in this session")
    facts = record["evidence_facts"]
    if record["observation_policy"] == "visual":
        preparation = record["initial_source_views"]
        evidence_id = preparation.get("evidence_id") if isinstance(preparation, dict) else None
        fact = facts.get(evidence_id) if isinstance(evidence_id, str) else None
        initial = fact.get("initial_views") if isinstance(fact, dict) else None
        if (not isinstance(fact, dict) or fact.get("kind") != "initial_views"
                or fact.get("role") != "source" or fact.get("revision") != record["source_revision"]
                or not _image_prepared(fact) or not isinstance(initial, dict)
                or initial.get("directions") != list(VIEW_DIRECTIONS)
                or initial.get("complete") is not True):
            raise ValueError(
                "Complete prepared Source initial_views are required before native modeling: "
                "check image_delivery.status, initial_views.directions and initial_views.complete")
    evidence = args["evidence"]
    if len(evidence) != len(set(evidence)):
        raise ValueError("Native command evidence IDs must be distinct")
    if any(item not in facts for item in evidence):
        raise ValueError("Native command evidence must belong to the bound session")
    if any(facts[item]["kind"] in IMAGE_OBSERVATION_KINDS
           and not _image_prepared(facts[item]) for item in evidence):
        raise ValueError("View evidence requires image_delivery.status=prepared; obtain usable images before writing")
    if not {facts[item]["role"] for item in evidence} >= {"source", "target"}:
        raise ValueError("Native command requires both Source and Target observation evidence")
    if any(facts[item]["role"] == "source" and facts[item]["revision"] != record["source_revision"]
           for item in evidence):
        raise ValueError("Source evidence must match the bound STEP revision")
    if not any(facts[item]["role"] == "target"
               and facts[item]["revision"] == args["expected_revision"]
               for item in evidence):
        raise ValueError("Native command requires Target evidence for the expected revision")
