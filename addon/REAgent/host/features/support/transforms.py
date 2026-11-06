"""Ordered native tool sources and exact references for PartDesign transforms."""

from REAgent.host.analysis.evidence import _geometry_evidence
from REAgent.host.analysis.selection import SelectionResolutionError, _feature_semantic_refs, _resolve_dressup_selection
from REAgent.host.validation.structure import _single_solid_summary
from REAgent.host.validation.tolerance import _ir_tolerance_contract
from REAgent.host.workspace.model import _body_objects, _predecessor_shape
from REAgent.host.workspace.properties import _metadata
from REAgent.host.features.support.construction import _history_context
from REAgent.host.features.support.references import _current_faces, _role_object

_TRANSFORM_TYPES = ("PartDesign::Mirrored", "PartDesign::LinearPattern",
                    "PartDesign::PolarPattern", "PartDesign::MultiTransform",
                    "PartDesign::Scaled")


def _active_feature_chain(tip, body):
    """Follow current same-Body feature dependencies, never Body.Group as a selector."""
    members = {obj.Name: obj for obj in body.Group}
    reached = set()
    pending = [tip.Name]
    while pending:
        name = pending.pop()
        if name in reached or name not in members:
            continue
        reached.add(name)
        pending.extend(dep.Name for dep in members[name].OutList
                       if dep.Name in members and dep.TypeId.startswith("PartDesign::")
                       and dep.TypeId != "PartDesign::Body")
    return reached


def _transform_context(document, group, request):
    context = _history_context(document, group, request, require_solid=True)
    body, _sketch, tip = context
    if tip is None or tip.Name != request["predecessor_name"]:
        raise ValueError("Transformation predecessor must be the current Body Tip")
    return context, _transform_sources(document, body, tip, request["source_feature_names"])


def _transform_sources(document, body, tip, names):
    group_order = list(body.Group)
    tip_index = group_order.index(tip)
    chain = _active_feature_chain(tip, body)
    if not 1 <= len(names) <= 16:
        raise ValueError("Transformation requires one to 16 ordered source features")
    if len(names) != len(set(names)):
        raise ValueError("Transformation source features must be distinct")
    sources = []
    for name in names:
        feature = document.getObject(name)
        if (feature is None or feature.Document is not document
                or feature not in body.Group or feature.Name not in chain
                or group_order.index(feature) > tip_index):
            raise ValueError("Transformation source is not in the current Body history: " + name)
        if (feature.TypeId in _TRANSFORM_TYPES
                or not callable(getattr(feature, "isDerivedFrom", None))
                or not feature.isDerivedFrom("PartDesign::FeatureAddSub")):
            raise ValueError("Transformation source must be a native additive/subtractive feature: " + name)
        if ("AddSubShape" not in feature.PropertiesList
                or feature.AddSubShape.isNull()
                or not feature.AddSubShape.isValid()
                or not feature.AddSubShape.Solids):
            raise ValueError("Transformation source has no valid native tool shape: " + name)
        if ("Suppressed" in feature.PropertiesList and feature.Suppressed):
            raise ValueError("Transformation source is suppressed: " + name)
        if any(str(state).lower() in ("touched", "invalid", "error")
               for state in feature.State):
            raise ValueError("Transformation source is not recomputed: " + name)
        sources.append(feature)
    return sources


def _check_datum_supports(document, body, datum):
    """Check only the Datum attachment chain within the receiving Body."""
    members = {obj.Name: obj for obj in _body_objects(body) if obj is not body}
    cs_parents = {}
    for obj in body.Group:
        if obj.TypeId == "Part::LocalCoordinateSystem":
            for child in obj.OriginFeatures:
                if (child is None or child.Document is not document
                        or members.get(child.Name) is not child
                        or child.Name in cs_parents):
                    raise ValueError("Datum support Coordinate System children are unavailable or ambiguous")
                cs_parents[child.Name] = obj
    active, checked = set(), set()

    def visit(obj):
        if (obj is None or obj.Document is not document
                or members.get(obj.Name) is not obj):
            raise ValueError("Mirror Datum supports must belong to the same Body and document")
        if obj.Name in active:
            raise ValueError("Mirror Datum attachment support chain contains a cycle")
        if obj.Name in checked:
            return
        active.add(obj.Name)
        if obj.Name in cs_parents:
            visit(cs_parents[obj.Name])
        elif obj.TypeId in ("Part::DatumPlane", "Part::DatumLine", "Part::DatumPoint",
                            "Part::LocalCoordinateSystem"):
            if ("AttachmentSupport" not in obj.PropertiesList
                    or obj.getTypeIdOfProperty("AttachmentSupport") != "App::PropertyLinkSubList"):
                raise ValueError("Datum AttachmentSupport must be a native LinkSubList")
            supports = obj.AttachmentSupport
            if not isinstance(supports, (tuple, list)):
                raise ValueError("Datum AttachmentSupport is unavailable")
            for link in supports:
                if (not isinstance(link, (tuple, list)) or len(link) != 2
                        or not isinstance(link[1], (tuple, list))
                        or any(not isinstance(sub, str) for sub in link[1])):
                    raise ValueError("Datum AttachmentSupport contains an incomplete native link")
                visit(link[0])
        active.remove(obj.Name)
        checked.add(obj.Name)

    visit(datum)


