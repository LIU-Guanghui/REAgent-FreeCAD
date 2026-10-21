"""Core JSON Lines server and request dispatch on the FreeCAD GUI thread."""

import json
import os
import socket
from functools import partial

import FreeCAD as App
from PySide import QtCore

from ..features.body import BodyOperations
from ..features.boolean import BooleanOperations
from ..features.chamfer import ChamferOperations
from ..features.datum import DatumOperations
from ..features.draft import DraftOperations
from ..features.extrude import ExtrudeOperations
from ..features.fillet import FilletOperations
from ..features.hole import HoleOperations
from ..features.loft import LoftOperations
from ..features.mirror import MirrorOperations
from ..features.pattern import PatternOperations
from ..features.pipe import PipeOperations
from ..features.primitives import PrimitiveOperations
from ..features.revolve import RevolveOperations
from ..features.sketch import SketchOperations
from ..features.thickness import ThicknessOperations
from ..runtime import ADDON_PATH, HOST, MAX_REQUEST_BYTES, POLL_INTERVAL_MS, _document, _stamp_failure, _state
from ..validation.handlers import ValidationOperations
from ..view.presentation import _main_window_state
from ..view.queries import ViewObservationOperations
from ..view.rendering import _validate_view_query
from ..workspace.queries import ObservationOperations
from ..workspace.scope import ObservationScopeOperations
from ..workspace.persistence import PersistenceOperations
from ..workspace.sessions import SessionOperations
from .capabilities import _addon_version, capability_response
from .contract import (
    OPERATION_SPECS,
    PRIMITIVE_COMMANDS,
    PRODUCT_ID,
    ROLE_PORTS,
    initial_view_layout,
    _check_request_fields,
    request_side_effect,
    validate_input_schema,
    validate_observation_role,
)
from .logs import DiagnosticOperations
from .responses import _operation_failure, _operation_success

_FLOW_HANDLERS = {
    "agent_ping": "_ping",
    "agent_get_capabilities": "_get_capabilities",
    "agent_start_session": "_start_session",
    "agent_close_session": "_close_session",
    "agent_get_state": "_get_state",
    "agent_observe": "_observe",
    "agent_validate": "_validate",
    "agent_undo": "_undo",
    "agent_redo": "_redo",
    "agent_save": "_save",
    "agent_read_freecad_log": "_read_freecad_log",
    "PartDesign_Body": "_create_body",
    "PartDesign_NewSketch": "_new_sketch",
    "Sketcher_MapSketch": "_map_sketch",
    "Sketcher_EditSketch": "_edit_sketch",
    "Sketcher_ValidateSketch": "_validate_sketch",
    "Part_DatumPlane": "_datum_plane",
    "Part_DatumLine": "_datum_line",
    "Part_DatumPoint": "_datum_point",
    "Part_CoordinateSystem": "_coordinate_system",
    **dict.fromkeys(PRIMITIVE_COMMANDS, "_create_primitive"),
    **dict.fromkeys(("PartDesign_Pad", "PartDesign_Pocket"), "_create_extrude"),
    **dict.fromkeys(("PartDesign_Revolution", "PartDesign_Groove"), "_create_revolve"),
    "PartDesign_Hole": "_create_hole",
    "PartDesign_Boolean": "_create_boolean",
    **dict.fromkeys(("PartDesign_AdditiveLoft", "PartDesign_SubtractiveLoft"), "_create_loft"),
    **dict.fromkeys(("PartDesign_AdditivePipe", "PartDesign_SubtractivePipe"), "_create_pipe"),
    "PartDesign_Fillet": "_create_fillet",
    "PartDesign_Chamfer": "_create_chamfer",
    "PartDesign_Draft": "_create_draft",
    "PartDesign_Thickness": "_create_thickness",
    "PartDesign_Mirrored": "_create_mirror",
    **dict.fromkeys(("PartDesign_LinearPattern", "PartDesign_PolarPattern"), "_create_pattern"),
}


