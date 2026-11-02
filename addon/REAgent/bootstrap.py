"""The sole composition root for host lifecycle, Source UI and pure sessions."""

from .host.bridge import BridgeLifecycle


_source_window = None


def _application_release(role):
    if role != "source":
        return
    from .rebuild.controller import has_retained_task
    controller = getattr(_source_window, "_reagent_source_controller", None)
    if controller is None:
        raise RuntimeError("Source application release check is not ready; reopen the Source panel")
    if not controller.can_release() or has_retained_task():
        raise RuntimeError("Wait for the original worker and retained task to finish before releasing Source")


bridge = BridgeLifecycle(application_guard=_application_release)


def source_window_controller(window, release_role):
    global _source_window
    if bridge.selected_role() != "source":
        raise RuntimeError("Open the task panel in the explicitly bound Source window")
    from .host.settings import read_profiles, save_profiles
    from .ui.source_window import SourceWindowController
    controller = getattr(window, "_reagent_source_controller", None)
    if controller is None:
        controller = SourceWindowController(window, release_role, read_settings=read_profiles, save_settings=save_profiles)
        window._reagent_source_controller = controller
    _source_window = window
    return controller


def current_source_window_controller():
    return getattr(_source_window, "_reagent_source_controller", None)
