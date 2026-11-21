"""FreeCAD dual-instance launcher, isolated user.cfg provisioning, managed registry and two-phase stop."""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Lock
from typing import Any

from REAgent.application.control import envelope, session_close_confirmed
from REAgent.application.remote import (
    ADDON_PATH,
    CliBridgeError,
    error_payload,
    exec_flow,
    is_port_open,
    resolve_role_instance,
    stop_role_bridge,
    ui_action,
)

DEFAULT_FREECAD_EXE = r"C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe"
_EMPTY_USER_CFG = (
    '<?xml version="1.0" encoding="utf-8" standalone="no" ?>\n'
    '<FCParameters>\n'
    '  <FCParamGroup Name="Root"/>\n'
    '</FCParameters>\n'
)
_MANAGED_REGISTRY_DIR = Path(tempfile.gettempdir()) / "reagent_cli_managed"

_MANAGED_LOCK = Lock()
_MANAGED_POPENS: dict[tuple[str, int, str], subprocess.Popen[bytes]] = {}


def resolve_freecad_exe(freecad_exe: str | Path | None = None) -> Path:
    candidate = freecad_exe or os.environ.get("FREECAD_EXE") or DEFAULT_FREECAD_EXE
    path = Path(candidate)
    if not path.is_file():
        raise CliBridgeError(
            "freecad_not_found",
            f"FreeCAD executable was not found at {path}. Pass freecad_exe or set FREECAD_EXE.",
        )
    return path


def _version_sort_key(name: str) -> tuple[int, ...]:
    nums = [int(part) for part in re.findall(r"\d+", name)]
    return tuple(nums) if nums else (0,)


def default_user_cfg_path(freecad_exe: str | Path | None = None) -> Path | None:
    """Locate the current user's default FreeCAD user.cfg, preferring versioned subdirectories on Windows."""
    candidates: list[Path] = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            base = Path(appdata) / "FreeCAD"
            exe_hint = str(freecad_exe or os.environ.get("FREECAD_EXE") or DEFAULT_FREECAD_EXE)
            match = re.search(r"FreeCAD\s+(\d+)\.(\d+)", exe_hint, re.IGNORECASE)
            if match:
                preferred = base / f"v{match.group(1)}-{match.group(2)}" / "user.cfg"
                if preferred.is_file():
                    candidates.append(preferred)
            if base.is_dir():
                versioned = [sub for sub in base.glob("v*/user.cfg") if sub.is_file()]
                versioned.sort(key=lambda item: _version_sort_key(item.parent.name), reverse=True)
                for sub in versioned:
                    if sub not in candidates:
                        candidates.append(sub)
            direct = base / "user.cfg"
            if direct.is_file() and direct not in candidates:
                candidates.append(direct)
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "FreeCAD" / "user.cfg"
        legacy = Path.home() / ".FreeCAD" / "user.cfg"
        for item in (xdg, legacy):
            if item.is_file():
                candidates.append(item)
    return candidates[0] if candidates else None


def _prepare_temp_user_cfg(role: str, source_cfg: Path | None, temp_dir: Path) -> Path:
    cfg_path = temp_dir / "user.cfg"
    if role == "source" and source_cfg is not None:
        shutil.copy2(source_cfg, cfg_path)
    else:
        cfg_path.write_text(_EMPTY_USER_CFG, encoding="utf-8")
    return cfg_path


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        WAIT_TIMEOUT = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _wait_process_exit(pid: int, timeout: float, proc: subprocess.Popen[bytes] | None = None) -> bool:
    wait_seconds = max(1.0, float(timeout))
    if proc is not None:
        try:
            proc.wait(timeout=wait_seconds)
            return True
        except subprocess.TimeoutExpired:
            return False
    if os.name == "nt":
        SYNCHRONIZE = 0x00100000
        WAIT_OBJECT_0 = 0
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            if ctypes.get_last_error() != 5:
                return True
        else:
            try:
                result = kernel32.WaitForSingleObject(handle, int(wait_seconds * 1000))
                return result == WAIT_OBJECT_0
            finally:
                kernel32.CloseHandle(handle)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not _is_pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _is_pid_alive(pid)