class BridgeServer(
    DiagnosticOperations,
    SessionOperations,
    ObservationOperations,
    ViewObservationOperations,
    ObservationScopeOperations,
    BodyOperations,
    SketchOperations,
    DatumOperations,
    PrimitiveOperations,
    ExtrudeOperations,
    RevolveOperations,
    HoleOperations,
    BooleanOperations,
    LoftOperations,
    PipeOperations,
    FilletOperations,
    ChamferOperations,
    DraftOperations,
    ThicknessOperations,
    MirrorOperations,
    PatternOperations,
    ValidationOperations,
    PersistenceOperations,
):
    """Non-blocking local server polled by Qt on FreeCAD's main thread."""

    def __init__(self, role):
        if role not in ROLE_PORTS:
            raise ValueError("Bridge role must be source or rebuild")
        self.role = role
        self.host = HOST
        self.port = ROLE_PORTS[role]
        self._listener = None
        self._timer = None
        self._clients = {}

    @property
    def running(self):
        return self._listener is not None

    def start(self):
        if self.running:
            return "Bridge is already running at %s:%d." % (self.host, self.port)

        if _state.INSTANCE_ROLE not in (None, self.role):
            raise RuntimeError("This FreeCAD window is already bound to another role")
        previous_role = _state.INSTANCE_ROLE
        _state.INSTANCE_ROLE = self.role
        window = _main_window_state()
        if not window["gui_available"] or not window["main_window_available"]:
            _state.INSTANCE_ROLE = previous_role
            raise RuntimeError(
                "REAgent Bridge requires an existing FreeCAD GUI main window"
            )

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self.host, self.port))
            listener.listen(8)
            listener.setblocking(False)
        except Exception:
            listener.close()
            _state.INSTANCE_ROLE = previous_role
            raise

        self._listener = listener
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_INTERVAL_MS)
        message = "Bridge listening at %s:%d." % (self.host, self.port)
        App.Console.PrintMessage("[REAgent] %s\n" % message)
        return message

    def stop(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        for client in list(self._clients):
            self._close_client(client)
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if _state.INSTANCE_ROLE == self.role:
            _state.INSTANCE_ROLE = None
        message = "Bridge stopped."
        App.Console.PrintMessage("[REAgent] %s\n" % message)
        return message

    def _poll(self):
        if not self.running:
            return
        self._accept_clients()
        for client in list(self._clients):
            self._read_client(client)

    def _accept_clients(self):
        while True:
            try:
                client, _address = self._listener.accept()
            except BlockingIOError:
                return
            except OSError as exc:
                App.Console.PrintError(
                    "[REAgent] accept failed: %s\n" % exc
                )
                return
            client.setblocking(False)
            self._clients[client] = bytearray()

    def _read_client(self, client):
        try:
            chunk = client.recv(4096)
        except BlockingIOError:
            return
        except OSError:
            self._close_client(client)
            return

        if not chunk:
            self._close_client(client)
            return

        buffer = self._clients[client]
        buffer.extend(chunk)
        if len(buffer) > MAX_REQUEST_BYTES:
            self._send_error(
                client,
                None,
                "unknown",
                "request_too_large",
                "Request is too large",
            )
            return

        newline = buffer.find(b"\n")
        if newline < 0:
            return
        request_bytes = bytes(buffer[:newline])
        self._handle_request(client, request_bytes)

    def _handle_request(self, client, request_bytes):
        request_id = None
        method = "unknown"
        try:
            request = json.loads(request_bytes.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("Request must be a JSON object")
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(method, str) or not method:
                raise ValueError("method must be a non-empty string")
            if not isinstance(params, dict):
                raise ValueError("params must be a JSON object")
            result = self._dispatch(method, params)
            self._send(
                client,
                {
                    "id": request_id,
                    "ok": True,
                    "result": result,
                    "operation": _operation_success(method, result, params),
                },
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_error(
                client, request_id, method, "invalid_json", str(exc), exc
            )
        except ValueError as exc:
            self._send_error(
                client, request_id, method, "invalid_request", str(exc), exc
            )
        except KeyError as exc:
            self._send_error(
                client, request_id, method, "not_found", str(exc), exc
            )
        except Exception as exc:
            App.Console.PrintError("[REAgent] request failed: %s\n" % exc)
            self._send_error(
                client, request_id, method, "freecad_error", str(exc), exc
            )

    def _handlers(self):
        return {
            name: (partial(self._create_primitive, command=name)
                   if name in PRIMITIVE_COMMANDS
                   else partial(self._create_extrude, command=name)
                   if name in ("PartDesign_Pad", "PartDesign_Pocket")
                   else partial(self._create_revolve, command=name)
                   if name in ("PartDesign_Revolution", "PartDesign_Groove")
                   else partial(self._create_loft, command=name)
                   if name in ("PartDesign_AdditiveLoft", "PartDesign_SubtractiveLoft")
                   else partial(self._create_pipe, command=name)
                   if name in ("PartDesign_AdditivePipe", "PartDesign_SubtractivePipe")
                   else partial(self._create_pattern, command=name)
                   if name in ("PartDesign_LinearPattern", "PartDesign_PolarPattern")
                   else getattr(self, _FLOW_HANDLERS[name]))
            for name in OPERATION_SPECS
        }

    def _dispatch(self, method, params):
        handlers = self._handlers()
        handler = handlers.get(method)
        if handler is None:
            raise ValueError("Unknown method: %s" % method)
        specification = OPERATION_SPECS.get(method)
        if specification is not None:
            if _state.INSTANCE_ROLE not in specification["roles"]:
                raise ValueError("Operation is not available for this instance role")
            validate_input_schema(params, specification["input_schema"], path=method)
            allowed = set(specification["input_schema"]["properties"])
            unknown = sorted(set(params) - allowed)
            if unknown:
                raise ValueError(
                    "Unknown request parameters for %s: %s"
                    % (method, ", ".join(unknown))
                )
        if _state.ACTIVE_OPERATION is not None:
            raise ValueError("An operation is still executing; retry after its response")
        if method == "agent_observe":
            validate_input_schema(params["query"], specification["query_schemas"][params["kind"]], path="agent_observe.query")
            validate_observation_role(params["kind"], params["query"], _state.INSTANCE_ROLE)
        effect = request_side_effect(method, params)
        block = _state.AUDIT_BLOCKS.get(params.get("document"))
        if (block is not None
                and App.getDocument(params.get("document")) is block[0]
                and effect != "none"
                and method != "agent_close_session"):
            failure = RuntimeError(
                "Target write state remains unverified; only reads or bound session close are allowed"
            )
            failure.target_state_unknown = True
            failure.diagnostics = {"kind": "audit_restoration", **block[1]}
            raise failure
        _state.ACTIVE_OPERATION = {
            "operation_id": params.get("operation_id"),
            "operation": method,
            "document": params.get("document"),
            "stage": "bridge_execution", "write_started": False,
            "side_effect": effect,
        }
        try:
            value = handler(params)
            if self.role == "rebuild" and effect == "none" and method in ("agent_observe", "agent_validate"):
                value["document_state"] = self._diagnostic_document_state(params["document"])
            return value
        except Exception as exc:
            _stamp_failure(exc)
            raise
        finally:
            _state.ACTIVE_OPERATION = None

    def _ping(self, params):
        window = _main_window_state()
        active = App.activeDocument()
        return {
            "bridge": "REAgent",
            "product_id": PRODUCT_ID,
            "product_version": _addon_version(),
            "active_document": active.Name if active is not None else None,
            "addon_path": ADDON_PATH,
            "instance_role": _state.INSTANCE_ROLE,
            "port": self.port,
            "process_id": os.getpid(),
            "interactive": window["gui_available"] and window["main_window_available"],
            "window": window,
        }

    def _get_capabilities(self, params):
        return capability_response(
            view=params.get("view", "status"),
            operation=params.get("operation"),
            role=_state.INSTANCE_ROLE,
            port=self.port,
            registered_handlers=self._handlers(),
        )

    def _get_state(self, params):
        return self._get_target_state(params)

    def _observe(self, params):
        kind = params.get("kind")
        query = params.get("query")
        specification = OPERATION_SPECS["agent_observe"]
        _check_request_fields(params, specification["input_schema"], "agent_observe")
        validate_input_schema(query, specification["query_schemas"][kind], path="agent_observe.query[%s]" % kind)
        forwarded = dict(query)
        forwarded["document"] = params.get("document")
        if kind == "global":
            if _state.INSTANCE_ROLE == "source":
                result = self._analyze_source(forwarded)
            else:
                result = self._observe_target_global(forwarded)
        elif kind == "entity":
            result = self._inspect_geometry(forwarded)
        elif kind == "selection_resolution":
            if _state.INSTANCE_ROLE != "rebuild":
                raise ValueError(
                    "Selection resolution requires the Target instance"
                )
            result = self._resolve_target_selection(forwarded)
        elif kind == "regions":
            result = self._get_semantic_regions(forwarded)
        elif kind == "points":
            result = self._classify_points(forwarded)
        elif kind == "residual":
            result = self._get_reconstruction_residuals(forwarded)
        elif kind == "support_extension":
            result = self._infer_virtual_predecessor_bounds(forwarded)
        elif kind == "initial_views":
            initial_view_layout(query.get("width", 640), query.get("height", 480))
            forwarded["session_id"] = params["session_id"]
            doc = _document(params.get("document"))
            result = self._isolated_observation(doc, self._get_initial_views, forwarded, temporary=False)
        elif kind == "view":
            doc = _document(params.get("document"))
            if doc.RedoNames and (query.get("paired") or query.get("focus_box_mm")):
                raise ValueError("Paired/focus rendering is unavailable while a native redo branch exists")
            forwarded = _validate_view_query(query, _state.INSTANCE_ROLE)
            forwarded["document"] = params.get("document")
            forwarded["session_id"] = params["session_id"]
            paired = forwarded.pop("paired")
            if _state.INSTANCE_ROLE == "rebuild" and paired:
                result = self._isolated_observation(doc, self._get_reconstruction_views, forwarded)
            else:
                forwarded.pop("include_section", None)
                forwarded.pop("section_axis", None)
                if forwarded.get("focus_box_mm"):
                    result = self._isolated_observation(doc, self._get_observation_views, forwarded)
                else:
                    result = self._isolated_observation(doc, self._get_observation_views, forwarded, temporary=False)
        else:
            raise ValueError("unknown observation kind")
        return self._with_observation_revision(params["document"], result)

    def _validate(self, params):
        options = params.get("options")
        schema = OPERATION_SPECS["agent_validate"]["input_schema"]["properties"]["options"]
        _check_request_fields(options, schema, "agent_validate.options")
        validate_input_schema(options, schema, path="agent_validate.options")
        return self._validate_target({"document": params.get("document"), "options": options})

    def _send_error(
        self, client, request_id, method, code, message, exception=None
    ):
        if exception is None:
            exception = RuntimeError(message)
        operation = _operation_failure(method, exception, code)
        self._send(
            client,
            {
                "id": request_id,
                "ok": False,
                "error": {"code": operation["code"], "stage": operation["stage"],
                          "message": str(exception)[:500]},
                "operation": operation,
            },
        )

    def _send(self, client, response):
        payload = (
            json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            client.setblocking(True)
            client.settimeout(0.5)
            client.sendall(payload)
        except OSError as exc:
            App.Console.PrintWarning(
                "[REAgent] response send failed: %s\n" % exc
            )
        finally:
            self._close_client(client)

    def _close_client(self, client):
        self._clients.pop(client, None)
        try:
            client.close()
        except OSError:
            pass