def _transform_plane(document, body, group, tip, request, *, shape=None):
    choice = request["mirror_plane"]
    if choice["kind"] == "origin_plane":
        origin = body.Origin
        if origin is None:
            raise ValueError("Transformation Body has no native Origin")
        return _role_object(origin, choice["role"], "App::Plane", document), [""]
    if choice["kind"] == "predecessor_face":
        names, evidence = _current_faces(
            body, group, request, tip, [choice["face_ref"]],
            "mirror_plane", "mirror_plane.face_ref", shape=shape)
        detail = next(face for face in evidence["faces"] if face["name"] == names[0])
        if detail["surface_type"] != "Plane":
            raise ValueError("Mirror plane reference must be planar")
        return tip, names
    if choice["kind"] == "datum_plane":
        obj = document.getObject(choice["datum_name"])
        if (obj is None or obj.TypeId != "Part::DatumPlane"
                or obj.Document is not document or obj not in body.Group):
            raise ValueError("Mirror plane requires a same-Body Datum Plane")
        _check_datum_supports(document, body, obj)
        return obj, [""]
    raise ValueError("Unsupported mirror plane kind")


def _current_axis_edge(body, group, request, tip, reference, *, shape=None):
    if tip is None or tip.Name != request["predecessor_name"]:
        raise ValueError("Pattern axis edge requires the current Body Tip")
    refs = _feature_semantic_refs(
        [reference], "edge", "pattern_axis", "axis.edge_ref", 1)
    if (refs[0]["owner"].get("object_name") != tip.Name
            or refs[0].get("fallback") is not None):
        raise ValueError("Pattern axis edge must name the current predecessor without fallback")
    shape = _predecessor_shape(body) if shape is None else shape
    _single_solid_summary(shape, "Pattern predecessor")
    evidence = _geometry_evidence(
        shape, _ir_tolerance_contract(group), include_relations=False,
        include_repetition=False)
    if refs[0]["owner"]["revision"] != evidence["stable_selection"]["model_revision"]:
        raise ValueError("Pattern axis edge revision changed; re-observe")
    report = _resolve_dressup_selection(
        evidence, {"edge_refs": refs}, _ir_tolerance_contract(group))
    if not report["all_resolved"]:
        raise SelectionResolutionError(report)
    if (report["results"][0].get("matched_by")
            != "geometry_and_topology_signatures"):
        raise ValueError("Pattern axis edge requires exact current signatures")
    name = report["matched_names"][0]
    detail = next(edge for edge in evidence["edges"] if edge["name"] == name)
    if (detail["curve_type"] != "Line"
            or len(tip.Shape.Edges) != len(evidence["edges"])):
        raise ValueError("Pattern axis requires a current straight predecessor edge")
    return tip, [name]


def _transform_axis(document, body, group, tip, request, *, shape=None):
    """Return the native axis LinkSub and its parent only when it is a CS child."""
    choice = request["axis"]
    kind = choice["kind"]
    if kind == "origin_axis":
        origin = body.Origin
        if origin is None:
            raise ValueError("Pattern Body has no native Origin")
        return (_role_object(origin, choice["role"], "App::Line", document), [""]), None
    if kind == "predecessor_edge":
        return _current_axis_edge(body, group, request, tip, choice["edge_ref"], shape=shape), None
    if kind == "datum_line":
        obj = document.getObject(choice["datum_name"])
        if (obj is None or obj.TypeId != "Part::DatumLine"
                or obj.Document is not document or obj not in body.Group):
            raise ValueError("Pattern axis requires a same-Body Datum Line")
        return (obj, [""]), None
    if kind == "coordinate_system_axis":
        cs = document.getObject(choice["coordinate_system_name"])
        if (cs is None or cs.TypeId != "Part::LocalCoordinateSystem"
                or cs.Document is not document or cs not in body.Group):
            raise ValueError("Pattern axis requires a same-Body Coordinate System")
        return (_role_object(cs, choice["role"], "App::Line", document), [""]), cs
    raise ValueError("Unsupported pattern axis kind")


def _set_transform_sources(feature, sources):
    meta = _metadata(feature, "Originals")
    if not meta["writable"] or meta["codec"] != "link_list":
        raise ValueError("Native transformed Originals are not a writable link list")
    feature.Originals = sources


def _transform_sources_readback(feature, sources, predecessor):
    actual = list(feature.Originals)
    if (len(actual) != len(sources)
            or any(got is not wanted for got, wanted in zip(actual, sources))
            or any(source not in feature.OutList for source in sources)):
        raise RuntimeError("Native transformed sources did not read back in caller order")
    if "BaseFeature" not in feature.PropertiesList or feature.BaseFeature is not predecessor:
        raise RuntimeError("Native transformed predecessor link did not read back")


def _set_transform_reference(feature, property_name, reference):
    meta = _metadata(feature, property_name)
    if not meta["writable"] or meta["codec"] != "link_sub":
        raise ValueError("Native transformation reference is not a writable LinkSub")
    setattr(feature, property_name, (reference[0], reference[1]))


def _transform_reference_readback(feature, property_name, reference):
    actual = getattr(feature, property_name)
    if (not isinstance(actual, (tuple, list)) or len(actual) != 2
            or actual[0] is not reference[0]
            or list(actual[1]) != reference[1]
            or reference[0] not in feature.OutList):
        raise RuntimeError("Native transformation reference did not read back")
