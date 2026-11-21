"""Lightweight pure-Python CLI/UI shared contracts, mode rules, and completion projections."""

from __future__ import annotations

from typing import Any

from REAgent.cad.command_names import FIXED_FLOW_TOOLS, FIXED_TOOLS, MODEL_COMMANDS
from REAgent.json_values import to_json_value

LIFECYCLE_COMMANDS = ("start_instances", "stop_instances", "run_script")
UI_COMMANDS = (
    "new_task",
    "start_task",
    "send",
    "pause",
    "status",
    "compact",
    "compare",
    "save",
    "end_task",
    "stop_role",
)
GROUP_COMMANDS = {
    "lifecycle": LIFECYCLE_COMMANDS,
    "ui": UI_COMMANDS,
    "flow": FIXED_FLOW_TOOLS,
    "modeling": MODEL_COMMANDS,
}

COMMAND_GROUP: dict[str, str] = {}
for _group, _commands in GROUP_COMMANDS.items():
    for _cmd in _commands:
        if _cmd in COMMAND_GROUP:
            raise RuntimeError(f"Duplicate CLI command across groups: {_cmd}")
        COMMAND_GROUP[_cmd] = _group
if len(COMMAND_GROUP) != 70:
    raise RuntimeError(f"Expected 70 globally unique CLI commands, found {len(COMMAND_GROUP)}")

REQUIRED_CLI_CONTROLS = {
    "source": frozenset(("cli_stop_role", "cli_ui_action", "cli_ui_wait", "cli_exec_command")),
    "target": frozenset(("cli_stop_role",)),
}


def resolve_command(command: str, group: str | None = None) -> str:
    """Resolve and validate a globally unique CLI command and optional group."""
    if not isinstance(command, str) or command not in COMMAND_GROUP:
        raise ValueError(f"Unknown REAgent CLI command: {command!r}")
    actual = COMMAND_GROUP[command]
    if group is not None and group != actual:
        raise ValueError(f"Command {command!r} belongs to group {actual!r}, not {group!r}")
    return actual


def envelope(
    *,
    ok: bool,
    group: str,
    command: str,
    task_id: str | None = None,
    action_number: int | None = None,
    result: Any = None,
    error: Any = None,
) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "group": group,
        "command": command,
        "task_id": task_id,
        "action_number": action_number,
        "result": result,
        "error": error,
    }


def project_images_metadata(images_list: Any) -> list[dict[str, Any]]:
    """Strip PNG bytes while keeping pure JSON metadata for receipts and events."""
    return [
        {
            "image_id": item.image_id,
            "artifact_key": item.artifact_key,
            "width_px": item.width_px,
            "height_px": item.height_px,
            "mime_type": item.mime_type,
        }
        if hasattr(item, "image_id")
        else {key: value for key, value in item.items() if key != "data"}
        for item in images_list
    ]


def can_save_snapshot(snapshot: dict[str, Any] | None) -> bool:
    facts = (snapshot or {}).get("controller") or {}
    return bool(
        facts.get("target_document")
        and facts.get("session_status") == "ready"
        and not facts.get("write_block_sources", {}).get("controller")
    )


def mode_controls(
    *,
    snapshot: dict[str, Any] | None,
    task_open: bool,
    busy: bool,
    worker_failed: bool,
    worker_retiring: bool,
    input_dialog_open: bool,
    close_intent_active: bool,
    has_journal_cache: bool,
    has_image_entries: bool,
    workflow_names: tuple[str, ...],
    paired_views: bool,
) -> dict[str, bool]:
    """Single source of truth for GUI button enablement and CLI ui_action gating by session mode."""
    snap = snapshot or {}
    facts = snap.get("controller") or {}
    active = bool(facts)
    mode = snap.get("mode")
    fixed = mode == "fixed"
    chatting = mode == "chat"
    live = active or chatting
    blocked_base = worker_failed or worker_retiring or input_dialog_open
    idle = not (busy or blocked_base or close_intent_active)
    allow_busy_send = not ((busy and not live) or blocked_base or close_intent_active)
    writable = active and facts.get("session_status") == "ready" and not facts.get("target_writes_blocked")
    viewing = (not input_dialog_open) and (not close_intent_active)
    compares = (not fixed) and bool(paired_views)
    return {
        "settings": (not fixed) and idle and (not active or snap.get("status") in ("paused", "ready")),
        **{name: (not fixed) and idle and task_open and not active for name in workflow_names},
        "pause": busy and not close_intent_active,
        "compact": (not fixed) and idle and live and bool((snap.get("context") or {}).get("can_compact")),
        "compare": (not fixed) and idle and writable and compares and not facts.get("source_basis_unknown"),
        "compare_available": compares,
        "save": (not fixed) and idle and can_save_snapshot(snap),
        "journal": viewing and has_journal_cache,
        "close": (task_open or live or busy) and viewing and not worker_failed,
        "status": viewing,
        "images": viewing and has_image_entries,
        "new_task": idle and not task_open and not live,
        "send": (not fixed) and task_open and allow_busy_send,
    }


