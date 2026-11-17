"""Workspace view captures and paired half-cutaways in the owned observation scope."""

import FreeCAD as App
import FreeCADGui as Gui

from REAgent.host.process_state import operation_stage, PROCESS
from REAgent.host.analysis.measurements import geometry_bound_box
from REAgent.protocol.definitions import NATIVE_SOURCE_EXTENSIONS
from REAgent.protocol.observations import INITIAL_SOURCE_DISPLAY_MODES, VIEW_DIRECTIONS, initial_view_layout, paired_view_layout, single_view_layout
from REAgent.host.workspace.model import native_source_path, read_mesh, read_stp_reference, result_display_plan, resolve_result_object, result_shape, source_objects, target_state
from REAgent.host.workspace.scope import cleanup_observation_previews
from REAgent.host.workspace.state import source_view_revision, target_view_revision
from REAgent.host.view.camera import normalize_projection
from REAgent.host.view.rendering import _CaptureOutput, normalize_display_mode, existing_directory, focus_box, render_canonical_views, render_drawing_view, section_preview_shapes, view_directions, view_precondition


def _view_artifacts(params, document):
    binding = PROCESS.SESSION_BINDINGS.get(params["session_id"])
    if binding is None or binding["document"] is not document or binding["role"] != PROCESS.INSTANCE_ROLE:
        raise ValueError("View output requires the exact bound session Document")
    artifacts = dict(binding["artifacts"])
    existing_directory(artifacts["output_dir"])
    return artifacts


def _hide_all(document):
    for obj in document.Objects:
        if hasattr(obj, "ViewObject"):
            obj.ViewObject.Visibility = False


def _view_result(document, scope, params, camera, output):
    return {"document": document.Name, "capture_id": scope.capture_id, "capture_number": scope.capture_number,
            "width": params.get("width", 640), "height": params.get("height", 480),
            "output_long_edge_px": params.get("output_long_edge_px", 1536),
            "evidence_status": "generated", **output.finish(),
            **{key: camera[key] for key in ("requested_display_mode", "normalized_display_mode", "object_display_mode",
                                           "projection", "camera", "scale", "requested_transparency", "transparency")}}


