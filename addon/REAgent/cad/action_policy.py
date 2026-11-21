"""Project the task and action's available CAD tools and guard their execution."""

from REAgent.protocol.observations import IMAGE_OBSERVATION_KINDS, OBSERVATION_ROLES
from REAgent.protocol.operations import OPERATION_SPECS, request_side_effect
from REAgent.cad.tools import MODEL_COMMANDS, MODEL_TOOLS, finish_tool, model_tool, observation_query
from REAgent.cad.knowledge import knowledge_assets


_DRAWING_VIEW_FIELDS = frozenset(("width", "height", "output_long_edge_px", "sheet_region_mm"))


def allowed_tools(workflow, scope=None):
    names = set(MODEL_TOOLS)
    if not workflow.compares_stp:
        names.discard("agent_validate")
    if not workflow.requires_images:
        names.difference_update(("agent_read_image", "agent_set_evidence_retention"))
    if scope == "target_edit":
        names.difference_update(("agent_validate", "agent_finish_reconstruction"))
    elif scope is not None:
        names.discard("agent_resume_reconstruction")
    return names


def _observation_allowed(workflow, scope, role, kind, query):
    if OBSERVATION_ROLES.get(kind, role) != role or (role == "source" and scope == "target_edit"):
        return False
    source_kind = workflow.native_source if role == "source" else None
    if source_kind == "drawing":
        return kind == "view" and query.keys() <= _DRAWING_VIEW_FIELDS
    if kind == "global" and "mesh_section" in query:
        return source_kind == "mesh"
    if kind == "view":
        if "sheet_region_mm" in query or (source_kind == "mesh" and query.get("focus_box_mm")):
            return False
        if query.get("paired") or query.get("include_section"):
            if role != "target" or not workflow.paired_views:
                return False
            if not workflow.compares_stp and (query.get("include_section") or query.get("focus_box_mm") is not None):
                return False
    if source_kind == "mesh":
        return kind in ("global", "view", "initial_views")
    if role == "source":
        return workflow.compares_stp and kind not in ("selection_resolution", "residual")
    if kind == "support_extension":
        return False
    if kind == "residual":
        return workflow.compares_stp and scope != "target_edit"
    return kind not in IMAGE_OBSERVATION_KINDS or (workflow.requires_images and scope != "target_edit")


