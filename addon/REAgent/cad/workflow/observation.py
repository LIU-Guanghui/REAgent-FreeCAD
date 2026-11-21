"""Session evidence adaptation for explicit Source/Target observations."""

import uuid

from REAgent.cad.log_summary import observation_summary
from REAgent.cad.images import prepare_view_content, read_registered_images
from REAgent.cad.tools import observation_query
from REAgent.protocol.observations import FEATURE_OBSERVATION_BUDGET_BYTES, IMAGE_CONTENT_LIMITS, IMAGE_OBSERVATION_KINDS, validate_observation_role
from REAgent.cad.results import MODEL_RESULT_SOFT_BUDGET_BYTES, PreparedImage
from REAgent.json_values import encode_json
from REAgent.cad.errors import PublicError


_DRAWING_READBACK_FIELDS = {
    "global": ("shape",),
    "entity": ("faces", "edges", "wires", "relations", "repetition", "semantic_regions"),
    "feature": ("properties", "sketch"),
}
_GEOMETRY_DIMENSION_UNITS = {
    "length_mm": "mm", "radius_mm": "mm", "spacing_mm": "mm", "boundary_distance_mm": "mm",
    "angle_deg": "deg", "normal_angle_deg": "deg", "interior_dihedral_deg": "deg",
    "area_mm2": "mm^2", "volume_mm3": "mm^3",
}


def _dimension_readbacks(value, path=(), section=None):
    """Yield the observed dimensional scalars at their original paths."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _dimension_readbacks(item, (*path, index), section)
        return
    if not isinstance(value, dict):
        return
    number, unit = value.get("value"), value.get("unit")
    if type(number) in (int, float) and isinstance(unit, str) and unit:
        yield {"readback_path": list(path), "value": number, "unit": unit}
        return
    units = dict(_GEOMETRY_DIMENSION_UNITS)
    if value.get("active") is True:
        units["Value"] = value.get("value_unit")
    if section == "bounding_box_mm":
        units.update(x_length="mm", y_length="mm", z_length="mm")
    elif section in ("curve", "surface"):
        units.update(radius="mm", major_radius="mm", minor_radius="mm")
    for key, item in value.items():
        if type(item) in (int, float):
            unit = units.get(key)
            if isinstance(unit, str) and unit:
                yield {"readback_path": [*path, key], "value": item, "unit": unit}
        elif key not in ("semantic_ref", "stable_selector"):
            yield from _dimension_readbacks(item, (*path, key), key)


def _drawing_readbacks(value, fields):
    return list(_dimension_readbacks({key: value[key] for key in fields if key in value}))


def _page_model_entities(value, readback_fields):
    """Keep whole entity rows in the model's observation budget."""
    def fits():
        readbacks = _drawing_readbacks(value, readback_fields)
        projected = {**value, "dimension_readbacks": readbacks} if readbacks else value
        return len(encode_json(projected).encode("utf-8")) <= MODEL_RESULT_SOFT_BUDGET_BYTES

    if fits():
        return
    records = {name: value[name] for name in ("faces", "edges") if value[name]}
    native_pages = {name: value["pagination"][name] for name in records}
    omitted = {}
    value["model_projection"] = {"omitted": omitted,
        "next_query": "Use the returned offsets or narrow fields or selectors to read the omitted geometry."}

    def update_page(name):
        native_page = native_pages[name]
        returned = len(value[name])
        remaining = len(records[name]) - returned
        if remaining:
            omitted[name] = remaining
            value["pagination"][name] = {**native_page, "returned": returned,
                "next_offset": native_page["offset"] + returned, "model_projection_omitted": remaining}
        else:
            omitted.pop(name, None)
            value["pagination"][name] = native_page

    for name in records:
        value[name] = []
        update_page(name)
    if not fits() and "relations" in value:
        omitted["relations"] = len(value.pop("relations"))
    for name, rows in records.items():
        kept = value[name]
        for row in rows:
            kept.append(row)
            update_page(name)
            if not fits():
                kept.pop()
                update_page(name)
                break
    if not omitted:
        value.pop("model_projection")


def _read_input_image(args, record, started, ctx):
    index, region = args["index"], args.get("region")
    if index > len(record["inputs"]):
        raise ValueError("Choose a reference image index shown in the current task inputs")
    item = record["inputs"][index - 1]
    if region and (region[0] >= region[2] or region[1] >= region[3]):
        raise ValueError("The selected region must have positive width and height")
    ctx.guard_journal(record)
    try:
        data, width, height = ctx.view_input(item, region=region, long_edge=args.get("long_edge_px", 2048))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Cannot render " + item["filename"] + ". " + str(exc)) from exc
    if (len(data) > IMAGE_CONTENT_LIMITS["max_image_bytes"]
            or width * height > IMAGE_CONTENT_LIMITS["max_image_pixels"]):
        raise ValueError("Choose a smaller long_edge_px or a smaller region to fit the image budget")
    context = ctx.call_context.get()
    image_id = "input-" + context["operation_id"]
    view = {"index": index, "filename": item["filename"], "region": region,
            "revision": item["revision"], "image_id": image_id, "size_bytes": len(data),
            "width_px": width, "height_px": height}
    ctx.emit(record, "input.view_prepared", "agent_read_image",
             {key: value for key, value in view.items() if key not in ("revision", "image_id")},
             started, role="agent")
    ctx.guard_journal(record)
    context["image_content"] = (PreparedImage(data, None, image_id, width, height),)
    return {"input_view": view, "actor": (context.get("execution") or {}).get("actor", "external")}


