"""Client for the small JSON-lines bridge running inside FreeCAD."""

from __future__ import annotations

import json
import socket
import uuid
from typing import Any

from .contract import (
    BRIDGE_PROTOCOL_VERSION,
    PRODUCT_ID,
    ROLE_PORTS,
    RequestValidationError,
    request_side_effect,
    resolve_retry_action,
    validate_input_schema,
)
from .results import OperationResult

_BOOTSTRAP_READ_ONLY_METHODS = {"agent_ping", "agent_get_capabilities"}


class BridgeError(RuntimeError):
    """Base error raised by the FreeCAD bridge client."""

    def __init__(
        self, message: str, *, operation: OperationResult | None = None
    ) -> None:
        super().__init__(message)
        self.operation = operation


class BridgeConnectionError(BridgeError):
    """The local FreeCAD bridge could not be reached or returned invalid data."""


class BridgeResponseError(BridgeError):
    """FreeCAD rejected a valid bridge request."""

    @property
    def error_class(self) -> str | None:
        return (
            self.operation.error.error_class
            if self.operation is not None and self.operation.error is not None
            else None
        )

    @property
    def side_effect(self) -> str | None:
        return self.operation.side_effect if self.operation is not None else None

    @property
    def retry_action(self) -> str | None:
        return self.operation.retry_action if self.operation is not None else None


