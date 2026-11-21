"""Automated script.json runner with relative-path resolution, $var binding, expect checks and strict cleanup."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from REAgent.application.control import envelope, resolve_command, session_close_confirmed
from REAgent.application.remote import (
    error_payload,
    exec_flow,
    exec_modeling,
    is_port_open,
    probe_instance,
    ui_action,
)
from .launcher import is_process_alive, start_instances, stop_instances

_VAR_EXACT = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PATH_ARG_KEYS = frozenset(("source_path", "output_dir"))


def _validate_positive_number(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
        raise ValueError(f"{label} must be a positive finite number")


def _validate_script(data: Any) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ValueError("script.json must be an object with non-empty string 'name' and list 'steps'")
    if not isinstance(data.get("steps"), list):
        raise ValueError("script.json 'steps' must be a list")
    for key in ("reuse", "keep_open"):
        if key in data and data[key] is not None and not isinstance(data[key], bool):
            raise ValueError(f"script.json '{key}' must be a boolean")
    if "timeout" in data and data["timeout"] is not None:
        _validate_positive_number(data["timeout"], "script.json 'timeout'")
    for key in ("user_cfg", "freecad_exe"):
        if key in data and data[key] is not None and not isinstance(data[key], str):
            raise ValueError(f"script.json '{key}' must be a string path")
    if "model" in data and data["model"] is not None and not isinstance(data["model"], (str, dict)):
        raise ValueError("script.json 'model' must be a string path or JSON object")

    for index, step in enumerate(data["steps"]):
        if not isinstance(step, dict) or not isinstance(step.get("command"), str) or not step["command"]:
            raise ValueError(f"Step {index} must be an object with non-empty string 'command'")
        command = step["command"]
        resolve_command(command, step.get("group"))
        if "args" in step and step["args"] is not None and not isinstance(step["args"], dict):
            raise ValueError(f"Step {index} 'args' must be a JSON object")
        if "task_id" in step and step["task_id"] is not None and not isinstance(step["task_id"], str):
            raise ValueError(f"Step {index} 'task_id' must be a string")
        if "model" in step and step["model"] is not None and not isinstance(step["model"], (str, dict)):
            raise ValueError(f"Step {index} 'model' must be a string path or JSON object")
        if "workflow" in step and step["workflow"] is not None and not isinstance(step["workflow"], str):
            raise ValueError(f"Step {index} 'workflow' must be a string")
        if "wait" in step and step["wait"] is not None and step["wait"] not in ("idle", "dispatched"):
            raise ValueError(f"Step {index} 'wait' must be 'idle' or 'dispatched'")
        if "timeout" in step and step["timeout"] is not None:
            _validate_positive_number(step["timeout"], f"Step {index} 'timeout'")
        if "save_as" in step and step["save_as"] is not None:
            if not isinstance(step["save_as"], dict):
                raise ValueError(f"Step {index} 'save_as' must be a JSON object")
            for var_name, dotted in step["save_as"].items():
                if not isinstance(var_name, str) or not _VAR_NAME.match(var_name):
                    raise ValueError(f"Step {index} save_as variable name must be a valid identifier: {var_name!r}")
                if not isinstance(dotted, str) or not dotted:
                    raise ValueError(f"Step {index} save_as {var_name!r} must map to a non-empty dotted path string")
        if "expect" in step and step["expect"] is not None:
            if not isinstance(step["expect"], dict):
                raise ValueError(f"Step {index} 'expect' must be a JSON object")
            for dotted in step["expect"]:
                if not isinstance(dotted, str) or not dotted:
                    raise ValueError(f"Step {index} expect keys must be non-empty dotted path strings")


def _expand_vars(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = _VAR_EXACT.match(value)
        if match:
            name = match.group(1)
            if name not in variables:
                raise KeyError(f"Undefined script variable: ${name}")
            return variables[name]
        return value
    if isinstance(value, list):
        return [_expand_vars(item, variables) for item in value]
    if isinstance(value, dict):
        return {k: _expand_vars(v, variables) for k, v in value.items()}
    return value


def _resolve_rel(path_value: Any, base_dir: Path) -> Any:
    if isinstance(path_value, str) and path_value and not os.path.isabs(path_value):
        return str((base_dir / path_value).resolve())
    return path_value


def _resolve_step_paths(args: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    resolved = dict(args)
    for key in _PATH_ARG_KEYS:
        if key in resolved:
            resolved[key] = _resolve_rel(resolved[key], base_dir)
    if "paths" in resolved and isinstance(resolved["paths"], list):
        resolved["paths"] = [_resolve_rel(item, base_dir) for item in resolved["paths"]]
    return resolved


def _extract_path(obj: Any, dotted_path: Any) -> tuple[bool, Any]:
    if not isinstance(dotted_path, str) or not dotted_path:
        return False, None
    current = obj
    for segment in dotted_path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, (list, tuple)) and segment.isdigit():
            idx = int(segment)
            if idx >= len(current):
                return False, None
            current = current[idx]
        else:
            return False, None
    return True, current


def _json_equal(actual: Any, expected: Any) -> bool:
    if type(actual) is bool or type(expected) is bool:
        return type(actual) is type(expected) and actual is expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _json_equal(actual[k], expected[k]) for k in expected
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _json_equal(a, b) for a, b in zip(actual, expected)
        )
    return actual == expected


def run_script(script_path: str | Path, *, keep_open: bool | None = None) -> dict[str, Any]:
    """Execute a declarative script.json sequence against dual FreeCAD instances."""
    path = Path(script_path).resolve()
    if not path.is_file():
        return envelope(
            ok=False,
            group="lifecycle",
            command="run_script",
            result=None,
            error={"code": "script_not_found", "message": f"Script file was not found: {path}"},
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _validate_script(data)
    except Exception as exc:
        return envelope(
            ok=False,
            group="lifecycle",
            command="run_script",
            result=None,
            error=error_payload(exc),
        )

    base_dir = path.parent
    script_name = data["name"]
    top_user_cfg = _resolve_rel(data.get("user_cfg"), base_dir)
    top_model = _resolve_rel(data.get("model"), base_dir)
    top_freecad_exe = _resolve_rel(data.get("freecad_exe"), base_dir)
    script_reuse = True if data.get("reuse") is None else bool(data.get("reuse"))
    script_timeout = 30.0 if data.get("timeout") is None else float(data.get("timeout"))

    start_env = start_instances(
        freecad_exe=top_freecad_exe,
        user_cfg=top_user_cfg,
        timeout=script_timeout,
        reuse=script_reuse,
    )
    if not start_env["ok"]:
        return envelope(
            ok=False,
            group="lifecycle",
            command="run_script",
            result={
                "name": script_name,
                "passed": False,
                "step_count": 0,
                "failed_step": None,
                "steps": [],
                "variables": {},
                "cleanup": None,
                "acceptance_unconfirmed": False,
            },
            error=start_env["error"],
        )

    source_instance = start_env["result"]["source"]
    target_instance = start_env["result"]["target"]

    def instance_key(role_name: str, inst: dict[str, Any]) -> tuple[str, int, str]:
        return (role_name, inst["process_id"], inst["instance_id"])

    owned_instances: dict[tuple[str, int, str], dict[str, Any]] = {}
    for r, inst in (("source", source_instance), ("target", target_instance)):
        if inst.get("spawned_by_cli"):
            owned_instances[instance_key(r, inst)] = inst

    variables: dict[str, Any] = {}
    step_records: list[dict[str, Any]] = []
    confirmed_tasks: list[str] = []
    unclosed_tasks: set[str] = set()
    confirmed_session_ids: set[str] = set()
    current_task_id: str | None = None
    acceptance_unconfirmed = False
    passed = True
    failed_step: int | None = None
    failure_error: Any = None
    cleanup: dict[str, Any] | None = None
    cleanup_ok = True
    cleanup_error: Any = None

    def make_recorder(command: str):
        def record_accepted(tid: str | None, _anum: int | None) -> None:
            nonlocal current_task_id
            if command in ("new_task", "agent_open_session") and tid:
                if tid not in confirmed_tasks:
                    confirmed_tasks.append(tid)
                unclosed_tasks.add(tid)
                current_task_id = tid
        return record_accepted

    def record_session_id_from_env(env_payload: dict[str, Any], command_name: str) -> None:
        res = env_payload.get("result")
        if not isinstance(res, dict):
            return
        if command_name == "agent_open_session":
            val = res.get("value")
            if isinstance(val, dict):
                sid = val.get("session_id")
                if isinstance(sid, str) and sid:
                    confirmed_session_ids.add(sid)
        else:
            snap = res.get("snapshot")
            if isinstance(snap, dict):
                ctrl = snap.get("controller")
                if isinstance(ctrl, dict):
                    sid = ctrl.get("session_id")
                    if isinstance(sid, str) and sid:
                        confirmed_session_ids.add(sid)

    try:
        for index, step in enumerate(data["steps"]):
            if not isinstance(step, dict) or not isinstance(step.get("command"), str):
                passed = False
                failed_step = index
                failure_error = {"code": "invalid_script", "message": f"Step {index} must be an object with 'command'"}
                break
            command = step["command"]
            try:
                group = resolve_command(command, step.get("group"))
            except Exception as exc:
                passed = False
                failed_step = index
                failure_error = error_payload(exc)
                break

            try:
                raw_args = _expand_vars(step.get("args") or {}, variables)
                if not isinstance(raw_args, dict):
                    raise ValueError(f"Step {index} args must expand to a JSON object")
                args = _resolve_step_paths(raw_args, base_dir)
                step_tid = _expand_vars(step["task_id"], variables) if "task_id" in step else None
                if "task_id" not in step and group in ("ui", "flow", "modeling") and command not in ("new_task", "stop_role"):
                    if command != "agent_ping" or current_task_id is not None:
                        step_tid = current_task_id
                step_model = _expand_vars(step["model"], variables) if "model" in step else top_model
                step_model = _resolve_rel(step_model, base_dir)

                if group == "lifecycle":
                    if command == "start_instances":
                        env = start_instances(
                            freecad_exe=_resolve_rel(args.get("freecad_exe"), base_dir),
                            user_cfg=_resolve_rel(args.get("user_cfg"), base_dir),
                            timeout=float(args.get("timeout", script_timeout)),
                            reuse=bool(args.get("reuse", True)),
                        )
                        if env.get("ok"):
                            source_instance = env["result"]["source"]
                            target_instance = env["result"]["target"]
                            for r, inst in (("source", source_instance), ("target", target_instance)):
                                if inst.get("spawned_by_cli"):
                                    owned_instances[instance_key(r, inst)] = inst
                    elif command == "stop_instances":
                        env = stop_instances(
                            role=args.get("role", "both"),
                            quit=bool(args.get("quit", False)),
                            timeout=float(args.get("timeout", script_timeout)),
                            cleanup_temp_docs=bool(args.get("cleanup_temp_docs", True)),
                            source_instance=source_instance,
                            target_instance=target_instance,
                            allowed_task_ids=set(confirmed_tasks),
                        )
                        if env.get("ok"):
                            stopped_roles = env["result"].get("roles", {})
                            for r, info in stopped_roles.items():
                                inst = source_instance if r == "source" else target_instance
                                if info.get("status") == "already_exited" or (
                                    info.get("quit_requested") and env["result"].get("process_exited") is True
                                ):
                                    owned_instances.pop(instance_key(r, inst), None)
                    else:
                        env = envelope(
                            ok=False,
                            group="lifecycle",
                            command=command,
                            error={"code": "unsupported_step", "message": "Nested run_script steps are unsupported"},
                        )
                elif group == "ui":
                    ui_args = dict(args)
                    if command == "start_task" and "workflow" in step:
                        if "workflow" in ui_args and ui_args["workflow"] != step["workflow"]:
                            raise ValueError(f"Step {index} workflow conflicts with args.workflow")
                        ui_args["workflow"] = step["workflow"]
                    env = ui_action(
                        command,
                        task_id=step_tid,
                        args=ui_args,
                        model=step_model if command in ("start_task", "send") else None,
                        wait=step.get("wait", "idle"),
                        timeout=float(step.get("timeout", 300.0)),
                        source_instance=source_instance,
                        target_instance=target_instance if command in ("start_task", "stop_role") else None,
                        role=ui_args.get("role") if command == "stop_role" else None,
                        on_accepted=make_recorder(command),
                    )
                elif group == "flow":
                    env = exec_flow(
                        command,
                        args=args,
                        workflow=step.get("workflow"),
                        task_id=step_tid,
                        timeout=float(step.get("timeout", 180.0)),
                        source_instance=source_instance,
                        target_instance=target_instance if command == "agent_open_session" else None,
                        on_accepted=make_recorder(command),
                    )
                else:
                    env = exec_modeling(
                        command,
                        args=args,
                        task_id=step_tid,
                        timeout=float(step.get("timeout", 180.0)),
                        source_instance=source_instance,
                        on_accepted=make_recorder(command),
                    )

                step_err = env.get("error") or {}
                if isinstance(step_err, dict) and step_err.get("acceptance_unconfirmed"):
                    acceptance_unconfirmed = True
                    passed = False
                    failed_step = index
                    failure_error = step_err
                    step_records.append({
                        "step": index,
                        "group": group,
                        "command": command,
                        "passed": False,
                        "acceptance_unconfirmed": True,
                        "envelope": env,
                    })
                    break

                save_as = step.get("save_as")
                if save_as is not None and not isinstance(save_as, dict):
                    raise ValueError(f"Step {index} save_as must be a JSON object")
                expect_map = step.get("expect")
                if expect_map is not None and not isinstance(expect_map, dict):
                    raise ValueError(f"Step {index} expect must be a JSON object")
            except Exception as exc:
                passed = False
                failed_step = index
                failure_error = error_payload(exc)
                step_records.append({"step": index, "group": group, "command": command, "passed": False, "error": failure_error})
                break

            step_ok = True
            step_issues: list[str] = []

            for var_name, dotted in (save_as or {}).items():
                found, val = _extract_path(env, dotted)
                if not found:
                    step_ok = False
                    step_issues.append(f"save_as {var_name!r} path not found: {dotted}")
                else:
                    variables[var_name] = val

            if expect_map is not None:
                try:
                    for dotted, expected_val in expect_map.items():
                        expected_expanded = _expand_vars(expected_val, variables)
                        found, actual_val = _extract_path(env, dotted)
                        if not found:
                            step_ok = False
                            step_issues.append(f"expect path not found: {dotted}")
                        elif not _json_equal(actual_val, expected_expanded):
                            step_ok = False
                            step_issues.append(f"expect {dotted}: got {actual_val!r}, expected {expected_expanded!r}")
                except Exception as exc:
                    step_ok = False
                    step_issues.append(str(exc))
            elif not env.get("ok"):
                step_ok = False
                step_issues.append(f"step returned ok=False without expect override: {env.get('error')}")

            step_records.append({
                "step": index,
                "group": group,
                "command": command,
                "passed": step_ok,
                "issues": step_issues,
                "envelope": env,
            })
            if step_tid in confirmed_tasks or command in ("start_task", "agent_open_session"):
                record_session_id_from_env(env, command)
            if command == "end_task":
                closed_ok, _, _ = session_close_confirmed(env, is_fixed=False)
                if closed_ok:
                    if step_tid:
                        unclosed_tasks.discard(step_tid)
                    if current_task_id == step_tid:
                        current_task_id = None
            elif command == "agent_close_session":
                closed_ok, _, _ = session_close_confirmed(env, is_fixed=True)
                if closed_ok:
                    if step_tid:
                        unclosed_tasks.discard(step_tid)
                    if current_task_id == step_tid:
                        current_task_id = None
            elif command == "agent_manage_journal" and env.get("ok"):
                val = (env.get("result") or {}).get("value") or {}
                if val.get("session_retained") is False:
                    if step_tid:
                        unclosed_tasks.discard(step_tid)
                    if current_task_id == step_tid:
                        current_task_id = None
            if not step_ok:
                passed = False
                failed_step = index
                failure_error = {
                    "code": "step_assertion_failed",
                    "message": "; ".join(step_issues) or f"Step {index} ({command}) failed",
                }
                break
    finally:
        if acceptance_unconfirmed:
            cleanup = {
                "preserved_for_unconfirmed_acceptance": True,
                "confirmed_tasks": list(confirmed_tasks),
                "unclosed_tasks": sorted(unclosed_tasks),
                "owned_instances": [
                    {"role": r, "process_id": pid, "instance_id": iid}
                    for r, pid, iid in sorted(owned_instances)
                ],
            }
        else:
            close_reports: list[dict[str, Any]] = []
            try:
                if is_port_open("source"):
                    src_ping = probe_instance("source", expected_instance=source_instance)
                    st = ui_action("status", source_instance=src_ping)
                    if not st.get("ok"):
                        cleanup_ok = False
                        cleanup_error = st.get("error")
                        close_reports.append({"status_error": cleanup_error})
                    else:
                        res = st.get("result") or {}
                        snap = (res.get("details") or {}).get("snapshot") or {}
                        live_tid = res.get("task_id")
                        if live_tid in confirmed_tasks:
                            sid = (snap.get("controller") or {}).get("session_id")
                            if isinstance(sid, str) and sid:
                                confirmed_session_ids.add(sid)
                        if live_tid in confirmed_tasks and (
                            res.get("task_open") or snap.get("controller") is not None or snap.get("mode") in ("chat", "fixed")
                        ):
                            is_fixed_close = bool(snap.get("mode") == "fixed" and snap.get("controller") is not None)
                            if is_fixed_close:
                                close_env = exec_flow("agent_close_session", task_id=live_tid, source_instance=src_ping)
                            else:
                                close_env = ui_action("end_task", task_id=live_tid, wait="idle", source_instance=src_ping)
                            close_reports.append({"task_id": live_tid, "close": close_env})
                            close_err = close_env.get("error") or {}
                            if isinstance(close_err, dict) and close_err.get("acceptance_unconfirmed"):
                                acceptance_unconfirmed = True
                                cleanup_ok = False
                                cleanup_error = close_err
                            else:
                                closed_ok, close_err_norm, _ = session_close_confirmed(
                                    close_env,
                                    is_fixed=is_fixed_close,
                                )
                                if closed_ok:
                                    unclosed_tasks.discard(live_tid)
                                else:
                                    cleanup_ok = False
                                    cleanup_error = close_err_norm
                elif unclosed_tasks:
                    cleanup_ok = False
                    cleanup_error = {
                        "code": "source_unreachable_with_unclosed_tasks",
                        "message": f"Source bridge is unreachable while confirmed tasks {sorted(unclosed_tasks)} remain unclosed",
                    }
                    close_reports.append({"error": cleanup_error})
            except Exception as exc:
                cleanup_ok = False
                cleanup_error = error_payload(exc)
                close_reports.append({"error": cleanup_error})

            should_keep_open = bool(data.get("keep_open", False)) if keep_open is None else bool(keep_open)
            stop_env = None
            current_owned_roles = {
                r
                for r, inst in (("source", source_instance), ("target", target_instance))
                if instance_key(r, inst) in owned_instances
            }
            if not acceptance_unconfirmed and cleanup_ok and not should_keep_open and current_owned_roles:
                stop_role_arg = "both" if current_owned_roles == {"source", "target"} else next(iter(current_owned_roles))
                stop_env = stop_instances(
                    role=stop_role_arg,
                    quit=True,
                    cleanup_temp_docs=True,
                    cleanup_session_ids=confirmed_session_ids,
                    source_instance=source_instance if "source" in current_owned_roles else None,
                    target_instance=target_instance if "target" in current_owned_roles else None,
                    allowed_task_ids=set(confirmed_tasks),
                )
                stop_err = stop_env.get("error") or {}
                if isinstance(stop_err, dict) and stop_err.get("acceptance_unconfirmed"):
                    acceptance_unconfirmed = True
                    cleanup_ok = False
                    cleanup_error = stop_err
                elif not stop_env.get("ok"):
                    cleanup_ok = False
                    cleanup_error = stop_err or {
                        "code": "stop_cleanup_failed",
                        "message": "Failed to stop spawned FreeCAD instances during script cleanup",
                    }
                else:
                    for r in current_owned_roles:
                        inst = source_instance if r == "source" else target_instance
                        owned_instances.pop(instance_key(r, inst), None)
            if not acceptance_unconfirmed and cleanup_ok and not should_keep_open:
                lingering = [
                    {"role": r, "process_id": pid, "instance_id": iid}
                    for (r, pid, iid) in sorted(owned_instances)
                    if is_process_alive(pid)
                ]
                if lingering:
                    cleanup_ok = False
                    cleanup_error = {
                        "code": "owned_instance_left_running",
                        "message": f"Spawned FreeCAD instance(s) from this script remain alive: {lingering}",
                    }
            cleanup = {
                "confirmed_tasks": list(confirmed_tasks),
                "unclosed_tasks": sorted(unclosed_tasks),
                "owned_instances": [
                    {"role": r, "process_id": pid, "instance_id": iid}
                    for r, pid, iid in sorted(owned_instances)
                ],
                "task_cleanup": close_reports,
                "keep_open": should_keep_open,
                "stop_instances": stop_env,
            }
            if acceptance_unconfirmed:
                cleanup["preserved_for_unconfirmed_acceptance"] = True

    overall_ok = bool(passed and cleanup_ok and not acceptance_unconfirmed)
    return envelope(
        ok=overall_ok,
        group="lifecycle",
        command="run_script",
        task_id=current_task_id,
        result={
            "name": script_name,
            "passed": passed,
            "cleanup_ok": cleanup_ok,
            "step_count": len(step_records),
            "failed_step": failed_step,
            "steps": step_records,
            "variables": variables,
            "cleanup": cleanup,
            "acceptance_unconfirmed": acceptance_unconfirmed,
        },
        error=failure_error or cleanup_error,
    )
