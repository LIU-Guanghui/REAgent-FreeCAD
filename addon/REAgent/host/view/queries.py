"""Role-local captures and paired half-cutaways in the owned observation scope."""

import FreeCAD as App
import FreeCADGui as Gui

from REAgent.host.process_state import _operation_stage, PROCESS
from REAgent.host.analysis.measurements import _geometry_bound_box
from REAgent.protocol.observations import VIEW_DIRECTIONS, initial_view_layout, paired_view_layout, single_view_layout
from REAgent.host.workspace.model import _owned_objects, _result_display_plan, _result_object, _result_shape, _source_objects, _target_state
from REAgent.host.workspace.scope import _cleanup_observation_previews
from REAgent.host.workspace.state import _source_view_revision, _target_view_revision
from REAgent.host.view.camera import _projection
from REAgent.host.view.rendering import _CaptureOutput, _display_mode, _existing_directory, _focus_box, _pixel_dimension, _render_canonical_views, _section_preview_shapes, _view_directions, _view_precondition, _view_transparency


def _view_artifacts(params, document):
    binding = PROCESS.SESSION_BINDINGS.get(params["session_id"])
    if binding is None or binding["document"] is not document or binding["role"] != PROCESS.INSTANCE_ROLE:
        raise ValueError("View output requires the exact bound session Document")
    artifacts = dict(binding["artifacts"])
    _existing_directory(artifacts["output_dir"])
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
    def _get_reconstruction_views(self, params, scope):
        if not getattr(App, "GuiUp", False):
            raise ValueError("Paired views require FreeCAD GUI mode")
        document, source, rebuild, reconstruction_ir = _target_state(params.get("document"))
        artifacts = _view_artifacts(params, document)
        width = _pixel_dimension(params.get("width", 640), "width")
        height = _pixel_dimension(params.get("height", 480), "height")
        projection = _projection(params.get("projection"))
        _display_mode(params.get("display_mode"))
        directions = _view_directions(params.get("views"))
        section_requested = params.get("include_section", False)
        axis = params.get("section_axis", "y")
        if type(section_requested) is not bool or axis not in ("x", "y", "z"):
            raise ValueError("Invalid section request")
        rows = (*directions, *(("section",) if section_requested else ()))
        revisions = {"source": _source_view_revision(source),
                     "target": _target_view_revision(document, rebuild, reconstruction_ir)}
        result_object = _result_object(rebuild)
        if result_object is None or result_object.TypeId == "PartDesign::Body" and result_object.Tip is None:
            raise _view_precondition("Target has no Result/Tip to render", "view_result_unavailable")
        display_plan = _result_display_plan(rebuild)
        if not display_plan["render"] or not display_plan["style"]:
            raise _view_precondition("Target Result has no GUI style objects", "view_style_unavailable")
        source_shape, target_shape = source.Shape.copy(), _result_shape(rebuild)
        focus, focus_shape = _focus_box(params.get("focus_box_mm"))
        previews = []
        phase = "setup"
        section = None
        try:
            App.setActiveDocument(document.Name)
            gui_document = Gui.activeDocument()
            if gui_document is None:
                raise RuntimeError("Target GUI document is unavailable")
            view = gui_document.activeView()
            source_preview = scope.create_preview("REAgentSourcePreview")
            previews.append(source_preview)
            source_preview.Label = "Temporary Isolated Source Preview"
            source_preview.Shape = source_shape
            source_preview.ViewObject.ShapeColor = (0.75, 0.80, 0.90)
            source_preview.ViewObject.LineColor = (0.20, 0.25, 0.35)
            main_items = {"source": source_preview.ViewObject, "target": display_plan}
            if focus_shape is not None:
                source_focus, target_focus = source_shape.common(focus_shape), target_shape.common(focus_shape)
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
                sections, section = _section_preview_shapes(source_shape, target_shape, axis)
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
            camera = _render_canonical_views(view, main_items, output.capture, directions, width, height,
                scope=scope, transparency=_view_transparency(params.get("transparency", 0)),
                display_mode=params.get("display_mode"), projection=projection)
            if section_requested:
                phase = "section_render"
                _hide_all(document)
                section["capturing"] = True
                _render_canonical_views(view, section_items, output.capture, ("iso_xm_ym_zp",), width, height,
                    scope=scope, transparency=_view_transparency(params.get("transparency", 0)),
                    display_mode=params.get("display_mode"), projection=projection, camera_metadata=camera)
                section.pop("capturing")
                section.pop("preview_names")
                section["display"] = [{key: row[key] for key in ("role", "object_display_mode", "visible_object_count", "transparency")}
                                      for row in output.frames if row["direction"] == "section"]
            phase = "output"
            value = _view_result(document, scope, params, camera, output)
            value.update(revision=revisions["target"], view_directions=list(rows), section=section,
                focus_box_mm=focus, paired_revisions={"source_revision": revisions["source"],
                    "source_revision_basis": "step_sha256", "source_sha256": source.SourceSHA256,
                    "target_revision": revisions["target"], "rebuild_object_count": len(_owned_objects(rebuild))},
                rebuild_rendered_from={"name": result_object.Name, "type_id": result_object.TypeId,
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
            _cleanup_observation_previews(document, scope, previews)

    def _get_initial_views(self, params, scope):
        return self._get_observation_views({**params, "_initial_views": True,
            "projection": "orthographic", "display_mode": "flat_lines", "transparency": 0}, scope)

    def _get_observation_views(self, params, scope):
        if not getattr(App, "GuiUp", False):
            raise ValueError("Views require FreeCAD GUI mode")
        initial = params.get("_initial_views", False)
        native_role = PROCESS.INSTANCE_ROLE
        preview = None
        if native_role == "source":
            document, source = _source_objects(params.get("document"))
            shape, revision, role = source.Shape, _source_view_revision(source), "source"
            display_item = source.ViewObject
        elif native_role == "rebuild":
            document, _source, rebuild, reconstruction_ir = _target_state(params.get("document"))
            result_object = _result_object(rebuild)
            if result_object is None or result_object.TypeId == "PartDesign::Body" and result_object.Tip is None:
                raise _view_precondition("Target has no Result/Tip to render", "view_result_unavailable")
            shape, revision, role = _result_shape(rebuild), _target_view_revision(document, rebuild, reconstruction_ir), "target"
            display_item = _result_display_plan(rebuild)
        else:
            raise ValueError("Views require a Source or Target role")
        width = _pixel_dimension(params.get("width", 640), "width")
        height = _pixel_dimension(params.get("height", 480), "height")
        limit = params.get("output_long_edge_px", 1536)
        directions = VIEW_DIRECTIONS if initial else _view_directions(params.get("views"))
        focus, focus_shape = _focus_box(params.get("focus_box_mm"))
        if initial:
            layouts = {"initial_" + key: layout for key, layout in initial_view_layout(width, height, limit).items()}
        else:
            prefix = "source" if role == "source" else "rebuild"
            layouts = {prefix + "_" + d: single_view_layout(width, height, role, d, limit) for d in directions}
        artifacts = _view_artifacts(params, document)
        output = _CaptureOutput(scope, artifacts, layouts, {role: revision})
        phase = "setup"
        _operation_stage("observation", mutating=True)
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
            camera = _render_canonical_views(view, {role: display_item}, output.capture, directions, width, height,
                scope=scope, transparency=_view_transparency(params.get("transparency", 0)),
                display_mode=params.get("display_mode"), projection=_projection(params.get("projection")),
                framing_box=_geometry_bound_box(shape) if initial else None)
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
            _cleanup_observation_previews(document, scope, (preview,) if preview is not None else ())
