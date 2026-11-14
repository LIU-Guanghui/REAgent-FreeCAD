"""Route BridgeServer-validated requests through explicit native service dependencies."""

import os
import FreeCAD as App
from REAgent.resources import ADDON_PATH
from REAgent.host.process_state import active_task_state, require_document, PROCESS
from REAgent.host.view.presentation import main_window_state
from REAgent.host.view.rendering import normalize_view_query
from REAgent.host.capabilities import addon_metadata, capability_response
from REAgent.protocol.definitions import PRODUCT_ID
from REAgent.protocol.observations import initial_view_layout

class AdapterOperations:
    def __init__(self, role, port, *, queries, views, scope, validation, handlers):
        self.role, self.port = role, port
        self.queries, self.views, self.scope, self.validation = queries, views, scope, validation
        self.handlers = handlers

    def contribute(self):
        return {"agent_ping": self._ping, "agent_get_capabilities": self._get_capabilities,
                "agent_get_state": self._get_state, "agent_observe": self._observe, "agent_validate": self._validate}

    def _ping(self, params):
        window = main_window_state()
        active = App.activeDocument()
        return {
            "bridge": "REAgent",
            "product_id": PRODUCT_ID,
            **addon_metadata(),
            "active_document": active.Name if active is not None else None,
            "addon_path": ADDON_PATH,
            "instance_role": PROCESS.INSTANCE_ROLE,
            "port": self.port,
            "process_id": os.getpid(),
            "instance_id": PROCESS.INSTANCE_ID,
            "active_task": active_task_state(),
            "interactive": window["gui_available"] and window["main_window_available"],
            "window": window,
        }

    def _get_capabilities(self, params):
        return capability_response(
            view=params.get("view", "status"),
            operation=params.get("operation"),
            role=PROCESS.INSTANCE_ROLE,
            port=self.port,
            registered_handlers=self.handlers(),
        )

    def _get_state(self, params):
        return self.queries.get_target_state(params)

    def _observe(self, params):
        kind, query = params["kind"], params["query"]
        forwarded = dict(query)
        forwarded["document"] = params.get("document")
        if kind == "global":
            if PROCESS.INSTANCE_ROLE == "source":
                result = self.queries.analyze_source(forwarded)
            else:
                result = self.queries.observe_target_global(forwarded)
        elif kind == "entity":
            result = self.queries.inspect_geometry(forwarded)
        elif kind == "feature":
            if PROCESS.INSTANCE_ROLE != "target":
                raise ValueError("Feature observation requires the Target instance")
            result = self.queries.observe_feature(forwarded)
        elif kind == "selection_resolution":
            if PROCESS.INSTANCE_ROLE != "target":
                raise ValueError(
                    "Selection resolution requires the Target instance"
                )
            result = self.queries.resolve_target_selection(forwarded)
        elif kind == "regions":
            result = self.queries.get_semantic_regions(forwarded)
        elif kind == "points":
            result = self.queries.classify_points(forwarded)
        elif kind == "residual":
            result = self.queries.get_reconstruction_residuals(forwarded)
        elif kind == "support_extension":
            result = self.queries.infer_virtual_predecessor_bounds(forwarded)
        elif kind == "initial_views":
            initial_view_layout(query.get("width", 640), query.get("height", 480), query.get("output_long_edge_px", 1536))
            forwarded["session_id"] = params["session_id"]
            doc = require_document(params.get("document"))
            result = self.scope.isolated_observation(doc, self.views.get_initial_views, forwarded, temporary=False)
        elif kind == "view":
            doc = require_document(params.get("document"))
            if doc.RedoNames and (query.get("paired") or query.get("focus_box_mm")):
                raise ValueError("Paired/focus rendering is unavailable while a native redo branch exists")
            forwarded = normalize_view_query(query)
            forwarded["document"] = params.get("document")
            forwarded["session_id"] = params["session_id"]
            paired = forwarded.pop("paired")
            if PROCESS.INSTANCE_ROLE == "target" and paired:
                result = self.scope.isolated_observation(doc, self.views.get_reconstruction_views, forwarded)
            else:
                forwarded.pop("include_section", None)
                forwarded.pop("section_axis", None)
                if forwarded.get("focus_box_mm"):
                    result = self.scope.isolated_observation(doc, self.views.get_observation_views, forwarded)
                else:
                    result = self.scope.isolated_observation(doc, self.views.get_observation_views, forwarded, temporary=False)
        else:
            raise ValueError("unknown observation kind")
        return self.queries.with_observation_revision(params["document"], result)

    def _validate(self, params):
        return self.validation.validate_target(params)
