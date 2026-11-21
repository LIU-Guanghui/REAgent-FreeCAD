"""Native current references and exact link readback shared by feature commands."""

from REAgent.host.analysis.evidence import geometry_evidence
from REAgent.host.analysis.selection import FaceSelectionResolutionError, SelectionResolutionError, feature_semantic_refs, resolve_dressup_selection, resolve_face_selection
from REAgent.protocol.features import DRESSUP_SCOPE
from REAgent.host.validation.structure import single_solid_summary
from REAgent.host.validation.tolerance import ir_tolerance_contract
from REAgent.host.workspace.model import predecessor_shape
from REAgent.host.workspace.properties import metadata
from REAgent.host.features.construction import history_context, shape_readback


def dressup_edges(body, group, request, tip, *, shape=None):
    if tip is None or tip.Name != request["predecessor_name"]:
        raise ValueError("Dress-up predecessor must be the current Body Tip")
    references = feature_semantic_refs(
        request["edge_refs"], "edge", "dressup_edge", "edge_refs", DRESSUP_SCOPE["edge_limit"])
    if any(ref["owner"].get("object_name") != tip.Name for ref in references):
        raise ValueError("Dress-up edge reference belongs to another predecessor")
    contract = ir_tolerance_contract(group)
    shape = predecessor_shape(body) if shape is None else shape
    single_solid_summary(shape, "Dress-up predecessor")
    evidence = geometry_evidence(shape, contract, include_relations=False,
                                  include_repetition=False)
    revision = evidence["stable_selection"]["model_revision"]
    if any(ref["owner"]["revision"] != revision for ref in references):
        raise ValueError("Dress-up edge geometry revision changed; re-observe")
    report = resolve_dressup_selection(evidence, {"edge_refs": references}, contract)
    if not report["all_resolved"]:
        raise SelectionResolutionError(report)
    if any(row.get("matched_by") != "geometry_and_topology_signatures"
           for row in report["results"]):
        raise ValueError("Dress-up edges require unique exact current signatures")
    names = report["matched_names"]
    if len(names) != len(references) or len(names) != len(set(names)):
        raise ValueError("Dress-up edge set is ambiguous or duplicated")
    return names


def set_dressup_base(feature, tip, edge_names):
    meta = metadata(feature, "Base")
    if not meta["writable"] or meta["codec"] != "link_sub":
        raise ValueError("Native dress-up Base is not a writable edge LinkSub")
    feature.Base = (tip, edge_names)


def _dressup_base_readback(feature, tip, edge_names):
    value = feature.Base
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or value[0] is not tip or list(value[1]) != edge_names
            or tip not in feature.OutList):
        raise RuntimeError("Native dress-up Base changed its predecessor or edges")


def edge_dressup_context(document, group, request):
    body, _profile, tip = history_context(document, group, request, require_solid=True)
    return body, tip, dressup_edges(body, group, request, tip)


def edge_dressup_readback(document, feature, name, type_id, context):
    body, tip, edges = context
    shape = shape_readback(document, body, feature, name, type_id, tip)
    _dressup_base_readback(feature, tip, edges)
    for prop in ("UseAllEdges", "Refine", "SupportTransform"):
        if prop in feature.PropertiesList and bool(getattr(feature, prop)):
            raise RuntimeError("Native dress-up fixed option changed: " + prop)
    return {"feature": name, "body": body.Name, "type_id": feature.TypeId,
            "predecessor": tip.Name, "edges": edges, "tip": body.Tip.Name, "body_shape": shape}


def current_faces(body, group, request, tip, refs, role, parameter_name,
                   evidence=None, *, shape=None):
    if tip is None or tip.Name != request["predecessor_name"]:
        raise ValueError("Face dress-up predecessor must be the current Body Tip")
    references = feature_semantic_refs(refs, "face", role, parameter_name, 64)
    if any(ref["owner"].get("object_name") != tip.Name
           or ref.get("fallback") is not None for ref in references):
        raise ValueError("Face references require the current predecessor and no fallback")
    if evidence is None:
        shape = predecessor_shape(body) if shape is None else shape
        single_solid_summary(shape, "Face dress-up predecessor")
        evidence = geometry_evidence(
            shape, ir_tolerance_contract(group), include_relations=False,
            include_repetition=False)
    report = resolve_face_selection(
        evidence, {"face_refs": references}, role)
    if not report["all_resolved"]:
        raise FaceSelectionResolutionError(report)
    if any(row["matched_by"] != "geometry_and_topology_signatures"
           for row in report["results"]):
        raise ValueError("Face references require unique exact current signatures")
    names = report["matched_names"]
    if len(names) != len(references) or len(names) != len(set(names)):
        raise ValueError("Face references are ambiguous or duplicated")
    if len(tip.Shape.Faces) != len(evidence["faces"]):
        raise ValueError("Body and predecessor face counts differ")
    return names, evidence


def set_face_base(feature, tip, names):
    meta = metadata(feature, "Base")
    if not meta["writable"] or meta["codec"] != "link_sub":
        raise ValueError("Native face dress-up Base is not a writable LinkSub")
    feature.Base = (tip, names)


def link_sub_readback(feature, property_name, object_ref, names):
    value = getattr(feature, property_name)
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or value[0] is not object_ref or list(value[1]) != names
            or object_ref not in feature.OutList):
        raise RuntimeError("Native %s changed its linked object or faces" % property_name)


def role_object(container, role, type_id, document):
    matches = [obj for obj in getattr(container, "OriginFeatures", [])
               if getattr(obj, "Role", None) == role]
    if (len(matches) != 1 or matches[0].TypeId != type_id
            or matches[0].Document is not document
            or document.getObject(matches[0].Name) is not matches[0]):
        raise ValueError("Native reference role is missing or ambiguous: " + role)
    return matches[0]
