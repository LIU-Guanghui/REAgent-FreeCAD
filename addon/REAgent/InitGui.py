"""FreeCAD GUI entry point for the embedded CAD agent."""

import FreeCADGui as Gui
from PySide import QtWidgets


class _RoleController:
    """Hold the shared role state and GUI dependencies for FreeCAD callbacks."""

    def __init__(self, gui, qt_widgets):
        self._gui = gui
        self._qt_widgets = qt_widgets
        self._bridge = None
        self._role_label = None

    def selected_role(self):
        return self._bridge.role if self._bridge is not None and self._bridge.running else None

    def select_role(self, role):
        bridge = self._bridge
        if self.selected_role() != role:
            from REAgent.host.process_state import assert_role_release
            from REAgent.host.server import BridgeServer

            try:
                assert_role_release()
                bridge = BridgeServer(role, assert_release=self._assert_release)
                bridge.start()
            except (OSError, RuntimeError) as exc:
                self._qt_widgets.QMessageBox.critical(
                    self._gui.getMainWindow(), "REAgent",
                    f"Cannot select this window as {role.title()}:\n{exc}",
                )
                return False
            self._bridge = bridge

        if self._role_label is None:
            window = self._gui.getMainWindow()
            self._role_label = self._qt_widgets.QLabel(window)
            window.statusBar().addPermanentWidget(self._role_label)
            
        color = "#2563eb" if bridge.role == "source" else "#059669"
        self._role_label.setText(f"REAgent: {bridge.role.upper()} · {bridge.host}:{bridge.port}")
        self._role_label.setStyleSheet(f"font-weight: bold; color: {color};")
        self._role_label.show()
        return True

    def open_panel(self):
        window = self._gui.getMainWindow()
        manager = self._source_controller(window)
        if manager is None:
            from REAgent.host.settings import read_profiles, save_profiles
            from REAgent.ui.source_window import SourceWindowController

            manager = SourceWindowController(
                window, self._stop_now, read_settings=read_profiles, save_settings=save_profiles
            )
            window._reagent_source_controller = manager
        manager.show()

    def stop(self):
        window = self._gui.getMainWindow()
        manager = self._source_controller(window)
        if manager is not None:
            manager.request_close("release")
            return
        try:
            self._stop_now()
        except RuntimeError as exc:
            self._qt_widgets.QMessageBox.warning(window, "REAgent", str(exc))

    def _stop_now(self):
        if self._bridge is not None:
            self._bridge.stop()
            self._bridge = None
        if self._role_label is not None:
            self._role_label.hide()

    @staticmethod
    def _source_controller(window):
        return getattr(window, "_reagent_source_controller", None)

    def _assert_release(self):
        from REAgent.host.process_state import assert_role_release

        assert_role_release()
        if self.selected_role() != "source":
            return
        manager = self._source_controller(self._gui.getMainWindow())
        if manager is not None:
            manager.assert_release()


class SourceBridgeCommand:
    def __init__(self, controller):
        self._controller = controller

    def GetResources(self):
        return {
            "MenuText": "Source",
            "ToolTip": "Use this window as the read-only Source, or reopen its task panel",
        }

    def IsActive(self):
        return self._controller.selected_role() in (None, "source")

    def Activated(self):
        if self._controller.selected_role() == "source" or self._controller.select_role("source"):
            self._controller.open_panel()


class TargetBridgeCommand:
    def __init__(self, controller):
        self._controller = controller

    def GetResources(self):
        return {
            "MenuText": "Target",
            "ToolTip": "Use this visible FreeCAD window as the editable Target",
        }

    def IsActive(self):
        return self._controller.selected_role() is None

    def Activated(self):
        self._controller.select_role("target")


class StopBridgeCommand:
    def __init__(self, controller):
        self._controller = controller

    def GetResources(self):
        return {
            "MenuText": "Stop",
            "ToolTip": "Release the Source or Target role from this window",
        }

    def IsActive(self):
        return self._controller.selected_role() is not None

    def Activated(self):
        self._controller.stop()


class REAgentWorkbench(Gui.Workbench):
    MenuText = "REAgent"
    ToolTip = "Build editable FreeCAD parts from STP, STL, DXF drawings, images or text"

    def Initialize(self):
        commands = [
            "REAgentSource",
            "REAgentTarget",
            "REAgentStop",
        ]
        self.appendToolbar("REAgent", commands)
        self.appendMenu("REAgent", commands)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


_controller = _RoleController(Gui, QtWidgets)
Gui.addCommand("REAgentSource", SourceBridgeCommand(_controller))
Gui.addCommand("REAgentTarget", TargetBridgeCommand(_controller))
Gui.addCommand("REAgentStop", StopBridgeCommand(_controller))
Gui.addWorkbench(REAgentWorkbench())