def _read_evidence_image(args, record, started, ctx):
    registration = record["evidence_facts"].get(args["evidence_id"], {}).get("image_registration")
    if registration is None:
        raise ValueError("Image evidence is not registered to the current task")
    delivery, content, previews = read_registered_images(
        registration, args["artifact_key"])
    context = ctx.call_context.get()
    context["image_content"] = content
    value = {"evidence_id": args["evidence_id"], "role": registration["role"],
             "actor": registration["actor"], "reader_actor": (context.get("execution") or {}).get("actor", "external"),
             "document": registration["document"],
             "revision": registration["revision"], "paired_revisions": registration.get("paired_revisions"),
             "initial_views": registration.get("initial_views"), "image_delivery": delivery,
             "images": registration["frames"],
             "image_previews": previews, "capture_id": registration["capture_id"],
             "capture_number": registration["capture_number"], "historical": True,
             "source_basis": "registered_capture_revision", "cad_access": False}
    ctx.emit(record, "observation.read_back", "agent_read_image", {"evidence_id": args["evidence_id"],
          "artifact_key": args["artifact_key"], "image_count": len(content), "cad_access": False},
          started, role=registration["role"])
    return value


def run(name, args, record, started, ctx):
    _call = ctx.call
    _emit = ctx.emit
    if name == "agent_read_image":
        if "index" in args:
            return _read_input_image(args, record, started, ctx)
        return _read_evidence_image(args, record, started, ctx)
    if name == "agent_observe":
        context = ctx.call_context.get()
        if context is None:
            raise RuntimeError("Observation evidence requires an Controller response context")
        role, kind = args["role"], args["kind"]
        query = observation_query(args)
        trusted = context.get("execution") or {}
        actor = trusted.get("actor", "external")
        validate_observation_role(kind, query, role)
        value = _call(record, "agent_observe", role, kind=kind, query=query)
        revision = value.get("revision")
        if not isinstance(revision, str) or not revision:
            raise RuntimeError("Bound observation did not return a model revision")
        if role == "source" and revision != record["source_revision"]:
            raise PublicError("source_revision_mismatch", "public_response", "Source observation differs from the bound reference revision",
                              side_effect=context["side_effect"])
        if "paired_revisions" in value and value["paired_revisions"]["source_revision"] != record["source_revision"]:
            raise PublicError("source_revision_mismatch", "public_response", "Paired Source snapshot differs from the bound reference revision; start a new task",
                              side_effect=context["side_effect"])
        evidence_id = "evidence-" + uuid.uuid4().hex[:20]
        fact = {"role": role, "revision": revision, "kind": kind,
                "actor": actor,
                 "document": record[role + "_document"]}
        if kind == "feature":
            fact.update(object=value["object"], type_id=value["type_id"], body=value["body"],
                        predecessor=value["predecessor"])
            page = value.get("sketch", {}).get("page")
            if page is not None:
                fact["sketch_indices"] = {page["section"]: [item["index"] for item in page["items"]]}
            if "vertex_page" in value:
                fact["vertex_indices"] = [item["index"] for item in value["vertex_page"]["items"]]
        scope = value.get("target_geometry_scope") or {}
        if scope.get("kind") == "edit_predecessor" and value.get("writable_references_available"):
            fact.update(edit_object=scope["edit_object"], predecessor=scope["shape_object"])
        if kind in IMAGE_OBSERVATION_KINDS:
            value = {**value, "document": fact["document"]}
            delivery, content, registration, previews = prepare_view_content(
                value, record["artifacts"], evidence_id, role, actor=actor)
            context["image_content"] = content
            value = {**value, "image_delivery": delivery, "image_previews": previews}
            fact["image_delivery"] = delivery
            if registration is not None:
                fact["image_registration"] = registration
            if kind == "initial_views":
                fact["initial_views"] = value["initial_views"]
        # Captured facts stay local until the Controller success response is constructed.
        value = {**value, "evidence_id": evidence_id, "purpose": args.get("purpose"),
                 "actor": actor, "document": fact["document"], "operation_id": context["operation_id"]}
        if actor == "model":
            drawing_target = record["workflow"].require_dimension_readback and role == "target"
            fields = _DRAWING_READBACK_FIELDS.get(kind, ()) if drawing_target else ()
            if kind == "entity":
                _page_model_entities(value, fields)
            if drawing_target:
                readbacks = _drawing_readbacks(value, fields)
                if readbacks:
                    value["dimension_readbacks"] = fact["dimension_readbacks"] = readbacks
                if value.get("owning_feature"):
                    fact["object"] = value["owning_feature"]["name"]
        if kind == "feature":
            size = len(encode_json(value).encode("utf-8"))
            if size > FEATURE_OBSERVATION_BUDGET_BYTES:
                raise ValueError("Feature observation exceeds the response budget. Request a smaller page or shorter purpose.")
        context["pending_evidence"] = (record, evidence_id, fact)
        _emit(record, "observation.completed", name, observation_summary(args, value), started, role=role)
        return value
    raise ValueError("Unknown tool in workflow group")


def contribute():
    return {name: run for name in ('agent_observe', 'agent_read_image')}
