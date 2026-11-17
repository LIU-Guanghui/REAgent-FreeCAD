"""The input, evidence and delivery differences between CAD workflows."""

from dataclasses import dataclass
from typing import Callable

from REAgent.protocol.observations import VIEW_DIRECTIONS


@dataclass(frozen=True)
class Workflow:
    name: str
    title: str
    objective: str
    file_label: str | None = None
    file_filter: str = ""
    native_source: str | None = None
    image_inputs: bool = False
    require_reference: Callable | None = None
    require_evidence: Callable | None = None
    delivery: Callable | None = None
    delivery_queries: tuple[tuple[str, ...], ...] = ()
    require_dimension_readback: bool = False

    @property
    def requires_images(self):
        return bool(self.native_source or self.image_inputs)

    @property
    def compares_stp(self):
        return self.native_source == "stp"

    @property
    def paired_views(self):
        return self.native_source in ("stp", "mesh")


def require_source_global(record, observations):
    if observations.get("global", {}).get("revision") != record["source_revision"]:
        raise ValueError("Use the bound Source global observation before modeling")


def require_initial_views(record, observations=None):
    prepared = record["initial_source_views"] or {}
    evidence_id = prepared.get("evidence_id")
    fact = record["evidence_facts"].get(evidence_id, {})
    initial = fact.get("initial_views", {})
    if (fact.get("role") != "source" or fact.get("kind") != "initial_views"
            or fact.get("revision") != record["source_revision"]
            or fact.get("image_delivery", {}).get("status") != "prepared"
            or initial.get("directions") != list(VIEW_DIRECTIONS)
            or initial.get("complete") is not True
            or (observations is not None
                and observations.get("initial_views", {}).get("evidence_id") != evidence_id)):
        raise ValueError("Use the complete, adopted 14-direction Source initial views before modeling")


def require_input_view(record, observations):
    viewed = observations.get("input_view", {})
    if (viewed.get("scope") != "model_request_completed"
            or viewed.get("revision") not in {item["revision"] for item in record["inputs"]}):
        raise ValueError("Use agent_view_input and receive the selected reference before modeling")
