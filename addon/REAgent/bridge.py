"""FreeCAD add-on entry point for the local structured Bridge."""

from . import BridgeServer as _BridgeServer, ROLE_PORTS as _ROLE_PORTS

_bridge = None


def select_role(role):
    """Bind this visible FreeCAD window to one explicit Bridge role."""

    global _bridge
    if role not in _ROLE_PORTS:
        raise ValueError("role must be source or rebuild")
    if _bridge is not None and _bridge.running:
        if _bridge.role == role:
            return "This FreeCAD window is already %s on port %d." % (
                role,
                _bridge.port,
            )
        raise RuntimeError(
            "Stop the current Bridge before assigning this window another role"
        )
    candidate = _BridgeServer(role=role)
    try:
        message = candidate.start()
    except Exception:
        _bridge = None
        raise
    _bridge = candidate
    return message


def select_source():
    return select_role("source")


def select_target():
    return select_role("rebuild")


def stop_bridge():
    global _bridge
    if _bridge is None:
        return "Bridge is not running."
    result = _bridge.stop()
    _bridge = None
    return result


def bridge_is_running():
    return _bridge is not None and _bridge.running


def selected_role():
    return _bridge.role if bridge_is_running() else None


__all__ = [
    "bridge_is_running",
    "select_role",
    "select_source",
    "select_target",
    "selected_role",
    "stop_bridge",
]