class BridgeClient:
    """Send one request per TCP connection to the FreeCAD add-on."""

    def __init__(
        self,
        *,
        role: str,
        timeout: float = 10.0,
        max_response_bytes: int = 1024 * 1024,
        long_operation_timeout: float = 120.0,
    ) -> None:
        if role not in ROLE_PORTS:
            raise ValueError("role must be source or rebuild")
        if long_operation_timeout <= 0:
            raise ValueError("long_operation_timeout must be positive")
        self.host = "127.0.0.1"
        self.port = ROLE_PORTS[role]
        self.role = role
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.long_operation_timeout = max(timeout, long_operation_timeout)
        self._operation_contract: dict[str, dict[str, Any]] = {}

    def install_operation_contract(self, contract: Any) -> None:
        """Cache transport policy and input schemas from the Bridge capability source."""

        if not isinstance(contract, dict) or contract.get("schema_version") != 1:
            raise ValueError("Bridge operation contract is invalid")
        operations = contract.get("operations")
        if not isinstance(operations, dict):
            raise ValueError("Bridge operation contract has no operations")
        normalized: dict[str, dict[str, Any]] = {}
        for name, spec in operations.items():
            if not isinstance(name, str) or not isinstance(spec, dict):
                raise ValueError("Bridge operation contract entry is invalid")
            if spec.get("side_effect") not in {
                "none",
                "committed",
                "transient_rolled_back",
            }:
                raise ValueError("Bridge operation side effect is invalid")
            if spec.get("timeout_class") not in {"short", "long"}:
                raise ValueError("Bridge operation timeout class is invalid")
            if name in ("agent_observe", "agent_validate"):
                schema = spec.get("input_schema")
                if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
                    raise ValueError(
                        f"Bridge input schema is missing for {name}; update the GUI add-on "
                        "and repeat the capability handshake before creating a session"
                    )
                if name == "agent_observe":
                    kinds = schema["properties"].get("kind", {}).get("enum", [])
                    queries = spec.get("query_schemas", {})
                    if not kinds or not isinstance(queries, dict) or any(
                        not isinstance(queries.get(kind), dict) for kind in kinds
                    ):
                        raise ValueError("Bridge observe query schemas are incomplete")
            normalized[name] = dict(spec)
        self._operation_contract = normalized

    def validate_request(self, method: str, *, partial: bool = False, **params: Any) -> None:
        """Reject invalid observation/validation inputs using cached schemas, with no I/O."""

        if method in _BOOTSTRAP_READ_ONLY_METHODS:
            return
        spec = self._operation_contract.get(method, {})
        schema = spec.get("input_schema")
        if not isinstance(schema, dict):
            message = f"Input contract unavailable for {method}; complete the capability handshake first"
            raise BridgeResponseError(message, operation=OperationResult.failure(
                method, error_class="precondition", code="input_contract_unavailable",
                message=message, side_effect="none",
                retry_action=resolve_retry_action("precondition", "none"),
            ))
        try:
            validate_input_schema(params, schema, path=method, partial=partial)
            if method == "agent_observe":
                kind = params["kind"]
                validate_input_schema(
                    params["query"], spec["query_schemas"][kind], path=f"agent_observe.query[{kind}]"
                )
        except RequestValidationError as exc:
            raise BridgeResponseError(str(exc), operation=OperationResult.failure(
                method, error_class="precondition", code="invalid_request",
                message=str(exc), side_effect="none",
                retry_action=resolve_retry_action("precondition", "none"),
                evidence=[{"kind": "input_validation", "stage": "bridge_validation", "issues": exc.issues,
                           "issue_count": exc.issue_count, "issues_omitted": exc.issues_omitted}],
            )) from exc

    def call(self, method: str, **params: Any) -> Any:
        if method == "agent_ping":
            return self._ensure_bridge()

        self.validate_request(method, **params)
        # Verify the user-selected GUI before sending a potentially mutating request.
        # Never retry the original request after a connection failure because
        # FreeCAD may already have executed it before the response was lost.
        self._ensure_bridge()
        return self._call_once(method, **params)

    def _ensure_bridge(self) -> Any:
        return self._ensure_bridge_result().value

    def _ensure_bridge_result(self) -> OperationResult:
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise BridgeConnectionError("REAgentFreeCAD accepts only a local GUI bridge")
        try:
            operation = self._call_result_once("agent_ping")
        except BridgeConnectionError as exc:
            display_role = "Source" if self.role == "source" else "Target"
            raise BridgeConnectionError(
                f"The {display_role} FreeCAD GUI is not connected at "
                f"{self.host}:{self.port}. Check the {display_role} instance "
                f"and its REAgent workbench {display_role} binding. "
                "This check does not establish the state of the other role.",
                operation=exc.operation,
            ) from exc
        self._verify_bridge(operation.value)
        return operation

    def _verify_bridge(self, result: Any) -> None:
        """Verify the bound GUI instance without requiring a presentation state."""
        if not isinstance(result, dict):
            raise BridgeConnectionError("FreeCAD ping did not return instance metadata")
        process_id = result.get("process_id")
        failures = []
        if result.get("instance_role") != self.role:
            failures.append("instance_role")
        port = result.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or port != self.port:
            failures.append("port")
        if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
            failures.append("process_id")
        window = result.get("window")
        if (result.get("interactive") is not True or not isinstance(window, dict)
                or window.get("gui_available") is not True):
            failures.append("gui_instance")
        if failures:
            raise BridgeConnectionError(
                "FreeCAD bridge responded with invalid binding metadata: "
                + ", ".join(failures)
                + f"; expected role={self.role}, port={self.port}; "
                + f"reported role={result.get('instance_role')!r}, port={port!r}, PID={process_id!r}. "
                + f"interactive={result.get('interactive')!r}, "
                + f"gui_available={window.get('gui_available') if isinstance(window, dict) else None!r}. "
                + "Check the selected instance binding and GUI availability."
            )

    def _call_once(self, method: str, **params: Any) -> Any:
        return self._call_result_once(method, **params).value

    def call_result(self, method: str, **params: Any) -> OperationResult:
        """Return structured operation metadata without changing ``call``."""

        if method == "agent_ping":
            return self._ensure_bridge_result()
        self.validate_request(method, **params)
        self._ensure_bridge()
        return self._call_result_once(method, **params)

    def _call_result_once(
        self, method: str, **params: Any
    ) -> OperationResult:
        request_id = uuid.uuid4().hex
        self.validate_request(method, **params)
        read_only = request_side_effect(method, params) == "none"

        def protocol_error(message):
            return self._protocol_error(method, request_id, message, read_only=read_only)

        request = {
            "id": request_id,
            "method": method,
            "params": params,
        }
        payload = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")

        transmission_started = False
        request_timeout = self._request_timeout(method)
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=request_timeout
            ) as connection:
                connection.settimeout(request_timeout)
                transmission_started = True
                connection.sendall(payload)
                response_bytes = self._read_line(connection)
        except (OSError, TimeoutError, BridgeConnectionError) as exc:
            operation = self._transport_failure(
                method,
                exc,
                transmission_started=transmission_started,
                request_id=request_id,
                operation_id=params.get("operation_id"),
                read_only=read_only,
            )
            if operation.side_effect == "unknown":
                raise BridgeConnectionError(
                    "Target write %s (%s) was sent but its response is unavailable; "
                    "the outcome is unknown (response_unknown). Inspect agent_get_state "
                    "before another write. Do not retry or restart Source. (%s)"
                    % (method, params.get("operation_id") or request_id, exc),
                    operation=operation,
                ) from exc
            raise BridgeConnectionError(
                f"Cannot complete the FreeCAD bridge request at "
                f"{self.host}:{self.port}. Is FreeCAD running with the "
                f"REAgent add-on enabled? ({exc})",
                operation=operation,
            ) from exc

        try:
            response = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            operation = OperationResult.failure(
                method,
                error_class="protocol",
                code="invalid_json",
                message="FreeCAD returned invalid JSON",
                side_effect=("none" if read_only else "unknown"),
                retry_action=resolve_retry_action(
                    "protocol", "none" if read_only else "unknown",
                    transmission_started=True,
                ),
                evidence=[{"kind": "request_id", "id": request_id}],
            )
            raise BridgeConnectionError(
                "FreeCAD returned invalid JSON", operation=operation
            ) from exc

        if not isinstance(response, dict):
            raise protocol_error("FreeCAD returned a non-object response")
        if response.get("id") != request_id:
            raise protocol_error("FreeCAD returned a mismatched request id")
        if response.get("ok") is not True:
            if response.get("ok") is not False:
                raise protocol_error("FreeCAD returned an invalid ok flag")
            error = response.get("error")
            if (
                not isinstance(error, dict)
                or not isinstance(error.get("code"), str)
                or not isinstance(error.get("message"), str)
            ):
                raise protocol_error("FreeCAD returned an invalid error object")
            message = error["message"]
            try:
                operation = self._failure_result(
                    method, response, error, message
                )
            except (TypeError, ValueError) as exc:
                raise protocol_error("FreeCAD returned invalid operation metadata: %s" % exc) from exc
            raise BridgeResponseError(message, operation=operation)
        operation = response.get("operation")
        try:
            operation = self._operation_metadata(method, operation)
            return OperationResult.success(
                method, response.get("result"), operation
            )
        except (TypeError, ValueError) as exc:
            raise protocol_error("FreeCAD returned invalid operation metadata: %s" % exc) from exc

    def _request_timeout(self, method: str) -> float:
        """Return the transport timeout appropriate for one bridge method.

        The short timeout remains the default for ordinary MCP calls.  CAD
        writes and explicitly requested diagnostics get a longer window so a normal
        FreeCAD recompute is not reported as an unknown side effect merely
        because the bridge response arrived after the health-check timeout.
        """

        spec = self._operation_contract.get(method)
        if spec is not None:
            return (
                self.long_operation_timeout
                if spec.get("timeout_class") == "long"
                else self.timeout
            )
        return (
            self.timeout
            if method in _BOOTSTRAP_READ_ONLY_METHODS
            else self.long_operation_timeout
        )

    def _failure_result(
        self,
        method: str,
        response: dict[str, Any],
        error: dict[str, Any],
        message: str,
    ) -> OperationResult:
        operation = self._operation_metadata(
            method, response.get("operation"), failure=True
        )
        code = error["code"]
        return OperationResult.failure(
            method,
            error_class=str(operation["error_class"]),
            code=code,
            message=message,
            side_effect=str(operation["side_effect"]),
            retry_action=str(operation["retry_action"]),
            evidence=operation["evidence"],
            protocol_version=int(operation["schema_version"]),
        )

    @staticmethod
    def _operation_metadata(
        method: str, value: Any, *, failure: bool = False
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("operation must be an object")
        required = {
            "schema_version",
            "bridge_protocol_version",
            "product_id",
            "method",
            "side_effect",
            "retry_action",
            "evidence",
        }
        if failure:
            required.add("error_class")
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(
                "operation is missing fields: " + ", ".join(missing)
            )
        if value["schema_version"] != 1:
            raise ValueError("operation.schema_version must be 1")
        if value["bridge_protocol_version"] != BRIDGE_PROTOCOL_VERSION:
            raise ValueError("operation.bridge_protocol_version is unsupported")
        if value["product_id"] != PRODUCT_ID:
            raise ValueError("operation.product_id is invalid")
        if value["method"] != method:
            raise ValueError("operation.method does not match the request")
        if not isinstance(value["evidence"], list):
            raise ValueError("operation.evidence must be a list")
        return value

    def _transport_failure(
        self,
        method: str,
        exc: BaseException,
        *,
        transmission_started: bool,
        request_id: str,
        operation_id: Any = None,
        read_only: bool,
    ) -> OperationResult:
        side_effect = (
            "none" if not transmission_started or read_only else "unknown"
        )
        transport_evidence = {
            "kind": "transport",
            "host": self.host,
            "port": self.port,
            "request_id": request_id,
            "transmission_started": transmission_started,
        }
        if isinstance(operation_id, str) and operation_id:
            transport_evidence["operation_id"] = operation_id
        return OperationResult.failure(
            method,
            error_class="connection",
            code=("connection_failed" if not transmission_started else "response_unknown"),
            message=str(exc),
            side_effect=side_effect,
            retry_action=resolve_retry_action(
                "connection",
                side_effect,
                transmission_started=transmission_started,
            ),
            evidence=[transport_evidence],
        )

    def _protocol_error(
        self, method: str, request_id: str, message: str, *, read_only: bool
    ) -> BridgeConnectionError:
        side_effect = "none" if read_only else "unknown"
        operation = OperationResult.failure(
            method,
            error_class="protocol",
            code="invalid_response",
            message=message,
            side_effect=side_effect,
            retry_action=resolve_retry_action(
                "protocol",
                side_effect,
                transmission_started=True,
            ),
            evidence=[{"kind": "request_id", "id": request_id}],
        )
        return BridgeConnectionError(message, operation=operation)

    def _read_line(self, connection: socket.socket) -> bytes:
        data = bytearray()
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                raise BridgeConnectionError(
                    "FreeCAD closed the connection before completing a response"
                )
            data.extend(chunk)
            if len(data) > self.max_response_bytes:
                raise BridgeConnectionError("FreeCAD response exceeded the size limit")
            newline = data.find(b"\n")
            if newline >= 0:
                return bytes(data[:newline])