def model_spec(workflow, scope, name, *, type_id=None):
    """Apply the same task scope to the published schema and exact discovery."""
    available = allowed_tools(workflow, scope)
    if name not in available:
        raise ValueError("Tool is unavailable for the current CAD action")
    if name == "agent_finish_reconstruction":
        return finish_tool(workflow, model=True)
    spec = model_tool(name, type_id=type_id)
    schema = spec["input_schema"]
    if name == "agent_observe":
        if not workflow.compares_stp or scope == "target_edit":
            spec.pop("view_evidence_contract")
            if not workflow.requires_images or scope == "target_edit":
                spec.pop("image_content_limits")
            spec["description"] = ("Get revision-bound evidence for the geometry, references and measurements "
                                   "needed by the next decision or delivery.")
            if workflow.requires_images and scope != "target_edit":
                spec["description"] += " Image captures must be the sole call."
                if workflow.native_source == "mesh":
                    spec["description"] += (" Source initial_views returns both boards with all 14 directions at one scale. "
                                            "Source global can include a mesh_section, with actual mesh contour bounds. "
                                            "Target paired views frame both complete models. Omit focus_box_mm for a pair. "
                                            "Capture view_frames give scale in the final image.")
                if workflow.native_source == "drawing":
                    spec["description"] += (" Source view shows the whole DXF sheet from above by default. To enlarge a detail, use sheet_region_mm with world X/Y bounds. "
                                            "Use the frame's focus_mm as its center, image_rect_px as its pixel rectangle, and final_scale to locate the bounds. X increases rightward and Y upward.")
        spec["description"] += " Cite the returned observation_id when using this observation as evidence."
        forms = []
        for role in ("source", "target"):
            for form in schema["oneOf"]:
                kind = form["properties"]["kind"]["enum"][0]
                if not _observation_allowed(workflow, scope, role, kind, {}):
                    continue
                properties = {**form["properties"], "role": {**form["properties"]["role"], "enum": [role]}}
                if kind == "global" and (role != "source" or workflow.native_source != "mesh"):
                    properties.pop("mesh_section", None)
                if kind == "view":
                    if role == "source" and workflow.native_source == "drawing":
                        properties = {key: value for key, value in properties.items()
                                      if key in _DRAWING_VIEW_FIELDS or key in ("role", "kind", "purpose")}
                    else:
                        excluded = {"sheet_region_mm"}
                        if role != "target" or not workflow.paired_views:
                            excluded.add("paired")
                        if role != "target" or not workflow.compares_stp:
                            excluded.update(("include_section", "section_axis"))
                        if role == "source" and workflow.native_source == "mesh":
                            excluded.update(("focus_box_mm", "display_mode"))
                        properties = {key: value for key, value in properties.items() if key not in excluded}
                forms.append({**form, "properties": properties})
        spec["input_schema"] = {**schema, "oneOf": forms}
    if name == "agent_execute_command":
        schema["properties"]["command"]["enum"] = [command for command in MODEL_COMMANDS if command in available]
    if name == "agent_get_capabilities":
        for form in schema["oneOf"]:
            props = form["properties"]["request"]["properties"]
            if "name" in props:
                props["name"]["enum"] = [item for item in props["name"]["enum"] if item in available]
            if "role" in props and not workflow.native_source:
                props["role"]["enum"] = ["target"]
    if name == "agent_read_image" and (not workflow.image_inputs or scope == "target_edit"):
        spec["input_schema"] = next(
            branch for branch in schema["oneOf"] if "evidence_id" in branch["properties"]
        )
    if name == "agent_get_knowledge":
        schema["properties"]["knowledge_id"]["enum"] = [asset.knowledge_id for asset in knowledge_assets(workflow)]
    if name == "agent_read_freecad_log" and not workflow.native_source:
        schema["properties"]["role"]["enum"] = ["target"]
    return spec


def guard(record, name, args, context):
    workflow = record["workflow"]
    model = context is not None and context["actor"] == "model"
    scope = context["scope"] if model else None
    if context and context["binding"] != record["session_id"]:
        raise ValueError("Execution context belongs to a different task")
    if name == "agent_read_image":
        if "index" in args:
            if not workflow.image_inputs:
                raise ValueError("The current task has no reference image files")
            if model and scope == "target_edit":
                raise ValueError("Reference image viewing is unavailable during target_edit")
        elif not workflow.requires_images:
            raise ValueError("The current task has no image observation evidence")
    if name == "agent_observe" and not _observation_allowed(
            workflow, scope, args["role"], args["kind"], observation_query(args)):
        raise ValueError("Observation is unavailable for the current role and CAD action")
    if name == "agent_validate" and not workflow.compares_stp:
        raise ValueError("Geometric comparison requires an STP source")
    if name == "agent_read_freecad_log" and args["role"] == "source" and not workflow.native_source:
        raise ValueError("The current task has no native Source binding")
    if not model:
        return
    if context["purpose"] == "report":
        raise ValueError("This delivered run can only publish its final report")
    if "delivery-" + context["run_id"] in record["delivery_receipts"] and name != "agent_finish_reconstruction":
        raise ValueError("This reconstruction run already delivered; a new user action is required")
    if name not in allowed_tools(workflow, scope):
        raise ValueError("Tool is outside the current CAD action")
    if ((scope == "reconstruct" or not workflow.compares_stp) and workflow.require_reference is not None
            and name != "agent_observe" and name in OPERATION_SPECS
            and request_side_effect(name, args) != "none"):
        workflow.require_reference(record, context["observations"])
