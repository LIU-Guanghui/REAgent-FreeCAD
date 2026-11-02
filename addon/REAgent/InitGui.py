"""FreeCAD GUI entry point for the embedded STEP reconstruction agent."""

import os
import sys

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui

try:
    ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # FreeCAD executes InitGui.py and does not always define __file__.
    import inspect

    ADDON_DIR = os.path.dirname(
        os.path.abspath(inspect.getfile(inspect.currentframe()))
    )
ADDON_PARENT = os.path.dirname(ADDON_DIR)
if ADDON_PARENT not in sys.path:
    sys.path.insert(0, ADDON_PARENT)


class _RoleController:
    """Own callback dependencies that FreeCAD must retain after loading InitGui.py."""

    def __init__(self, app, gui, qt_core, qt_widgets):
        self._app = app
        self._gui = gui
        self._qt_core = qt_core
        self._qt_widgets = qt_widgets
        self._role_label = None

    def _set_role_label(self, role=None):
        window = self._gui.getMainWindow()
        if self._role_label is None:
            self._role_label = self._qt_widgets.QLabel(window)
            self._role_label.setMinimumWidth(230)
            self._role_label.setAlignment(self._qt_core.Qt.AlignCenter)
            window.statusBar().addPermanentWidget(self._role_label)
        if role == "source":
            self._role_label.setText("REAgent: SOURCE · 127.0.0.1:9876")
            self._role_label.setStyleSheet("font-weight: bold; color: #2563eb;")
            self._role_label.show()
        elif role == "rebuild":
            self._role_label.setText("REAgent: TARGET · 127.0.0.1:9877")
            self._role_label.setStyleSheet("font-weight: bold; color: #059669;")
            self._role_label.show()
        else:
            self._role_label.hide()

    def select_role(self, role):
        from REAgent.bootstrap import bridge

        try:
            message = bridge.select_role(role)
        except Exception as exc:
            self._app.Console.PrintError(
                "[REAgent] role selection failed: %s\n" % exc
            )
            self._qt_widgets.QMessageBox.critical(
                self._gui.getMainWindow(),
                "REAgent",
                "Cannot select this window as %s:\n%s"
                % ("Source" if role == "source" else "Target", exc),
            )
            return
        self._set_role_label(role)
        self._app.Console.PrintMessage("[REAgent] %s\n" % message)
        if role == "source":
            self.open_panel()

    def open_panel(self):
        try:
            from REAgent.bootstrap import source_window_controller

            source_window_controller(self._gui.getMainWindow(), self._stop_now).show()
        except Exception as exc:
            self._app.Console.PrintError("[REAgent] Agent panel unavailable: %s\n" % exc)
            self._qt_widgets.QMessageBox.warning(
                self._gui.getMainWindow(), "REAgent", "Agent panel unavailable; check the complete Addon installation.\n%s" % exc)

    def stop(self):
        from REAgent.bootstrap import current_source_window_controller

        manager = current_source_window_controller()
        if manager is not None:
            manager.request_close("release")
            return
        self._stop_now()

    def _stop_now(self):
        from REAgent.bootstrap import bridge

        try:
            message = bridge.stop_bridge()
        except Exception as exc:
            self._app.Console.PrintError("[REAgent] role release refused: %s\n" % exc)
            self._qt_widgets.QMessageBox.warning(self._gui.getMainWindow(), "REAgent", str(exc))
            return False
        self._set_role_label(None)
        self._app.Console.PrintMessage("[REAgent] %s\n" % message)
        return True


class SourceBridgeCommand:
    def __init__(self, controller):
        self._controller = controller

    def GetResources(self):
        return {
            "MenuText": "Source",
            "ToolTip": "Use this window as the read-only Source, or reopen its task panel",
        }

    def IsActive(self):
        from REAgent.bootstrap import bridge

        return not bridge.bridge_is_running() or bridge.selected_role() == "source"

    def Activated(self):
        from REAgent.bootstrap import bridge

        if bridge.selected_role() == "source":
            self._controller.open_panel()
        else:
            self._controller.select_role("source")


class TargetBridgeCommand:
    def __init__(self, controller):
        self._controller = controller

    def GetResources(self):
        return {
            "MenuText": "Target",
            "ToolTip": "Use this visible FreeCAD window as the editable Target",
        }

    def IsActive(self):
        from REAgent.bootstrap import bridge

        return not bridge.bridge_is_running()

    def Activated(self):
        self._controller.select_role("rebuild")


class StopBridgeCommand:
    def __init__(self, controller):
        self._controller = controller

    def GetResources(self):
        return {
            "MenuText": "Stop",
            "ToolTip": "Release the Source or Target role from this window",
        }

    def IsActive(self):
        from REAgent.bootstrap import bridge

        return bridge.bridge_is_running()

    def Activated(self):
        self._controller.stop()


_controller = _RoleController(App, Gui, QtCore, QtWidgets)
Gui.addCommand("REAgentSource", SourceBridgeCommand(_controller))
Gui.addCommand("REAgentTarget", TargetBridgeCommand(_controller))
Gui.addCommand("REAgentStop", StopBridgeCommand(_controller))


class REAgentWorkbench(Gui.Workbench):
    MenuText = "REAgent"
    ToolTip = "Embedded model and native CAD loop for editable STEP reconstruction"

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


Gui.addWorkbench(REAgentWorkbench())
