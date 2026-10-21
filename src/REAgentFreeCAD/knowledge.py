"""Portable access to the canonical Agent reconstruction knowledge assets."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KnowledgeAsset:
    knowledge_id: str
    relative_path: str
    purpose: str


def _asset(group: str, name: str, purpose: str) -> KnowledgeAsset:
    return KnowledgeAsset(
        f"{group}/{name}",
        f".agents/skills/reagentfreecad/references/{group}/{name}.md",
        purpose,
    )


_ASSETS = (
    _asset("workflow", "entry", "End-to-end iterative reconstruction workflow."),
    _asset(
        "workflow",
        "session-and-isolation",
        "Source/Target process, visibility, ownership, and persistence boundaries.",
    ),
    _asset(
        "workflow",
        "observation",
        "Question-driven dimensional and visual observations.",
    ),
    _asset(
        "workflow",
        "validation",
        "Native result readiness, explicit comparison and checkpoint facts.",
    ),
    _asset("features", "entry", "Routing for the retained feature families."),
    _asset("features", "body", "Main and tool Body modeling judgment."),
    _asset("features", "sketch", "Native Sketch profile and constraint judgment."),
    _asset("features", "primitives", "Native additive and subtractive primitive judgment."),
    _asset("features", "datum", "Stable Datum and local-frame judgment."),
    _asset("features", "extrude", "Pad and Pocket semantics."),
    _asset("features", "revolve", "Native Revolution and Groove modeling judgment."),
    _asset("features", "boolean", "Native Fuse, Cut and Common operand judgment."),
    _asset("features", "hole", "Coaxial subtractive Hole semantics."),
    _asset("features", "loft", "Ordered section transition and native Loft judgment."),
    _asset("features", "pipe", "Section transport along a continuous path and native Pipe judgment."),
    _asset("features", "pattern", "Linear and Polar Pattern semantics."),
    _asset("features", "mirror", "Mirror semantics."),
    _asset("features", "fillet", "Constant-radius Fillet semantics."),
    _asset("features", "chamfer", "Equal- and two-distance Chamfer semantics."),
    _asset("features", "draft", "Explicit neutral-plane Draft judgment."),
    _asset("features", "thickness", "Opening-face and material-side Thickness judgment."),
    _asset("foundations", "entry", "Routing for general CAD and FreeCAD foundations."),
    _asset("foundations", "brep-topology", "B-Rep topology and material boundary."),
    _asset(
        "foundations",
        "geometry-parameterization",
        "Geometry carriers, trimming, orientation, and continuity.",
    ),
    _asset(
        "foundations",
        "tolerance-validity",
        "Tolerance, validity and topology-changing repair cautions.",
    ),
    _asset(
        "foundations",
        "persistent-references",
        "Current native object/subelement references and semantic re-resolution.",
    ),
    _asset(
        "foundations",
        "transactions-errors",
        "Transactions, operation history, rollback, and error channels.",
    ),
    _asset(
        "foundations",
        "freecad-document",
        "FreeCAD document, recompute, reference and persistence distinctions.",
    ),
)

_BY_ID = {asset.knowledge_id: asset for asset in _ASSETS}


def _read_relative_asset(relative_path: str) -> str:
    repository_path = Path(__file__).resolve().parents[2] / relative_path
    if repository_path.is_file():
        return repository_path.read_text(encoding="utf-8")

    packaged = resources.files("REAgentFreeCAD").joinpath(
        "knowledge_assets", *relative_path.split("/")
    )
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Packaged knowledge asset is unavailable: {relative_path}")


def read_knowledge(knowledge_id: str) -> dict[str, Any]:
    """Return one allowlisted canonical knowledge asset."""

    try:
        asset = _BY_ID[knowledge_id]
    except KeyError as exc:
        raise ValueError("Unknown knowledge_id. Call agent_get_guidance for the catalog.") from exc
    content = _read_relative_asset(asset.relative_path)
    return {
        "knowledge_id": asset.knowledge_id,
        "purpose": asset.purpose,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content": content,
    }


def reconstruction_guidance(
    *,
    source_path: str | None = None,
    output_dir: str | None = None,
    objective: str | None = None,
    include_catalog: bool = True,
) -> dict[str, Any]:
    """Expose discoverable knowledge IDs without injecting Skill text."""

    return {
        "mode": "agent_driven",
        "task": {
            "source_path": source_path,
            "output_dir": output_dir,
            "objective": objective
            or "Create the simplest robust editable parametric feature tree.",
        },
        "workflow_entry": "workflow/entry",
        "read_with": "agent_get_guidance(knowledge_id=...)",
        **(
            {"knowledge_catalog": [
                {"knowledge_id": asset.knowledge_id, "purpose": asset.purpose}
                for asset in _ASSETS
            ]}
            if include_catalog else {}
        ),
    }


__all__ = ["read_knowledge", "reconstruction_guidance"]