class ViewObservationOperations:
    def get_reconstruction_views(self, params, scope):
        if not getattr(App, "GuiUp", False):
            raise ValueError("Paired views require FreeCAD GUI mode")
        document, target_body = target_state(params.get("document"))
        artifacts = _view_artifacts(params, document)
        width = params.get("width", 640)
        height = params.get("height", 480)
        projection = normalize_projection(params.get("projection"))
        directions = view_directions(params.get("views"))
        section_requested = params.get("include_section", False)
        axis = params.get("section_axis", "y")
        rows = (*directions, *(("section",) if section_requested else ()))
        result_object = resolve_result_object(target_body)
        if result_object is None or result_object.TypeId == "PartDesign::Body" and result_object.Tip is None:
            raise view_precondition("Target has no Result/Tip to render", "view_result_unavailable")
        display_plan = result_display_plan(target_body)
        if not display_plan["render"] or not display_plan["style"]:
            raise view_precondition("Target Result has no GUI style objects", "view_style_unavailable")
        source_path = getattr(target_body, "SourceFile", "")
        mesh_source = source_path.lower().endswith(NATIVE_SOURCE_EXTENSIONS["mesh"])
        display_mode = params.get("display_mode")
        if mesh_source:
            if section_requested or params.get("focus_box_mm") is not None:
                raise ValueError("STL paired views frame the whole mesh and Target")
            source_revision = PROCESS.SESSION_BINDINGS[params["session_id"]]["source_revision"]
            source_mesh = read_mesh(native_source_path(source_path, NATIVE_SOURCE_EXTENSIONS["mesh"]), source_revision)
            display_mode = display_mode or INITIAL_SOURCE_DISPLAY_MODES["mesh"]
        else:
            source = read_stp_reference(document, target_body, params["session_id"])
            source_shape = source.Shape
            source_revision = source.SourceSHA256
        revisions = {"source": source_revision, "target": target_view_revision(document, target_body)}
        normalize_display_mode(display_mode)
        focus, focus_shape = focus_box(params.get("focus_box_mm"))
        previews = []
        phase = "setup"
        section = None
        try:
            App.setActiveDocument(document.Name)
            gui_document = Gui.activeDocument()
            if gui_document is None:
                raise RuntimeError("Target GUI document is unavailable")
            view = gui_document.activeView()
            source_preview = scope.create_preview("REAgentSourcePreview",
                "Mesh::Feature" if mesh_source else "Part::Feature")
            previews.append(source_preview)
            source_preview.Label = "Temporary Isolated Source Preview"
            if mesh_source:
                source_preview.Mesh = source_mesh
            else:
                source_preview.Shape = source_shape
                source_preview.ViewObject.ShapeColor = (0.75, 0.80, 0.90)
                source_preview.ViewObject.LineColor = (0.20, 0.25, 0.35)
            main_items = {"source": source_preview.ViewObject, "target": display_plan}
            if focus_shape is not None:
                source_focus = source_shape.common(focus_shape)
                target_focus = result_shape(target_body).common(focus_shape)
                if source_focus.isNull() or target_focus.isNull():
                    raise ValueError("focus_box_mm does not intersect both shapes")
                source_preview.Shape = source_focus
                target_preview = scope.create_preview("REAgentTargetFocusPreview")
                previews.append(target_preview)
                target_preview.Shape = target_focus
                main_items["target"] = target_preview.ViewObject
            section_items = {}
            if section_requested:
                phase = "section_geometry"
                sections, section = section_preview_shapes(source_shape, result_shape(target_body), axis)
                section["preview_names"] = {}
                for role, shape in sections.items():
                    preview = scope.create_preview("REAgent" + role.title() + "SectionPreview")
                    previews.append(preview)
                    preview.Shape = shape
                    preview.Label = "Temporary " + role.title() + " Section Preview"
                    preview.ViewObject.ShapeColor = (0.75, 0.80, 0.90) if role == "source" else (0.85, 0.80, 0.55)
                    preview.ViewObject.LineColor = (0.20, 0.25, 0.35)
                    section["preview_names"][role] = preview.Name
                    section_items[role] = preview.ViewObject
            layouts = {"paired": paired_view_layout(width, height, rows, params.get("output_long_edge_px", 1536))}
            output = _CaptureOutput(scope, artifacts, layouts, revisions, section=section)
            _hide_all(document)
            phase = "render"
            camera = render_canonical_views(view, main_items, output.capture, directions, width, height,
                scope=scope, transparency=params.get("transparency", 0),
                display_mode=display_mode, projection=projection)
            if section_requested:
                phase = "section_render"
                _hide_all(document)
                section["capturing"] = True
                render_canonical_views(view, section_items, output.capture, ("iso_xm_ym_zp",), width, height,
                    scope=scope, transparency=params.get("transparency", 0),
                    display_mode=display_mode, projection=projection, camera_metadata=camera)
                section.pop("capturing")
                section.pop("preview_names")
                section["display"] = [{key: row[key] for key in ("role", "object_display_mode", "visible_object_count", "transparency")}
                                      for row in output.frames if row["direction"] == "section"]
            phase = "output"
            value = _view_result(document, scope, params, camera, output)
            value.update(revision=revisions["target"], view_directions=list(rows), section=section,
                focus_box_mm=focus, paired_revisions={"source_revision": revisions["source"],
                    "target_revision": revisions["target"]},
                target_rendered_from={"name": result_object.Name, "type_id": result_object.TypeId,
                                       "body_display_mode": display_plan["body_display_mode"]})
            return value
        except Exception as exc:
            exc.view_phase = phase
            exc.failure_detail = {**dict(getattr(exc, "failure_detail", {}) or {}), "view_phase": phase}
            if section is not None:
                detail = dict(getattr(exc, "failure_detail", {}) or {})
                detail["section"] = {key: value for key, value in section.items() if key not in ("capturing", "preview_names")}
                exc.failure_detail = detail
            raise
        finally:
            cleanup_observation_previews(document, scope, previews)

    def get_initial_views(self, params, scope):
        return self.get_observation_views({**params, "_initial_views": True,
            "projection": "orthographic", "transparency": 0}, scope)

    def get_drawing_view(self, params, scope):
        document, source = source_objects(params["document"])
        width, height = params.get("width", 640), params.get("height", 480)
        limit = params.get("output_long_edge_px", 1536)
        region = params.get("sheet_region_mm")
        revision = source_view_revision(source)
        layouts = {"source_top": single_view_layout(width, height, "source", "top", limit)}
        output = _CaptureOutput(scope, _view_artifacts(params, document), layouts, {"source": revision})
        camera = render_drawing_view(Gui.activeDocument().activeView(), output.capture, width, height,
                                     scope=scope, region=region)
        return {**_view_result(document, scope, params, camera, output), "role": "source",
                "revision": revision, "sheet_region_mm": region, "view_directions": ["top"]}

    def get_observation_views(self, params, scope):
        if not getattr(App, "GuiUp", False):
            raise ValueError("Views require FreeCAD GUI mode")
        initial = params.get("_initial_views", False)
        role = PROCESS.INSTANCE_ROLE
        preview = None
        mesh = None
        if role == "source":
            document, source = source_objects(params.get("document"))
            mesh = source.Mesh if source.TypeId == "Mesh::Feature" else None
            shape = source.Shape if mesh is None else None
            revision = source_view_revision(source)
            display_item = source.ViewObject
        elif role == "target":
            document, target_body = target_state(params.get("document"))
            result_object = resolve_result_object(target_body)
            if result_object is None or result_object.TypeId == "PartDesign::Body" and result_object.Tip is None:
                raise view_precondition("Target has no Result/Tip to render", "view_result_unavailable")
            shape, revision = result_shape(target_body), target_view_revision(document, target_body)
            display_item = result_display_plan(target_body)
        else:
            raise ValueError("Views require a Source or Target role")
        width = params.get("width", 640)
        height = params.get("height", 480)
        limit = params.get("output_long_edge_px", 1536)
        directions = VIEW_DIRECTIONS if initial else view_directions(params.get("views"))
        display_mode = params.get("display_mode")
        if initial or mesh is not None:
            display_mode = INITIAL_SOURCE_DISPLAY_MODES["mesh" if mesh is not None else "stp"]
        focus, focus_shape = focus_box(params.get("focus_box_mm"))
        if mesh is not None and focus_shape is not None:
            raise ValueError("Mesh views use the whole reference")
        if initial:
            layouts = {"initial_" + key: layout for key, layout in initial_view_layout(width, height, limit).items()}
        else:
            layouts = {role + "_" + d: single_view_layout(width, height, role, d, limit) for d in directions}
        artifacts = _view_artifacts(params, document)
        output = _CaptureOutput(scope, artifacts, layouts, {role: revision})
        phase = "setup"
        operation_stage("observation", mutating=True)
        try:
            App.setActiveDocument(document.Name)
            gui_document = Gui.activeDocument()
            if gui_document is None:
                raise RuntimeError("GUI document is unavailable for observation")
            view = gui_document.activeView()
            scope.assert_render_safe()
            _hide_all(document)
            if focus_shape is not None:
                shape = shape.common(focus_shape)
                if shape.isNull():
                    raise ValueError("focus_box_mm does not intersect the role shape")
                preview = scope.create_preview("REAgentObservationPreview")
                preview.Shape = shape
                display_item = preview.ViewObject
            phase = "render"
            camera = render_canonical_views(view, {role: display_item}, output.capture, directions, width, height,
                scope=scope, transparency=params.get("transparency", 0),
                display_mode=display_mode,
                projection=normalize_projection(params.get("projection")),
                framing_box=(mesh.BoundBox if mesh is not None else geometry_bound_box(shape)) if initial else None)
            phase = "output"
            value = _view_result(document, scope, params, camera, output)
            value.update(role=role, revision=revision, focus_box_mm=focus, view_directions=list(directions))
            if initial:
                value["initial_views"] = {"directions": list(directions), "direction_count": len(directions),
                    "complete": True, "capture_id": scope.capture_id}
            return value
        except Exception as exc:
            exc.view_phase = phase
            exc.failure_detail = {**dict(getattr(exc, "failure_detail", {}) or {}), "view_phase": phase}
            raise
        finally:
            cleanup_observation_previews(document, scope, (preview,) if preview is not None else ())