def is_process_alive(pid: int) -> bool:
    """Return True if a host OS process with pid is currently alive."""
    return _is_pid_alive(pid)


def _managed_record_path(role: str, pid: int, iid: str) -> Path:
    safe_iid = re.sub(r"[^A-Za-z0-9_-]", "_", iid)
    return _MANAGED_REGISTRY_DIR / f"{role}_{pid}_{safe_iid}.json"


def _is_managed_temp_dir(path_str: str | None) -> bool:
    if not path_str:
        return False
    try:
        path = Path(path_str).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        return path.parent == temp_root and path.name.startswith("reagent_")
    except Exception:
        return False


def _sweep_dead_managed_entries() -> list[dict[str, Any]]:
    if not _MANAGED_REGISTRY_DIR.is_dir():
        return []
    live: list[dict[str, Any]] = []
    for rec_path in _MANAGED_REGISTRY_DIR.glob("*.json"):
        try:
            item = json.loads(rec_path.read_text(encoding="utf-8"))
        except Exception:
            item = None
        pid = item.get("process_id") if isinstance(item, dict) else None
        if isinstance(item, dict) and isinstance(pid, int) and _is_pid_alive(pid):
            live.append(item)
        else:
            try:
                rec_path.unlink(missing_ok=True)
            except OSError:
                pass
            temp_dir = item.get("temp_dir") if isinstance(item, dict) else None
            if _is_managed_temp_dir(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
    return live


def _register_managed_entry(
    role: str,
    ping: dict[str, Any],
    proc: subprocess.Popen[bytes],
    temp_dir: Path,
    user_cfg_source: str | None,
) -> tuple[str, int, str]:
    pid = ping["process_id"]
    iid = ping["instance_id"]
    mem_key = (role, pid, iid)
    rec_path = _managed_record_path(role, pid, iid)
    record = {
        "role": role,
        "process_id": pid,
        "instance_id": iid,
        "temp_dir": str(temp_dir),
        "user_cfg_source": user_cfg_source,
        "addon_path": ADDON_PATH,
    }
    with _MANAGED_LOCK:
        _sweep_dead_managed_entries()
        _MANAGED_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="reagent_reg_", suffix=".tmp", dir=str(_MANAGED_REGISTRY_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, rec_path)
        except Exception as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise CliBridgeError(
                "managed_registry_write_failed",
                f"Failed to persist CLI managed record for {role} pid={pid}: {exc}",
            ) from exc
        _MANAGED_POPENS[mem_key] = proc
    return mem_key


def _get_managed_record(role: str, ping: dict[str, Any]) -> dict[str, Any] | None:
    pid = ping.get("process_id")
    iid = ping.get("instance_id")
    if not isinstance(pid, int) or not isinstance(iid, str):
        return None
    mem_key = (role, pid, iid)
    rec_path = _managed_record_path(role, pid, iid)
    with _MANAGED_LOCK:
        proc = _MANAGED_POPENS.get(mem_key)
        disk_entry: dict[str, Any] | None = None
        if rec_path.is_file():
            try:
                loaded = json.loads(rec_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    disk_entry = loaded
            except Exception:
                disk_entry = None
        if proc is None and disk_entry is None:
            return None
        return {
            **(disk_entry or {}),
            "popen": proc,
        }


def _list_managed_records(*, role: str | None = None) -> list[dict[str, Any]]:
    with _MANAGED_LOCK:
        live = _sweep_dead_managed_entries()
    if role is None:
        return live
    return [item for item in live if item.get("role") == role]


def _pop_managed_record(role: str, pid: int, iid: str) -> dict[str, Any] | None:
    mem_key = (role, pid, iid)
    rec_path = _managed_record_path(role, pid, iid)
    with _MANAGED_LOCK:
        proc = _MANAGED_POPENS.pop(mem_key, None)
        disk_entry: dict[str, Any] | None = None
        if rec_path.is_file():
            try:
                loaded = json.loads(rec_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    disk_entry = loaded
            except Exception:
                disk_entry = None
            try:
                rec_path.unlink(missing_ok=True)
            except OSError:
                pass
        if proc is None and disk_entry is None:
            return None
        return {**(disk_entry or {}), "popen": proc}


def _cleanup_spawned(spawned: list[dict[str, Any]]) -> None:
    for item in spawned:
        proc = item.get("popen")
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=4.0)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
        managed_key = item.get("managed_key")
        if managed_key is not None:
            _pop_managed_record(*managed_key)
        temp_dir = item.get("temp_dir")
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _check_source_idle(ping: dict[str, Any]) -> None:
    st = ui_action("status", source_instance=ping)
    if not st["ok"]:
        raise CliBridgeError(
            "source_status_failed",
            f"Unable to query Source status before launch/reuse: {(st.get('error') or {}).get('message')}",
        )
    res = st["result"] or {}
    snapshot = (res.get("details") or {}).get("snapshot") or {}
    if res.get("task_open") or snapshot.get("controller") is not None or snapshot.get("mode") in ("chat", "fixed"):
        raise CliBridgeError(
            "task_active",
            "Existing Source instance retains an open, active or paused task; close it or pass reuse=False after stopping",
        )


def _same_cfg_path(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _instance_summary(
    ping: dict[str, Any],
    *,
    reused: bool,
    spawned_by_cli: bool,
    user_cfg_source: str | None = None,
) -> dict[str, Any]:
    return {
        "process_id": ping["process_id"],
        "instance_id": ping["instance_id"],
        "port": ping["port"],
        "addon_path": ping["addon_path"],
        "cli_controls": list(ping.get("cli_controls") or ()),
        "cli_managed": bool(ping.get("cli_managed")),
        "user_cfg_source": user_cfg_source,
        "reused": reused,
        "spawned_by_cli": spawned_by_cli,
        "window": ping.get("window"),
    }


def start_instances(
    *,
    freecad_exe: str | Path | None = None,
    user_cfg: str | Path | None = None,
    timeout: float = 30.0,
    reuse: bool = True,
) -> dict[str, Any]:
    """Start or verify reusable Source/Target FreeCAD GUI instances running matching installed Addon."""
    spawned: list[dict[str, Any]] = []
    try:
        explicit_user_cfg = user_cfg is not None
        source_cfg: Path | None = None
        if explicit_user_cfg:
            source_cfg = Path(user_cfg).resolve()
            if not source_cfg.is_file():
                raise ValueError(f"user_cfg file was not found: {source_cfg}")
        else:
            source_cfg = default_user_cfg_path(freecad_exe)
        source_cfg_str = str(source_cfg.resolve()) if source_cfg is not None else None

        ready: dict[str, dict[str, Any]] = {}
        missing_roles: list[str] = []
        for role in ("target", "source"):
            if is_port_open(role):
                if not reuse:
                    raise CliBridgeError(
                        "port_in_use",
                        f"Bridge port for {role} is already bound; stop existing instances or use reuse=True",
                    )
                ping = resolve_role_instance(role)
                managed_rec = _get_managed_record(role, ping)
                existing_cfg = ping.get("user_cfg_source") or (managed_rec or {}).get("user_cfg_source")
                if role == "source" and explicit_user_cfg:
                    if not _same_cfg_path(existing_cfg, source_cfg_str):
                        raise CliBridgeError(
                            "user_cfg_mismatch",
                            f"Existing Source instance uses user_cfg {existing_cfg!r}, which does not match explicit user_cfg {source_cfg_str!r}. "
                            "Stop Source first or pass matching user_cfg.",
                        )
                if ping.get("active_task") is not None:
                    raise CliBridgeError(
                        "task_active",
                        f"Existing {role} instance retains an active or unresolved native task",
                    )
                if role == "source":
                    _check_source_idle(ping)
                ready[role] = _instance_summary(
                    ping,
                    reused=True,
                    spawned_by_cli=False,
                    user_cfg_source=existing_cfg,
                )
            else:
                missing_roles.append(role)

        if missing_roles:
            exe = resolve_freecad_exe(freecad_exe)
            if not explicit_user_cfg:
                source_cfg = default_user_cfg_path(exe)
                source_cfg_str = str(source_cfg.resolve()) if source_cfg is not None else None
            pending: dict[str, dict[str, Any]] = {}
            for role in missing_roles:
                temp_dir = Path(tempfile.mkdtemp(prefix=f"reagent_{role}_"))
                entry: dict[str, Any] = {"role": role, "popen": None, "temp_dir": temp_dir, "managed_key": None}
                spawned.append(entry)
                cfg_path = _prepare_temp_user_cfg(role, source_cfg if role == "source" else None, temp_dir)
                env = {
                    **os.environ,
                    "REAGENT_BOOTSTRAP_ROLE": role,
                    "REAGENT_CLI_MANAGED": "1",
                    "REAGENT_BOOTSTRAP_TIMEOUT": str(int(max(30.0, float(timeout)))),
                }
                if role == "source" and source_cfg_str is not None:
                    env["REAGENT_USER_CFG_SOURCE"] = source_cfg_str
                proc = subprocess.Popen([str(exe), "--user-cfg", str(cfg_path)], env=env)
                entry["popen"] = proc
                pending[role] = entry

            deadline = time.monotonic() + max(1.0, float(timeout))
            last_errors: dict[str, str] = {}
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    detail = "; ".join(f"{r}: {msg}" for r, msg in sorted(last_errors.items()))
                    raise CliBridgeError(
                        "startup_timeout",
                        f"Timed out after {timeout}s waiting for FreeCAD roles {sorted(pending)} to bind Bridge"
                        + (f" ({detail})" if detail else ""),
                    )
                for role in list(pending):
                    entry = pending[role]
                    proc = entry["popen"]
                    if proc.poll() is not None:
                        raise CliBridgeError(
                            "process_exited",
                            f"Spawned FreeCAD {role} process exited early with code {proc.returncode}",
                        )
                    if not is_port_open(role, timeout=0.15):
                        continue
                    try:
                        ping = resolve_role_instance(role, timeout=2.0)
                        if ping.get("process_id") != proc.pid:
                            raise CliBridgeError(
                                "pid_mismatch",
                                f"{role.title()} bridge PID {ping.get('process_id')} differs from spawned PID {proc.pid}",
                            )
                        if ping.get("active_task") is not None:
                            raise CliBridgeError("task_active", f"Newly spawned {role} reported active_task")
                        if role == "source":
                            _check_source_idle(ping)
                    except CliBridgeError as exc:
                        if exc.code in ("connection_failed", "connection_closed", "gui_unavailable"):
                            last_errors[role] = f"{exc.code}: {exc}"
                            continue
                        raise
                    managed_key = _register_managed_entry(
                        role,
                        ping,
                        proc,
                        entry["temp_dir"],
                        source_cfg_str if role == "source" else None,
                    )
                    entry["managed_key"] = managed_key
                    ready[role] = _instance_summary(
                        ping,
                        reused=False,
                        spawned_by_cli=True,
                        user_cfg_source=source_cfg_str if role == "source" else None,
                    )
                    del pending[role]
                if pending:
                    time.sleep(min(0.2, max(0.05, deadline - time.monotonic())))

        if ready["source"]["process_id"] == ready["target"]["process_id"]:
            raise CliBridgeError("same_process", "Source and Target must run in separate FreeCAD GUI processes")

        return envelope(
            ok=True,
            group="lifecycle",
            command="start_instances",
            result={
                "addon_path": ADDON_PATH,
                "source": ready["source"],
                "target": ready["target"],
            },
        )
    except Exception as exc:
        _cleanup_spawned(spawned)
        return envelope(
            ok=False,
            group="lifecycle",
            command="start_instances",
            result=None,
            error=error_payload(exc),
        )


def _stop_error_envelope(
    *,
    code: str,
    message: str,
    task_id: str | None = None,
    session_released: bool = False,
    role_results: dict[str, Any] | None = None,
    abandoned_roles: list[str] | None = None,
    last_stage: str = "rejected",
    retained_reason: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return envelope(
        ok=False,
        group="lifecycle",
        command="stop_instances",
        task_id=task_id,
        result={
            "session_released": session_released,
            "roles": role_results or {},
            "abandoned_roles": abandoned_roles or [],
            "stop_accepted": False,
            "bridge_stopped": False,
            "quit_requested": False,
            "process_exited": None,
            "last_confirmed_stage": last_stage,
            "retained_reason": retained_reason or message,
        },
        error=error or {"code": code, "message": message},
    )


def stop_instances(
    *,
    role: str = "both",
    quit: bool = False,
    timeout: float = 30.0,
    cleanup_temp_docs: bool = True,
    cleanup_session_ids: set[str] | list[str] | None = None,
    source_instance: dict[str, Any] | None = None,
    target_instance: dict[str, Any] | None = None,
    allowed_task_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Two-phase graceful stop: end/close owned session while online, release roles and optionally quit owned processes."""
    role_results: dict[str, Any] = {}
    abandoned_roles: list[str] = []
    session_released = True
    retained_reason: str | None = None
    last_stage = "idle"
    stop_error: dict[str, Any] | None = None

    try:
        if role not in ("both", "source", "target"):
            raise ValueError(f"role must be 'both', 'source', or 'target', got {role!r}")
        roles = ("source", "target") if role == "both" else (role,)

        resolved_pings: dict[str, dict[str, Any]] = {}
        managed_records: dict[str, dict[str, Any] | None] = {}
        for r in roles:
            if is_port_open(r):
                expected = source_instance if r == "source" else target_instance
                ping = resolve_role_instance(r, expected_instance=expected)
                resolved_pings[r] = ping
                managed = _get_managed_record(r, ping)
                managed_records[r] = managed
                if quit and (not ping.get("cli_managed") or managed is None):
                    return _stop_error_envelope(
                        code="unmanaged_instance_quit_refused",
                        message=f"{r.title()} FreeCAD window (pid={ping['process_id']}) was not launched by REAgent CLI and cannot be quit",
                        session_released=True,
                        last_stage="quit_ownership_refused",
                        retained_reason="unmanaged_instance_quit_refused",
                    )

        if "source" in resolved_pings:
            src_ping = resolved_pings["source"]
            st = ui_action("status", source_instance=src_ping)
            if not st["ok"]:
                err = st.get("error") or {}
                return _stop_error_envelope(
                    code=err.get("code") or "status_failed",
                    message=err.get("message") or "Failed to read Source status",
                    last_stage="status_failed",
                    retained_reason=err.get("message") or err.get("code"),
                    error=err,
                )
            res = st["result"] or {}
            snap = (res.get("details") or {}).get("snapshot") or {}
            ctrl = snap.get("controller")
            mode = snap.get("mode")
            if res.get("task_open") or ctrl is not None or mode in ("chat", "fixed"):
                last_stage = "closing_session"
                tid = res.get("task_id")
                if allowed_task_ids is not None and tid not in allowed_task_ids:
                    return _stop_error_envelope(
                        code="unowned_task_active",
                        message=f"Source retains active task {tid!r} not owned by this script",
                        task_id=tid,
                        last_stage="unowned_task_active",
                        retained_reason="unowned_task_active",
                    )
                is_fixed_close = mode == "fixed" and ctrl is not None
                if is_fixed_close:
                    close_env = exec_flow(
                        "agent_close_session",
                        task_id=tid,
                        timeout=timeout,
                        source_instance=src_ping,
                    )
                else:
                    close_env = ui_action(
                        "end_task",
                        task_id=tid,
                        wait="idle",
                        timeout=timeout,
                        source_instance=src_ping,
                    )
                close_err = close_env.get("error") or {}
                if isinstance(close_err, dict) and close_err.get("acceptance_unconfirmed"):
                    return _stop_error_envelope(
                        code=close_err.get("code") or "acceptance_unconfirmed",
                        message=close_err.get("message") or "Close acceptance was unconfirmed",
                        task_id=tid,
                        last_stage="disconnect_before_close_receipt",
                        retained_reason="acceptance_unconfirmed",
                        error=close_err,
                    )
                if isinstance(close_err, dict) and close_err.get("code") == "waiting_user_or_in_flight":
                    return _stop_error_envelope(
                        code="waiting_user_or_in_flight",
                        message=close_err.get("message") or "waiting_user_or_in_flight",
                        task_id=tid,
                        last_stage="waiting_user_or_in_flight",
                        retained_reason="waiting_user_or_in_flight",
                        error=close_err,
                    )
                confirmed, close_err_norm, close_val = session_close_confirmed(
                    close_env,
                    is_fixed=is_fixed_close,
                )
                abandoned_roles = list(close_val.get("abandoned_roles") or ())
                if not confirmed:
                    retained_reason = (
                        (close_err_norm.get("message") if isinstance(close_err_norm, dict) else None)
                        or "session_cleanup_or_journal_retained"
                    )
                    err_code = (
                        close_err_norm.get("code") if isinstance(close_err_norm, dict) else None
                    ) or "session_retained"
                    return _stop_error_envelope(
                        code=err_code,
                        message=retained_reason,
                        task_id=tid,
                        role_results=close_val.get("roles") or role_results,
                        abandoned_roles=abandoned_roles,
                        last_stage=last_stage,
                        retained_reason=retained_reason,
                        error=close_err_norm or {"code": err_code, "message": retained_reason},
                    )
                last_stage = "session_released"

        all_stop_accepted = True
        bridge_results: dict[str, Any] = {}
        exit_results: dict[str, bool | None] = {}
        any_quit_requested = False

        for r in roles:
            if r not in resolved_pings:
                if quit:
                    expected = source_instance if r == "source" else target_instance
                    live_pids: list[int] = []
                    if expected is not None and isinstance(expected.get("process_id"), int):
                        exp_pid = expected["process_id"]
                        exp_iid = expected.get("instance_id", "")
                        if _is_pid_alive(exp_pid):
                            live_pids = [exp_pid]
                        else:
                            popped = _pop_managed_record(r, exp_pid, exp_iid)
                            if popped is not None and _is_managed_temp_dir(popped.get("temp_dir")):
                                shutil.rmtree(popped["temp_dir"], ignore_errors=True)
                            role_results[r] = {
                                "status": "already_exited",
                                "process_id": exp_pid,
                                "stop_accepted": True,
                                "bridge_stopped": True,
                                "quit_requested": False,
                                "process_exited": True,
                            }
                            bridge_results[r] = True
                            exit_results[r] = True
                            last_stage = f"{r}_already_exited"
                            continue
                    else:
                        live_recs = _list_managed_records(role=r)
                        live_pids = sorted({
                            rec["process_id"]
                            for rec in live_recs
                            if isinstance(rec.get("process_id"), int)
                        })
                    if live_pids:
                        all_stop_accepted = False
                        retained_reason = "managed_process_alive_without_bridge"
                        last_stage = f"{r}_bridge_offline_process_alive"
                        stop_error = {
                            "code": "managed_process_alive_without_bridge",
                            "message": f"{r.title()} bridge is offline but managed FreeCAD PID(s) {live_pids} are still alive",
                        }
                        role_results[r] = {
                            "status": "bridge_offline_process_alive",
                            "process_ids": live_pids,
                            "stop_accepted": False,
                            "bridge_stopped": True,
                            "quit_requested": False,
                            "process_exited": False,
                        }
                        bridge_results[r] = True
                        exit_results[r] = False
                        break
                role_results[r] = {"status": "not_running"}
                bridge_results[r] = True
                exit_results[r] = None
                continue
            ping = resolved_pings[r]
            managed = managed_records[r]
            want_quit = bool(quit)
            if want_quit:
                any_quit_requested = True
            stop_info = stop_role_bridge(
                r,
                quit=want_quit,
                cleanup_temp_docs=bool(cleanup_temp_docs and want_quit),
                cleanup_session_ids=cleanup_session_ids if (cleanup_temp_docs and want_quit) else None,
                expected_instance=ping,
                timeout=timeout,
            )
            role_results[r] = {
                "status": "stopped" if stop_info.get("stop_accepted") else "retained",
                "stop_accepted": bool(stop_info.get("stop_accepted")),
                "bridge_stopped": stop_info.get("bridge_stopped"),
                "quit_requested": want_quit,
                "last_confirmed_stage": stop_info.get("last_confirmed_stage"),
            }
            bridge_results[r] = stop_info.get("bridge_stopped")
            if not stop_info.get("stop_accepted"):
                all_stop_accepted = False
                stop_error = stop_info.get("error")
                retained_reason = stop_info.get("last_confirmed_stage") or "stop_rejected"
                last_stage = (
                    "waiting_user_or_in_flight"
                    if stop_info.get("waiting_user_or_in_flight")
                    else retained_reason
                )
                exit_results[r] = False if want_quit else None
                break
            if stop_info.get("bridge_stopped") is False:
                stop_error = stop_info.get("error")
                retained_reason = "stop_timeout"
                last_stage = f"{r}_bridge_timeout"
                exit_results[r] = False if want_quit else None
                break
            last_stage = f"{r}_stopped"
            if want_quit:
                exited = _wait_process_exit(
                    ping["process_id"],
                    timeout,
                    proc=(managed or {}).get("popen"),
                )
                if exited:
                    exit_results[r] = True
                    popped = _pop_managed_record(r, ping["process_id"], ping["instance_id"])
                    if popped is not None and _is_managed_temp_dir(popped.get("temp_dir")):
                        shutil.rmtree(popped["temp_dir"], ignore_errors=True)
                else:
                    exit_results[r] = False
                    retained_reason = f"{r}_quit_timeout"
                    last_stage = f"{r}_quit_timeout"
                    break
            else:
                exit_results[r] = None

        bridge_summary: bool | str
        if any(val is False for val in bridge_results.values()):
            bridge_summary = False
        elif any(val == "unconfirmed" for val in bridge_results.values()):
            bridge_summary = "unconfirmed"
        elif all(val is True for val in bridge_results.values()):
            bridge_summary = True
        else:
            bridge_summary = False

        exited_values = [v for v in exit_results.values() if v is not None]
        process_exited = all(exited_values) if exited_values else None
        ok = bool(
            session_released
            and all_stop_accepted
            and (bridge_summary is not False)
            and (process_exited is not False)
        )

        payload = {
            "session_released": session_released,
            "roles": role_results,
            "abandoned_roles": abandoned_roles,
            "stop_accepted": all_stop_accepted,
            "bridge_stopped": bridge_summary,
            "quit_requested": any_quit_requested,
            "process_exited": process_exited,
            "last_confirmed_stage": last_stage,
            "retained_reason": retained_reason,
        }
        err_payload = None
        if not ok:
            err_payload = stop_error or {
                "code": retained_reason or "stop_incomplete",
                "message": f"Stop incomplete at stage {last_stage}: {retained_reason}",
            }
        return envelope(
            ok=ok,
            group="lifecycle",
            command="stop_instances",
            result=payload,
            error=err_payload,
        )
    except Exception as exc:
        return _stop_error_envelope(
            code=getattr(exc, "code", "cli_error"),
            message=str(exc) or type(exc).__name__,
            session_released=session_released,
            role_results=role_results,
            abandoned_roles=abandoned_roles,
            last_stage=last_stage,
            retained_reason=str(exc),
            error=error_payload(exc),
        )
