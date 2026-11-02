"""Host Bridge lifecycle with an injected application release check."""

from REAgent.protocol.definitions import ROLE_PORTS
from .process_state import _assert_role_release
from .server import BridgeServer


class BridgeLifecycle:
    def __init__(self, *, application_guard):
        if not callable(application_guard):
            raise ValueError("An application release guard is required")
        self.application_guard = application_guard
        self.bridge = None

    def assert_release(self):
        _assert_role_release()
        self.application_guard(self.selected_role())

    def select_role(self, role):
        if role not in ROLE_PORTS:
            raise ValueError("role must be source or rebuild")
        if self.bridge is not None and self.bridge.running:
            if self.bridge.role == role:
                return "This FreeCAD window is already %s on port %d." % (role, self.bridge.port)
            raise RuntimeError("Stop the current Bridge before assigning this window another role")
        self.assert_release()
        candidate = BridgeServer(role=role, assert_release=self.assert_release)
        message = candidate.start()
        self.bridge = candidate
        return message

    def stop_bridge(self):
        self.assert_release()
        if self.bridge is None:
            return "Bridge is not running."
        result = self.bridge.stop()
        self.bridge = None
        return result

    def bridge_is_running(self):
        return self.bridge is not None and self.bridge.running

    def selected_role(self):
        return self.bridge.role if self.bridge_is_running() else None
