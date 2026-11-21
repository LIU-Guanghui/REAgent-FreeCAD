"""CLI argument parser and JSON envelope dispatcher for `python -m REAgent`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from REAgent.application.control import COMMAND_GROUP, GROUP_COMMANDS, envelope, resolve_command
from REAgent.application.remote import error_payload
from .client import exec_flow, exec_modeling, ui_action
from .launcher import start_instances, stop_instances
from .runner import run_script


def _load_json_arg(raw: str | None, args_file: str | None, label: str = "args") -> dict[str, Any]:
    if raw is not None and args_file is not None:
        raise ValueError(f"Pass either --{label} or --{label}-file, not both")
    if args_file is not None:
        data = json.loads(Path(args_file).read_text(encoding="utf-8"))
    elif raw is not None:
        data = json.loads(raw)
    else:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"--{label} must decode to a JSON object")
    return data


def _build_parser(group: str, command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python -m REAgent {command}")
    if command == "start_instances":
        parser.add_argument("--freecad-exe", default=None)
        parser.add_argument("--user-cfg", default=None)
        parser.add_argument("--timeout", type=float, default=30.0)
        parser.add_argument("--no-reuse", action="store_true")
    elif command == "stop_instances":
        parser.add_argument("--role", choices=("both", "source", "target"), default="both")
        parser.add_argument("--quit", action="store_true")
        parser.add_argument("--timeout", type=float, default=30.0)
        parser.add_argument("--no-cleanup-temp-docs", action="store_true")
    elif command == "run_script":
        parser.add_argument("script_path")
        keep_group = parser.add_mutually_exclusive_group()
        keep_group.add_argument("--keep-open", dest="keep_open", action="store_true", default=None)
        keep_group.add_argument("--no-keep-open", dest="keep_open", action="store_false")
    elif group == "ui":
        parser.add_argument("--args", default=None)
        parser.add_argument("--args-file", default=None)
        parser.add_argument("--source-instance-id", default=None)
        if command == "stop_role":
            parser.add_argument("--role", choices=("source", "target"), default=None)
            parser.add_argument("--timeout", type=float, default=30.0)
            parser.add_argument("--target-instance-id", default=None)
            return parser
        if command == "new_task":
            return parser
        parser.add_argument("--task-id", default=None)
        if command == "status":
            return parser
        parser.add_argument("--wait", choices=("idle", "dispatched"), default="idle")
        parser.add_argument("--timeout", type=float, default=300.0)
        if command == "start_task":
            parser.add_argument("--model", default=None)
            parser.add_argument("--workflow", default=None)
            parser.add_argument("--source-path", default=None)
            parser.add_argument("--paths", nargs="+", default=None)
            parser.add_argument("--output-dir", default=None)
            parser.add_argument("--objective", default=None)
            parser.add_argument("--document", default=None)
            parser.add_argument("--observation-policy", default=None)
            parser.add_argument("--linear-tolerance-mm", type=float, default=None)
            parser.add_argument("--angular-tolerance-deg", type=float, default=None)
            parser.add_argument("--target-instance-id", default=None)
        elif command == "send":
            parser.add_argument("--model", default=None)
            parser.add_argument("--text", default=None)
        elif command == "compare":
            parser.add_argument("--options", default=None)
            parser.add_argument("--view-request", default=None)
    elif group == "flow":
        parser.add_argument("--task-id", default=None)
        parser.add_argument("--timeout", type=float, default=180.0)
        parser.add_argument("--args", default=None)
        parser.add_argument("--args-file", default=None)
        parser.add_argument("--source-instance-id", default=None)
        if command in ("agent_ping", "agent_open_session"):
            parser.add_argument("--workflow", default=None)
        if command == "agent_open_session":
            parser.add_argument("--source-path", default=None)
            parser.add_argument("--paths", nargs="+", default=None)
            parser.add_argument("--output-dir", default=None)
            parser.add_argument("--document", default=None)
            parser.add_argument("--observation-policy", default=None)
            parser.add_argument("--linear-tolerance-mm", type=float, default=None)
            parser.add_argument("--angular-tolerance-deg", type=float, default=None)
            parser.add_argument("--target-instance-id", default=None)
    else:
        parser.add_argument("--task-id", default=None)
        parser.add_argument("--timeout", type=float, default=180.0)
        parser.add_argument("--args", default=None)
        parser.add_argument("--args-file", default=None)
        parser.add_argument("--source-instance-id", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    if not tokens or tokens[0] in ("-h", "--help"):
        help_payload = {
            "usage": "python -m REAgent [<group>] <command> [options]",
            "groups": {k: list(v) for k, v in GROUP_COMMANDS.items()},
        }
        print(json.dumps(help_payload, ensure_ascii=False, indent=2))
        return 0

    group: str | None = None
    if tokens[0] in GROUP_COMMANDS:
        group = tokens[0]
        if len(tokens) < 2 or tokens[1] in ("-h", "--help"):
            print(json.dumps({"group": group, "commands": list(GROUP_COMMANDS[group])}, ensure_ascii=False, indent=2))
            return 0 if len(tokens) > 1 else 2
        command = tokens[1]
        rest = tokens[2:]
    else:
        command = tokens[0]
        rest = tokens[1:]

    try:
        resolved_group = resolve_command(command, group)
        parser = _build_parser(resolved_group, command)
        ns = parser.parse_args(rest)

        if command == "start_instances":
            out = start_instances(
                freecad_exe=ns.freecad_exe,
                user_cfg=ns.user_cfg,
                timeout=ns.timeout,
                reuse=not ns.no_reuse,
            )
        elif command == "stop_instances":
            out = stop_instances(
                role=ns.role,
                quit=ns.quit,
                timeout=ns.timeout,
                cleanup_temp_docs=not ns.no_cleanup_temp_docs,
            )
        elif command == "run_script":
            out = run_script(ns.script_path, keep_open=ns.keep_open)
        elif resolved_group == "ui":
            args = _load_json_arg(ns.args, ns.args_file)
            if command == "start_task":
                for key, val in (
                    ("workflow", ns.workflow),
                    ("source_path", ns.source_path),
                    ("paths", ns.paths),
                    ("output_dir", ns.output_dir),
                    ("objective", ns.objective),
                    ("document", ns.document),
                    ("observation_policy", ns.observation_policy),
                    ("linear_tolerance_mm", ns.linear_tolerance_mm),
                    ("angular_tolerance_deg", ns.angular_tolerance_deg),
                ):
                    if val is not None:
                        args[key] = val
            elif command == "send" and ns.text is not None:
                args["text"] = ns.text
            elif command == "compare":
                if ns.options is not None:
                    args["options"] = json.loads(ns.options)
                if ns.view_request is not None:
                    args["view_request"] = json.loads(ns.view_request)
            out = ui_action(
                command,
                task_id=getattr(ns, "task_id", None),
                args=args,
                model=getattr(ns, "model", None),
                wait=getattr(ns, "wait", "idle"),
                timeout=getattr(ns, "timeout", 300.0),
                source_instance_id=ns.source_instance_id,
                target_instance_id=getattr(ns, "target_instance_id", None),
                role=getattr(ns, "role", None),
            )
        elif resolved_group == "flow":
            args = _load_json_arg(ns.args, ns.args_file)
            if command == "agent_open_session":
                for key, val in (
                    ("source_path", ns.source_path),
                    ("paths", ns.paths),
                    ("output_dir", ns.output_dir),
                    ("document", ns.document),
                    ("observation_policy", ns.observation_policy),
                    ("linear_tolerance_mm", ns.linear_tolerance_mm),
                    ("angular_tolerance_deg", ns.angular_tolerance_deg),
                ):
                    if val is not None:
                        args[key] = val
            out = exec_flow(
                command,
                args,
                workflow=getattr(ns, "workflow", None),
                task_id=ns.task_id,
                timeout=ns.timeout,
                source_instance_id=ns.source_instance_id,
                target_instance_id=getattr(ns, "target_instance_id", None),
            )
        else:
            args = _load_json_arg(ns.args, ns.args_file)
            out = exec_modeling(
                command,
                args,
                task_id=ns.task_id,
                timeout=ns.timeout,
                source_instance_id=ns.source_instance_id,
            )
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    except Exception as exc:
        resolved = COMMAND_GROUP.get(command, group or "unknown")
        out = envelope(
            ok=False,
            group=resolved,
            command=command,
            result=None,
            error=error_payload(exc),
        )

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1