def project_close_terminal(
    *,
    task_id: str | None,
    snapshot: dict[str, Any] | None,
    task_open: bool,
    last_close: dict[str, Any] | None = None,
    journal_metadata: dict[str, Any] | None = None,
    worker_failed: bool = False,
) -> dict[str, Any]:
    """Build one consistent terminal close payload for both numbered and unnumbered end_task waits."""
    snap = snapshot or {}
    close_val = last_close if isinstance(last_close, dict) else {}
    session_retained = bool(snap.get("controller")) or bool(close_val.get("session_retained"))
    mode = snap.get("mode")
    ended = (not task_open) and (not session_retained) and (mode not in ("chat", "fixed"))

    error: dict[str, Any] | None = None
    if worker_failed:
        ok = False
        error = {"code": "worker_failed", "message": "Source worker exited unexpectedly"}
    elif session_retained:
        ok = False
        error = {"code": "session_retained", "message": "Session cleanup or journal remains pending"}
    elif not ended:
        ok = False
        error = {"code": "close_cancelled", "message": "Task close was cancelled or did not complete"}
    else:
        ok = True

    return to_json_value({
        "action_status": "completed",
        "ok": ok,
        "error": error,
        "task_id": task_id,
        "task_open": bool(task_open),
        "ended": bool(ended),
        "session_retained": bool(session_retained),
        "roles": close_val.get("roles", {}),
        "abandoned_roles": close_val.get("abandoned_roles", []),
        "journal": close_val.get("journal") or journal_metadata,
        "snapshot": snap,
    })


def project_completion(
    completion: dict[str, Any],
    *,
    task_id: str | None,
    task_open: bool,
    messages: list[dict[str, str]],
    journal_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a Worker completion packet into bounded, action-scoped JSON for CLI/UI consumers."""
    action = completion["action"]
    snapshot = completion.get("snapshot") or {}
    controller_facts = snapshot.get("controller") or {}
    new_results = bool(completion.get("new_results"))

    def project_rows(rows: Any) -> list[dict[str, Any]]:
        return [
            {
                **{k: v for k, v in row.items() if k != "images"},
                "task_id": task_id,
                "images": project_images_metadata(row.get("images", ())),
            }
            for row in rows
        ]

    projected_results = project_rows(completion.get("results") or ()) if new_results else []
    terminal_error = completion.get("terminal_error") or completion.get("error")
    ok = bool(completion.get("ok")) and terminal_error is None

    entry: dict[str, Any] = {
        "task_id": task_id,
        "action_number": completion["action_number"],
        "action": action,
        "command": completion.get("command"),
        "action_status": "completed",
        "ok": ok,
        "error": terminal_error,
        "session_status": controller_facts.get("session_status"),
        "reason": snapshot.get("reason"),
        "task_open": bool(task_open),
        "paused_before_start": bool(completion.get("paused_before_start")),
        "presentation_backlog": bool(completion.get("presentation_backlog")),
        "results": projected_results,
        "new_results": new_results,
        "comparison": completion.get("comparison"),
        "comparison_results": project_rows(completion.get("comparison_results") or ()),
        "delivery_receipt": controller_facts.get("delivery_receipt"),
        "messages": list(messages),
        "snapshot": snapshot,
    }
    if action == "save":
        last_val = (
            projected_results[-1].get("value")
            if projected_results and isinstance(projected_results[-1].get("value"), dict)
            else {}
        )
        if last_val.get("saved") is True:
            entry["last_saved_checkpoint"] = {
                "saved": True,
                "saved_to": last_val.get("saved_to"),
                "saved_revision": last_val.get("saved_revision"),
                **(last_val.get("last_saved_checkpoint") or {}),
            }
        else:
            entry["last_saved_checkpoint"] = {"saved": False, "saved_to": None}
    if action == "compact":
        entry["last_compaction"] = (snapshot.get("context") or {}).get("last_compaction") if ok else None
    if action == "close":
        last_close = (
            projected_results[-1]["value"]
            if projected_results
            and projected_results[-1].get("function") == "agent_close_session"
            and isinstance(projected_results[-1].get("value"), dict)
            else {}
        )
        terminal = project_close_terminal(
            task_id=task_id,
            snapshot=snapshot,
            task_open=task_open,
            last_close=last_close,
            journal_metadata=journal_metadata,
        )
        entry.update({
            "ended": terminal["ended"],
            "session_retained": terminal["session_retained"],
            "roles": terminal["roles"],
            "abandoned_roles": terminal["abandoned_roles"],
            "journal": terminal["journal"],
        })
        if terminal_error is not None:
            entry["ok"] = False
            entry["error"] = terminal_error
        else:
            entry["ok"] = terminal["ok"]
            entry["error"] = terminal["error"]
    return to_json_value(entry)


def session_close_confirmed(
    close_env: Any,
    *,
    is_fixed: bool,
) -> tuple[bool, dict[str, Any] | None, dict[str, Any]]:
    """Verify session close receipt proves ok, ended=True, and session_retained=False."""
    if not isinstance(close_env, dict):
        return False, {"code": "session_close_unconfirmed", "message": "Missing session close response"}, {}
    res = close_env.get("result")
    raw = res.get("value") if is_fixed and isinstance(res, dict) else res
    val = dict(raw) if isinstance(raw, dict) else {}
    ok = bool(close_env.get("ok"))
    ended = val.get("ended") is True
    retained = bool(val.get("session_retained", not ok))
    confirmed = bool(ok and ended and not retained)
    if confirmed:
        return True, None, val
    err = close_env.get("error")
    if isinstance(err, dict):
        return False, err, val
    if retained:
        return False, {
            "code": "session_retained",
            "message": "Session cleanup or journal remains retained",
        }, val
    return False, {
        "code": "session_close_unconfirmed",
        "message": "Session close did not confirm ended=True",
    }, val
