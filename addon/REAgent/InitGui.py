"""FreeCAD GUI entry point for the embedded CAD agent."""

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets


class _RoleController:
    """Hold the shared role state and GUI dependencies for FreeCAD callbacks."""

    def __init__(self, app, gui, qt_core, qt_widgets, os_mod):
        self._app = app
        self._gui = gui
        self._qt_core = qt_core
        self._qt_widgets = qt_widgets
        self._os = os_mod
        self._bridge = None
        self._role_label = None
        self._bootstrap_timer = None
        self._bootstrap_attempts = 0
        self._cli_managed = self._os.environ.get("REAGENT_CLI_MANAGED") == "1"
        self._user_cfg_source = self._os.environ.get("REAGENT_USER_CFG_SOURCE", "").strip() or None
        self._stop_scheduled = False
        raw_timeout = self._os.environ.get("REAGENT_BOOTSTRAP_TIMEOUT", "").strip()
        try:
            bootstrap_timeout = float(raw_timeout) if raw_timeout else 30.0
        except ValueError:
            bootstrap_timeout = 30.0
        bootstrap_timeout = min(max(bootstrap_timeout, 30.0), 300.0)
        self._bootstrap_max_attempts = max(300, int(bootstrap_timeout * 10))

    def selected_role(self):
        return self._bridge.role if self._bridge is not None and self._bridge.running else None

    def start_bootstrap(self):
        role = self._os.environ.get("REAGENT_BOOTSTRAP_ROLE", "").strip().lower()
        if role not in ("source", "target") or self._bootstrap_timer is not None:
            return
        mw = self._gui.getMainWindow()
        timer = self._qt_core.QTimer(mw) if mw is not None else self._qt_core.QTimer()
        timer.setInterval(100)
        timer.timeout.connect(lambda: self._try_bootstrap(role))
        self._bootstrap_timer = timer
        timer.start()

    def _try_bootstrap(self, role):
        self._bootstrap_attempts += 1
        window = self._gui.getMainWindow()
        ready = (
            window is not None
            and window.isVisible()
            and "REAgentWorkbench" in self._gui.listWorkbenches()
        )
        if not ready:
            if self._bootstrap_attempts >= self._bootstrap_max_attempts and self._bootstrap_timer is not None:
                self._bootstrap_timer.stop()
                self._bootstrap_timer = None
                self._app.Console.PrintError("[REAgent] Timed out waiting for FreeCAD main window during role bootstrap.\n")
            return
        if self._bootstrap_timer is not None:
            self._bootstrap_timer.stop()
            self._bootstrap_timer = None
        if self.select_role(role, interactive=False) and role == "source":
            self.open_panel()

    def _controls_for_role(self, role):
        controls = {"cli_stop_role": lambda params: self._cli_stop_role(role, params)}
        if role == "source":
            controls.update({
                "cli_ui_action": lambda params: self._require_source_manager().cli_ui_action(params),
                "cli_ui_wait": lambda params: self._require_source_manager().cli_ui_wait(params),
                "cli_exec_command": lambda params: self._require_source_manager().cli_exec_command(params),
            })
        return controls

    def _require_source_manager(self):
        if self.selected_role() != "source":
            raise RuntimeError("Window is not bound as source")
        window = self._gui.getMainWindow()
        manager = self._source_controller(window)
        if manager is None:
            self.open_panel()
            manager = self._source_controller(window)
        if manager is None:
            raise RuntimeError("Source task panel is unavailable")
        return manager

    def select_role(self, role, *, interactive=True):
        if not interactive:
            workbench = self._gui.activeWorkbench()
            target_wb = self._gui.listWorkbenches().get("REAgentWorkbench")
            if workbench is None or workbench is not target_wb:
                self._gui.activateWorkbench("REAgentWorkbench")
        bridge = self._bridge
        if self.selected_role() != role:
            from REAgent.host.process_state import assert_role_release
            from REAgent.host.server import BridgeServer

            try:
                assert_role_release()
                bridge = BridgeServer(
                    role,
                    assert_release=self._assert_release,
                    controls=self._controls_for_role(role),
                    ping_extra={
                        "cli_managed": self._cli_managed,
                        "user_cfg_source": self._user_cfg_source,
                    },
                )
                bridge.start()
            except (OSError, RuntimeError) as exc:
                if interactive:
                    self._qt_widgets.QMessageBox.critical(
                        self._gui.getMainWindow(), "REAgent",
                        f"Cannot select this window as {role.title()}:\n{exc}",
                    )
                else:
                    self._app.Console.PrintError(f"[REAgent] Cannot bind {role}: {exc}\n")
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

    def _clear_stop_state(self):
        self._stop_scheduled = False
        if self._bridge is not None:
            self._bridge.set_stopping(False)

    def _cleanup_temp_docs(self, cleanup_temp_docs, cleanup_session_ids=None):
        if cleanup_temp_docs and self._cli_managed:
            from REAgent.host.process_state import cleanup_unmodified_temp_docs
            cleanup_unmodified_temp_docs(session_ids=cleanup_session_ids, gui=self._gui)

    def _cli_stop_role(self, expected_role, params):
        if self.selected_role() != expected_role:
            raise RuntimeError(f"Window is not bound as {expected_role}")
        if self._stop_scheduled:
            raise RuntimeError("Role stop is already in progress")
        quit_after = bool(params.get("quit", False))
        cleanup_temp_docs = bool(params.get("cleanup_temp_docs", False))
        raw_session_ids = params.get("cleanup_session_ids")
        cleanup_session_ids = None
        if raw_session_ids is not None:
            if (not isinstance(raw_session_ids, list)
                    or not all(isinstance(item, str) and item for item in raw_session_ids)):
                raise ValueError("cleanup_session_ids must be a list of non-empty strings")
            cleanup_session_ids = set(raw_session_ids)
        if quit_after and not self._cli_managed:
            raise RuntimeError("Cannot quit a FreeCAD window that was not launched by REAgent CLI")
        window = self._gui.getMainWindow()
        manager = self._source_controller(window) if expected_role == "source" else None
        if self._bridge is not None:
            self._bridge.set_stopping(True)
        if manager is not None:
            try:
                result = manager.cli_stop_role(
                    quit=quit_after,
                    before_native_close=(
                        (lambda: self._cleanup_temp_docs(cleanup_temp_docs, cleanup_session_ids))
                        if quit_after
                        else None
                    ),
                    on_stop_aborted=self._clear_stop_state,
                )
            except Exception:
                self._clear_stop_state()
                raise
            if not isinstance(result, dict) or not result.get("stop_accepted"):
                self._clear_stop_state()
            return result
        from REAgent.host.process_state import assert_role_release
        try:
            assert_role_release()
        except Exception:
            self._clear_stop_state()
            raise
        self._stop_scheduled = True

        def finish():
            try:
                if quit_after:
                    assert_role_release()
                    self._cleanup_temp_docs(cleanup_temp_docs, cleanup_session_ids)
                    accepted = bool(window.close()) if window is not None else True
                    if accepted:
                        self._stop_now()
                        self._stop_scheduled = False
                    else:
                        self._clear_stop_state()
                else:
                    self._stop_now()
                    self._stop_scheduled = False
            except Exception as exc:
                self._app.Console.PrintError(f"[REAgent] cli_stop_role stop failed: {exc}\n")
                self._clear_stop_state()

        self._qt_core.QTimer.singleShot(0, finish)
        return {
            "stop_accepted": True,
            "role": expected_role,
            "quit_requested": quit_after,
            "last_confirmed_stage": "accepted",
        }

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
            try:
                self._role_label.hide()
            except RuntimeError:
                pass

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


_controller = _RoleController(App, Gui, QtCore, QtWidgets, os)
Gui.addCommand("REAgentSource", SourceBridgeCommand(_controller))
Gui.addCommand("REAgentTarget", TargetBridgeCommand(_controller))
Gui.addCommand("REAgentStop", StopBridgeCommand(_controller))
Gui.addWorkbench(REAgentWorkbench())
_controller.start_bootstrap()
