"""Pure-Python Bridge client for remote GUI control, instance verification, waiting, and action dispatch."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from pathlib import Path
from typing import Any

from REAgent.application.control import (
    REQUIRED_CLI_CONTROLS,
    envelope,
    resolve_command,
)
from REAgent.json_values import to_json_value
from REAgent.protocol.definitions import ROLE_PORTS
from REAgent.resources import ADDON_PATH

BRIDGE_RESPONSE_LIMIT_BYTES = 8 * 1024 * 1024


class CliBridgeError(RuntimeError):
    """Structured error raised when communicating with or rejected by the GUI Bridge."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        acceptance_unconfirmed: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.acceptance_unconfirmed = acceptance_unconfirmed
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": str(self),
        }
        if self.acceptance_unconfirmed:
            payload["acceptance_unconfirmed"] = True
        if self.details:
            payload["details"] = self.details
        return payload


def error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, CliBridgeError):
        return exc.to_dict()
    return {
        "code": "cli_error",
        "message": str(exc) or type(exc).__name__,
    }


def same_addon_path(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _raw_bridge_call(
    role: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    instance_id: str | None = None,
    timeout: float = 10.0,
    read_only: bool = False,
) -> Any:
    if role not in ROLE_PORTS:
        raise ValueError(f"Role must be 'source' or 'target', got {role!r}")
    port = ROLE_PORTS[role]
    request_id = uuid.uuid4().hex
    request: dict[str, Any] = {
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    if method != "agent_ping":
        if not isinstance(instance_id, str) or not instance_id:
            raise CliBridgeError("instance_binding_unavailable", f"Missing instance_id for {role}.{method}")
        request["instance_id"] = instance_id
    payload = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")

    unconfirmed_eligible = (not read_only) and method != "agent_ping"
    transmission_started = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as conn:
            conn.settimeout(timeout)
            transmission_started = True
            conn.sendall(payload)
            buf = bytearray()
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    raise CliBridgeError(
                        "acceptance_unconfirmed" if unconfirmed_eligible else "connection_closed",
                        f"Bridge {role} closed connection before replying to {method}",
                        acceptance_unconfirmed=unconfirmed_eligible,
                    )
                buf.extend(chunk)
                if len(buf) > BRIDGE_RESPONSE_LIMIT_BYTES:
                    raise CliBridgeError(
                        "invalid_response",
                        f"Bridge {role} response exceeded size limit",
                        acceptance_unconfirmed=unconfirmed_eligible,
                    )
                newline = buf.find(b"\n")
                if newline >= 0:
                    line = bytes(buf[:newline])
                    break
    except CliBridgeError:
        raise
    except (OSError, TimeoutError) as exc:
        if transmission_started and unconfirmed_eligible:
            raise CliBridgeError(
                "acceptance_unconfirmed",
                f"Disconnected or timed out after sending {method} to {role}: {exc}",
                acceptance_unconfirmed=True,
            ) from exc
        raise CliBridgeError(
            "connection_failed" if not transmission_started else "wait_disconnected",
            f"Cannot reach REAgent {role} bridge on 127.0.0.1:{port}: {exc}",
        ) from exc

    try:
        response = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliBridgeError(
            "invalid_json",
            f"Bridge {role} returned invalid JSON for {method}: {exc}",
            acceptance_unconfirmed=(transmission_started and unconfirmed_eligible),
        ) from exc

    if not isinstance(response, dict) or response.get("id") != request_id:
        raise CliBridgeError(
            "invalid_response",
            f"Bridge {role} returned a mismatched or non-object response",
            acceptance_unconfirmed=(transmission_started and unconfirmed_eligible),
        )
    if response.get("ok") is not True:
        err = response.get("error") or {}
        raise CliBridgeError(
            str(err.get("code") or "bridge_error"),
            str(err.get("message") or f"{role}.{method} failed"),
            details=response,
        )
    return response.get("result")


def is_port_open(role: str, *, timeout: float = 0.25) -> bool:
    port = ROLE_PORTS[role]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_instance(
    role: str,
    *,
    timeout: float = 5.0,
    expected_instance: dict[str, Any] | None = None,
    instance_id: str | None = None,
) -> dict[str, Any]:
    """Ping a running GUI Bridge instance, verifying role, window visibility, addon_path and identity."""
    ping = _raw_bridge_call(role, "agent_ping", {}, timeout=timeout, read_only=True)
    if not isinstance(ping, dict):
        raise CliBridgeError("invalid_ping", f"{role} agent_ping did not return an object")
    if ping.get("instance_role") != role or ping.get("port") != ROLE_PORTS[role]:
        raise CliBridgeError(
            "role_mismatch",
            f"{role} bridge reported incompatible role/port: {ping.get('instance_role')}:{ping.get('port')}",
        )
    window = ping.get("window") or {}
    if (
        ping.get("interactive") is not True
        or window.get("gui_available") is not True
        or window.get("visible") is not True
    ):
        raise CliBridgeError("gui_unavailable", f"{role} FreeCAD GUI window is not interactive and visible")
    if expected_instance is not None:
        if (
            ping.get("process_id") != expected_instance.get("process_id")
            or ping.get("instance_id") != expected_instance.get("instance_id")
        ):
            raise CliBridgeError(
                "instance_changed",
                f"Running {role} instance differs from expected (got pid={ping.get('process_id')}, expected pid={expected_instance.get('process_id')})",
            )
    if instance_id is not None and ping.get("instance_id") != instance_id:
        raise CliBridgeError(
            "instance_changed",
            f"Running {role} instance_id {ping.get('instance_id')!r} differs from expected {instance_id!r}",
        )
    return ping


def verify_addon_identity(ping: dict[str, Any], role: str) -> None:
    """Reject stale, mismatched-path or non-CLI Addon installations upfront."""
    remote_path = ping.get("addon_path")
    if not same_addon_path(remote_path, ADDON_PATH):
        raise CliBridgeError(
            "addon_path_mismatch",
            f"{role.title()} FreeCAD Addon path {remote_path!r} differs from CLI Addon path {ADDON_PATH!r}. "
            "Sync or install the matching Phase 46 Addon under FreeCAD\\Mod\\REAgent before running CLI.",
        )
    controls = set(ping.get("cli_controls") or ())
    missing = sorted(REQUIRED_CLI_CONTROLS[role] - controls)
    if missing:
        raise CliBridgeError(
            "cli_controls_missing",
            f"{role.title()} FreeCAD Addon is missing CLI controls {missing}. Reload or install the Phase 46 Addon.",
        )


def resolve_role_instance(
    role: str,
    *,
    expected_instance: dict[str, Any] | None = None,
    instance_id: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    ping = probe_instance(
        role,
        timeout=timeout,
        expected_instance=expected_instance,
        instance_id=instance_id,
    )
    verify_addon_identity(ping, role)
    return ping


def send_control(
    role: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    expected_instance: dict[str, Any] | None = None,
    instance_id: str | None = None,
    timeout: float = 15.0,
    read_only: bool = False,
) -> Any:
    ping = resolve_role_instance(
        role,
        expected_instance=expected_instance,
        instance_id=instance_id,
        timeout=min(timeout, 5.0),
    )
    return _raw_bridge_call(
        role,
        method,
        params,
        instance_id=ping["instance_id"],
        timeout=timeout,
        read_only=read_only,
    )


def stop_role_bridge(
    role: str,
    *,
    quit: bool = False,
    cleanup_temp_docs: bool = False,
    cleanup_session_ids: list[str] | set[str] | None = None,
    expected_instance: dict[str, Any] | None = None,
    instance_id: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Request cli_stop_role on one role and report acceptance and listener port state honestly."""
    ping = resolve_role_instance(
        role,
        expected_instance=expected_instance,
        instance_id=instance_id,
        timeout=min(timeout, 5.0),
    )
    payload: dict[str, Any] = {
        "quit": bool(quit),
        "cleanup_temp_docs": bool(cleanup_temp_docs),
    }
    if cleanup_session_ids is not None:
        payload["cleanup_session_ids"] = sorted(cleanup_session_ids)
    try:
        reply = _raw_bridge_call(
            role,
            "cli_stop_role",
            payload,
            instance_id=ping["instance_id"],
            timeout=timeout,
            read_only=False,
        )
    except CliBridgeError as exc:
        if exc.acceptance_unconfirmed:
            return {
                "stop_accepted": False,
                "bridge_stopped": "unconfirmed",
                "port_open": is_port_open(role, timeout=0.15),
                "quit_requested": bool(quit),
                "last_confirmed_stage": "disconnect_before_stop_receipt",
                "error": exc.to_dict(),
                "ping": ping,
            }
        return {
            "stop_accepted": False,
            "bridge_stopped": False,
            "port_open": True,
            "quit_requested": bool(quit),
            "last_confirmed_stage": "stop_rejected",
            "error": exc.to_dict(),
            "ping": ping,
        }

    if not isinstance(reply, dict) or not reply.get("stop_accepted"):
        return {
            **(reply if isinstance(reply, dict) else {}),
            "stop_accepted": False,
            "bridge_stopped": False,
            "port_open": is_port_open(role, timeout=0.15),
            "ping": ping,
        }

    deadline = time.monotonic() + min(max(1.0, float(timeout)), 10.0)
    stopped: bool | str = False
    port_open = True
    while time.monotonic() < deadline:
        if not is_port_open(role, timeout=0.15):
            stopped = "unconfirmed"
            port_open = False
            break
        time.sleep(0.05)

    stop_error: dict[str, Any] | None = None
    if stopped is False:
        stop_error = {
            "code": "stop_timeout",
            "message": f"Bridge port for {role} remained open after stop_accepted",
        }
    return {
        **reply,
        "bridge_stopped": stopped,
        "port_open": port_open,
        **({"error": stop_error} if stop_error is not None else {}),
        "ping": ping,
    }


def resolve_model_data(model: str | Path | dict[str, Any] | None) -> dict[str, Any] | None:
    if model is None:
        return None
    if isinstance(model, dict):
        return dict(model)
    path = Path(model)
    if not path.is_file():
        raise ValueError(f"model.json file was not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("model.json must contain a JSON object")
    return data


def _resolve_expected_target(
    *,
    expected_target: dict[str, Any] | None = None,
    target_instance_id: str | None = None,
) -> dict[str, Any] | None:
    if expected_target is not None:
        if target_instance_id is not None and expected_target.get("instance_id") != target_instance_id:
            raise CliBridgeError("instance_changed", "expected_target conflicts with target_instance_id")
        return {"process_id": expected_target["process_id"], "instance_id": expected_target["instance_id"]}
    try:
        ping = probe_instance("target", timeout=3.0, instance_id=target_instance_id)
        return {"process_id": ping["process_id"], "instance_id": ping["instance_id"]}
    except CliBridgeError as exc:
        if target_instance_id is not None:
            raise
        if exc.code == "connection_failed":
            return None
        raise


def wait_for_action(
    *,
    instance_id: str,
    task_id: str | None,
    action_number: int | None,
    timeout: float = 300.0,
    poll_interval: float = 0.1,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, float(timeout))
    params: dict[str, Any] = {"task_id": task_id}
    if action_number is not None:
        params["action_number"] = action_number
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliBridgeError(
                "wait_timeout",
                f"Timed out after {timeout}s waiting for task={task_id} action={action_number}",
            )
        try:
            reply = _raw_bridge_call(
                "source",
                "cli_ui_wait",
                params,
                instance_id=instance_id,
                timeout=max(1.0, remaining),
                read_only=True,
            )
        except CliBridgeError as exc:
            if (
                exc.code == "wait_disconnected"
                and isinstance(exc.__cause__, TimeoutError)
                and time.monotonic() >= deadline
            ):
                raise CliBridgeError(
                    "wait_timeout",
                    f"Timed out after {timeout}s waiting for task={task_id} action={action_number}",
                ) from exc
            raise
        status = reply.get("action_status")
        if status in ("completed", "waiting_user_or_in_flight"):
            return reply
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliBridgeError(
                "wait_timeout",
                f"Timed out after {timeout}s waiting for task={task_id} action={action_number}",
            )
        time.sleep(min(poll_interval, remaining))


def ui_action(
    command: str,
    *,
    task_id: str | None = None,
    args: dict[str, Any] | None = None,
    model: str | Path | dict[str, Any] | None = None,
    wait: str = "idle",
    timeout: float = 300.0,
    source_instance: dict[str, Any] | None = None,
    source_instance_id: str | None = None,
    target_instance: dict[str, Any] | None = None,
    target_instance_id: str | None = None,
    role: str | None = None,
    quit: bool = False,
    cleanup_temp_docs: bool = False,
    on_accepted: Any = None,
) -> dict[str, Any]:
    """Invoke a Group 2 UI / task action synchronized with the Source panel."""
    accepted_tid: str | None = task_id
    accepted_num: int | None = None
    try:
        resolve_command(command, "ui")
        if wait not in ("idle", "dispatched"):
            raise ValueError(f"wait must be 'idle' or 'dispatched', got {wait!r}")
        arg_dict = dict(args or {})

        if command == "stop_role":
            if quit or arg_dict.get("quit"):
                raise ValueError("stop_role only releases the role and does not support quit; use stop_instances --quit")
            if cleanup_temp_docs or arg_dict.get("cleanup_temp_docs"):
                raise ValueError("stop_role does not support cleanup_temp_docs; use stop_instances --quit")
            unknown_keys = set(arg_dict) - {"role"}
            if unknown_keys:
                raise ValueError(f"Unknown stop_role arguments: {', '.join(sorted(unknown_keys))}")
            if role is not None and "role" in arg_dict and role != arg_dict["role"]:
                raise ValueError(f"Conflicting stop_role role: {role!r} vs args.role={arg_dict['role']!r}")
            target_role = role if role is not None else arg_dict.get("role", "source")
            if target_role not in ("source", "target"):
                raise ValueError(f"stop_role role must be 'source' or 'target', got {target_role!r}")
            expected = source_instance if target_role == "source" else target_instance
            iid = source_instance_id if target_role == "source" else target_instance_id
            res = stop_role_bridge(
                target_role,
                quit=False,
                cleanup_temp_docs=False,
                expected_instance=expected,
                instance_id=iid,
                timeout=min(timeout, 30.0),
            )
            ok = (
                bool(res.get("stop_accepted"))
                and not res.get("waiting_user_or_in_flight")
                and res.get("bridge_stopped") is not False
            )
            err = None
            if not ok:
                err = res.get("error") or {
                    "code": "waiting_user_or_in_flight" if res.get("waiting_user_or_in_flight") else "stop_rejected",
                    "message": res.get("last_confirmed_stage") or "Role stop was not accepted",
                }
            return envelope(
                ok=ok,
                group="ui",
                command="stop_role",
                result=res,
                error=err,
            )

        src_ping = resolve_role_instance(
            "source",
            expected_instance=source_instance,
            instance_id=source_instance_id,
        )
        params: dict[str, Any] = {"action": command, "task_id": task_id, "args": arg_dict}
        if model is not None:
            params["model_config"] = resolve_model_data(model)
        if command == "start_task":
            expected_tgt = _resolve_expected_target(
                expected_target=target_instance,
                target_instance_id=target_instance_id,
            )
            if expected_tgt is not None:
                params["expected_target"] = expected_tgt

        accepted = _raw_bridge_call(
            "source",
            "cli_ui_action",
            params,
            instance_id=src_ping["instance_id"],
            timeout=15.0,
            read_only=(command == "status"),
        )
        accepted_tid = accepted.get("task_id")
        accepted_num = accepted.get("action_number")
        if on_accepted is not None and command not in ("status",):
            on_accepted(accepted_tid, accepted_num)

        if command in ("new_task", "status") or accepted.get("completed") is True:
            return envelope(
                ok=True,
                group="ui",
                command=command,
                task_id=accepted_tid,
                action_number=accepted_num,
                result=accepted,
            )
        if wait == "dispatched" or (command == "pause" and accepted_num is None):
            return envelope(
                ok=bool(accepted.get("accepted", True)),
                group="ui",
                command=command,
                task_id=accepted_tid,
                action_number=accepted_num,
                result=accepted,
            )

        waited = wait_for_action(
            instance_id=src_ping["instance_id"],
            task_id=accepted_tid,
            action_number=accepted_num,
            timeout=timeout,
        )
        if waited.get("action_status") == "waiting_user_or_in_flight":
            return envelope(
                ok=False,
                group="ui",
                command=command,
                task_id=accepted_tid,
                action_number=accepted_num,
                result=waited,
                error={
                    "code": "waiting_user_or_in_flight",
                    "message": "Source dialog is waiting for user decision or in-flight work",
                },
            )
        if waited.get("followup_error") and command != "pause":
            return envelope(
                ok=False,
                group="ui",
                command=command,
                task_id=accepted_tid,
                action_number=accepted_num,
                result=waited,
                error=waited["followup_error"],
            )
        final_num = waited.get("action_number", accepted_num)
        if command == "end_task":
            ok = bool(waited.get("ended")) and not waited.get("session_retained") and not waited.get("error")
        elif command == "pause":
            ok = (waited.get("error") or {}).get("code") != "worker_failed"
        else:
            ok = bool(waited.get("ok"))
        return envelope(
            ok=ok,
            group="ui",
            command=command,
            task_id=waited.get("task_id", accepted_tid),
            action_number=final_num,
            result=waited,
            error=None if ok else waited.get("error"),
        )
    except Exception as exc:
        return envelope(
            ok=False,
            group="ui",
            command=command,
            task_id=accepted_tid,
            action_number=accepted_num,
            result=None,
            error=error_payload(exc),
        )


def _exec_fixed_command(
    group: str,
    command: str,
    args: dict[str, Any] | None = None,
    *,
    workflow: str | None = None,
    task_id: str | None = None,
    timeout: float = 180.0,
    source_instance: dict[str, Any] | None = None,
    source_instance_id: str | None = None,
    target_instance: dict[str, Any] | None = None,
    target_instance_id: str | None = None,
    on_accepted: Any = None,
) -> dict[str, Any]:
    accepted_tid: str | None = task_id
    accepted_num: int | None = None
    try:
        src_ping = resolve_role_instance(
            "source",
            expected_instance=source_instance,
            instance_id=source_instance_id,
        )
        params: dict[str, Any] = {
            "command": command,
            "task_id": task_id,
            "workflow": workflow,
            "args": to_json_value(args or {}),
        }
        if command == "agent_open_session":
            expected_tgt = _resolve_expected_target(
                expected_target=target_instance,
                target_instance_id=target_instance_id,
            )
            if expected_tgt is not None:
                params["expected_target"] = expected_tgt

        accepted = _raw_bridge_call(
            "source",
            "cli_exec_command",
            params,
            instance_id=src_ping["instance_id"],
            timeout=15.0,
            read_only=False,
        )
        accepted_tid = accepted.get("task_id")
        accepted_num = accepted.get("action_number")
        if on_accepted is not None:
            on_accepted(accepted_tid, accepted_num)

        waited = wait_for_action(
            instance_id=src_ping["instance_id"],
            task_id=accepted_tid,
            action_number=accepted_num,
            timeout=timeout,
        )
        if waited.get("action_status") == "waiting_user_or_in_flight":
            return envelope(
                ok=False,
                group=group,
                command=command,
                task_id=accepted_tid,
                action_number=accepted_num,
                result=waited,
                error={"code": "waiting_user_or_in_flight", "message": "Source is waiting for user decision"},
            )
        rows = waited.get("results") or []
        if rows:
            last = rows[-1]
            tool_result = {
                "value": last.get("value"),
                "is_error": bool(last.get("is_error")),
                "side_effect": last.get("side_effect", "none"),
                "images": last.get("images", []),
            }
            is_err = tool_result["is_error"]
            return envelope(
                ok=(not is_err) and bool(waited.get("ok", True)),
                group=group,
                command=command,
                task_id=waited.get("task_id", accepted_tid),
                action_number=waited.get("action_number", accepted_num),
                result=tool_result,
                error=waited.get("error") or (tool_result["value"] if is_err else None),
            )
        err = waited.get("error") or {"code": "execution_failed", "message": f"{command} did not return a tool result"}
        return envelope(
            ok=False,
            group=group,
            command=command,
            task_id=waited.get("task_id", accepted_tid),
            action_number=waited.get("action_number", accepted_num),
            result={"value": err, "is_error": True, "side_effect": "none", "images": []},
            error=err,
        )
    except Exception as exc:
        return envelope(
            ok=False,
            group=group,
            command=command,
            task_id=accepted_tid,
            action_number=accepted_num,
            result=None,
            error=error_payload(exc),
        )


def exec_flow(
    command: str,
    args: dict[str, Any] | None = None,
    *,
    workflow: str | None = None,
    task_id: str | None = None,
    timeout: float = 180.0,
    source_instance: dict[str, Any] | None = None,
    source_instance_id: str | None = None,
    target_instance: dict[str, Any] | None = None,
    target_instance_id: str | None = None,
    on_accepted: Any = None,
) -> dict[str, Any]:
    """Execute one of the 14 FIXED_FLOW_TOOLS commands via Source FixedSession."""
    try:
        resolve_command(command, "flow")
    except Exception as exc:
        return envelope(ok=False, group="flow", command=command, task_id=task_id, result=None, error=error_payload(exc))
    return _exec_fixed_command(
        "flow",
        command,
        args,
        workflow=workflow,
        task_id=task_id,
        timeout=timeout,
        source_instance=source_instance,
        source_instance_id=source_instance_id,
        target_instance=target_instance,
        target_instance_id=target_instance_id,
        on_accepted=on_accepted,
    )


def exec_modeling(
    command: str,
    args: dict[str, Any] | None = None,
    *,
    task_id: str | None = None,
    timeout: float = 180.0,
    source_instance: dict[str, Any] | None = None,
    source_instance_id: str | None = None,
    on_accepted: Any = None,
) -> dict[str, Any]:
    """Execute one of the 43 FEATURE_SPECS modeling commands via Source FixedSession."""
    try:
        resolve_command(command, "modeling")
    except Exception as exc:
        return envelope(ok=False, group="modeling", command=command, task_id=task_id, result=None, error=error_payload(exc))
    return _exec_fixed_command(
        "modeling",
        command,
        args,
        task_id=task_id,
        timeout=timeout,
        source_instance=source_instance,
        source_instance_id=source_instance_id,
        on_accepted=on_accepted,
    )
