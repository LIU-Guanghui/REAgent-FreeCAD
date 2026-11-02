"""Core JSON Lines server and request dispatch on the FreeCAD GUI thread."""

from .operation_table import assemble_operations

import json
import socket

import FreeCAD as App
from PySide import QtCore

from REAgent.host.process_state import HOST, MAX_REQUEST_BYTES, POLL_INTERVAL_MS, _stamp_failure, PROCESS
from REAgent.host.view.presentation import _main_window_state
from REAgent.host.workspace.sessions import _ended_receipt
from REAgent.protocol.operations import OPERATION_SPECS, request_side_effect
from REAgent.protocol.definitions import ROLE_PORTS
from REAgent.protocol.observations import validate_observation_role
from REAgent.schema import validate_input_schema
from REAgent.host.responses import _operation_failure, _operation_success

class BridgeServer:
    """Non-blocking local server polled by Qt on FreeCAD's main thread."""

    def __init__(self, role, *, assert_release):
        if role not in ROLE_PORTS:
            raise ValueError("Bridge role must be source or rebuild")
        self.assert_release = assert_release
        self.role = role
        self.host = HOST
        self.port = ROLE_PORTS[role]
        self._listener = None
        self._timer = None
        self._clients = {}
        self.operations = assemble_operations(role, self.port)

    @property
    def running(self):
        return self._listener is not None

    def start(self):
        if self.running:
            return "Bridge is already running at %s:%d." % (self.host, self.port)

        if PROCESS.INSTANCE_ROLE not in (None, self.role):
            raise RuntimeError("This FreeCAD window is already bound to another role")
        previous_role = PROCESS.INSTANCE_ROLE
        PROCESS.INSTANCE_ROLE = self.role
        window = _main_window_state()
        if not window["gui_available"] or not window["main_window_available"]:
            PROCESS.INSTANCE_ROLE = previous_role
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
            PROCESS.INSTANCE_ROLE = previous_role
            raise

        self._listener = listener
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_INTERVAL_MS)
        message = "Bridge listening at %s:%d." % (self.host, self.port)
        App.Console.PrintMessage("[REAgent] %s\n" % message)
        return message

    def stop(self):
        self.assert_release()
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        for client in list(self._clients):
            self._close_client(client)
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if PROCESS.INSTANCE_ROLE == self.role:
            PROCESS.INSTANCE_ROLE = None
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
            if method != "agent_ping" and request.get("instance_id") != PROCESS.INSTANCE_ID:
                failure = ValueError("The request belongs to another GUI startup instance")
                failure.error_code = "instance_changed"
                failure.error_class = "precondition"
                raise failure
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


    def _dispatch(self, method, params):
        application = QtCore.QCoreApplication.instance()
        if application is None or QtCore.QThread.currentThread() != application.thread():
            raise RuntimeError("Bridge operations must run on the GUI main thread")
        handlers = self.operations.handlers
        handler = handlers.get(method)
        if handler is None:
            raise ValueError("Unknown method: %s" % method)
        specification = OPERATION_SPECS.get(method)
        if specification is not None:
            if PROCESS.INSTANCE_ROLE not in specification["roles"]:
                raise ValueError("Operation is not available for this instance role")
            validate_input_schema(params, specification["input_schema"], path=method)
            allowed = set(specification["input_schema"]["properties"])
            unknown = sorted(set(params) - allowed)
            if unknown:
                raise ValueError(
                    "Unknown request parameters for %s: %s"
                    % (method, ", ".join(unknown))
                )
        if PROCESS.ACTIVE_OPERATION is not None:
            raise ValueError("An operation is still executing; retry after its response")
        task = PROCESS.ACTIVE_TASK
        if (method != "agent_start_session" and "session_id" in params
                and not (method == "agent_close_session" and _ended_receipt(params) is not None)):
            if (task is None or task["session_id"] != params["session_id"]
                    or task["role"] != PROCESS.INSTANCE_ROLE or task["instance_id"] != PROCESS.INSTANCE_ID):
                raise ValueError("Native request does not match this GUI's retained task")
            if task["status"] != "active" and method not in ("agent_close_session", "agent_get_state"):
                raise ValueError("The native task is unresolved; no further write is allowed")
            if (method != "agent_close_session" and task["document"] is not None
                    and App.listDocuments().get(task["document_name"]) is not task["document"]):
                raise ValueError("The original task Document is no longer present; do not bind by name")
        if method == "agent_observe":
            validate_input_schema(params["query"], specification["query_schemas"][params["kind"]], path="agent_observe.query")
            validate_observation_role(params["kind"], params["query"], PROCESS.INSTANCE_ROLE)
        effect = request_side_effect(method, params)
        block = PROCESS.AUDIT_BLOCKS.get(params.get("document"))
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
        PROCESS.ACTIVE_OPERATION = {
            "operation": method,
            "document": params.get("document"),
            "stage": "bridge_execution", "write_started": False,
            "side_effect": effect,
        }
        try:
            value = handler(params)
            if self.role == "rebuild" and effect == "none" and method == "agent_observe":
                value["document_state"] = self.operations.diagnostic_state(params["document"])
            return value
        except Exception as exc:
            _stamp_failure(exc)
            raise
        finally:
            PROCESS.ACTIVE_OPERATION = None


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
