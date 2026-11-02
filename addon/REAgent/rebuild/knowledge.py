"""Read the Addon's single static knowledge catalog; no repository fallback."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from REAgent import ADDON_PATH

_RESOURCE_ROOT = Path(ADDON_PATH) / "knowledge"


@dataclass(frozen=True)
class KnowledgeAsset:
    knowledge_id: str
    relative_path: str
    purpose: str


def _resource(relative_path):
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise ValueError("Knowledge paths must be non-empty POSIX relative paths")
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts or ":" in relative_path:
        raise ValueError("Knowledge path leaves the resource directory")
    path = _RESOURCE_ROOT.joinpath(*relative.parts)
    path.resolve(strict=True).relative_to(_RESOURCE_ROOT.resolve(strict=True))
    if not path.is_file():
        raise ValueError("Knowledge resource is not a file: " + relative_path)
    return path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate knowledge catalog key: " + key)
        result[key] = value
    return result


def _load_catalog():
    catalog = json.loads(_resource("catalog.json").read_text(encoding="utf-8"),
                         object_pairs_hook=_unique_object)
    if not isinstance(catalog, dict) or set(catalog) != {"entry", "cards"}:
        raise ValueError("Knowledge catalog requires entry and cards")
    entry = catalog["entry"]
    _resource(entry)
    if not isinstance(catalog["cards"], list) or not catalog["cards"]:
        raise ValueError("Knowledge catalog has no cards")
    assets, ids, paths = [], set(), {"catalog.json", entry}
    if len(paths) != 2:
        raise ValueError("Knowledge entry must differ from its catalog")
    for row in catalog["cards"]:
        if not isinstance(row, dict) or set(row) != {"knowledge_id", "relative_path", "purpose"}:
            raise ValueError("Invalid knowledge catalog entry")
        if any(not isinstance(value, str) or not value.strip() for value in row.values()):
            raise ValueError("Knowledge catalog values must be non-empty text")
        asset = KnowledgeAsset(**row)
        if asset.knowledge_id in ids or asset.relative_path in paths:
            raise ValueError("Duplicate knowledge ID or resource path")
        _resource(asset.relative_path)
        ids.add(asset.knowledge_id)
        paths.add(asset.relative_path)
        assets.append(asset)
    actual = {path.relative_to(_RESOURCE_ROOT).as_posix()
              for path in _RESOURCE_ROOT.rglob("*") if path.is_file()}
    if actual != paths:
        raise ValueError("Knowledge directory differs from its sole catalog")
    return entry, tuple(assets)


_ENTRY, _ASSETS = _load_catalog()
_BY_ID = {asset.knowledge_id: asset for asset in _ASSETS}


def read_instructions():
    return _resource(_ENTRY).read_text(encoding="utf-8")


def read_knowledge(knowledge_id):
    try:
        asset = _BY_ID[knowledge_id]
    except KeyError as exc:
        raise ValueError("Unknown knowledge_id. Read the knowledge catalog first.") from exc
    content = _resource(asset.relative_path).read_text(encoding="utf-8")
    return {"knowledge_id": asset.knowledge_id, "purpose": asset.purpose,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "content": content}


def reconstruction_guidance():
    return {"mode": "agent_driven",
            "task": {"source_path": None, "output_dir": None,
                     "objective": "Create the simplest robust editable parametric feature tree."},
            "workflow_entry": "workflow/entry", "read_with": "agent_get_guidance(knowledge_id=...)",
            "knowledge_catalog": [{"knowledge_id": asset.knowledge_id, "purpose": asset.purpose}
                                  for asset in _ASSETS]}
