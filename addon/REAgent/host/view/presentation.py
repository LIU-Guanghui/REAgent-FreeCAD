"""Explicit object presentation and visible FreeCAD window facts."""

import FreeCAD as App
import FreeCADGui as Gui


def _style_source_document(source):
    if not getattr(App, "GuiUp", False):
        return
    try:
        source.ViewObject.ShapeColor = (0.75, 0.80, 0.90)
        source.ViewObject.LineColor = (0.20, 0.25, 0.35)
        source.ViewObject.Transparency = 0
        source.ViewObject.Selectable = False
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
    except Exception:
        pass


_PRESENTATION_PROPERTIES = (
    "Visibility",
    "DisplayMode",
    "DisplayModeBody",
    "Transparency",
)


def _presentation_snapshot(owned_objects):
    return {
        obj.Name: {
            key: getattr(obj.ViewObject, key)
            for key in _PRESENTATION_PROPERTIES
            if hasattr(obj.ViewObject, key)
        }
        for obj in owned_objects
        if hasattr(obj, "ViewObject")
    }


def _restore_presentation(owned_objects, snapshot):
    objects = {obj.Name: obj for obj in owned_objects
               if hasattr(obj, "ViewObject")}
    # Container mode first, ordinary style second, visibility parent first last.
    for key in ("DisplayModeBody", "DisplayMode", "Transparency"):
        for name, expected in snapshot.items():
            obj = objects.get(name)
            if obj is not None and key in expected:
                if getattr(obj.ViewObject, key) != expected[key]:
                    setattr(obj.ViewObject, key, expected[key])
    for name, expected in snapshot.items():
        obj = objects.get(name)
        if obj is not None and "Visibility" in expected:
            if obj.ViewObject.Visibility != expected["Visibility"]:
                obj.ViewObject.Visibility = expected["Visibility"]
    actual = _presentation_snapshot(owned_objects)
    if actual != snapshot:
        raise RuntimeError("Native presentation restoration differs from snapshot")


def _set_body_display_mode(view, expected="Through"):
    if view is None or not hasattr(view, "DisplayModeBody"):
        raise ValueError("Result Body does not expose DisplayModeBody")
    if str(view.DisplayModeBody) != expected:
        view.DisplayModeBody = expected
    actual = str(view.DisplayModeBody)
    if actual != expected:
        raise RuntimeError(
            "Result Body did not enter %s mode; actual=%s" % (expected, actual)
        )
    return actual


def _present_result(owned_objects, plan):
    """Show the native Body-through-Tip chain without moving the camera."""
    _set_body_display_mode(plan["body"], plan["body_display_mode"])
    views = list(plan["render"])
    visible = {view.Object.Name for view in views}
    for obj in owned_objects:
        if hasattr(obj, "ViewObject"):
            expected = obj.Name in visible
            if bool(obj.ViewObject.Visibility) != expected:
                obj.ViewObject.Visibility = expected
    actual = _presentation_snapshot(owned_objects)
    if any(bool(value.get("Visibility")) != (name in visible)
           for name, value in actual.items()):
        raise RuntimeError("Native Result display chain could not be established")
    if str(plan["body"].DisplayModeBody) != plan["body_display_mode"]:
        raise RuntimeError("Native Result Body display mode changed unexpectedly")


def _main_window_state():
    """Return evidence for the real Qt main window in this FreeCAD process."""

    state = {"gui_available": False, "main_window_available": False,
             "handle": None, "visible": False, "minimized": False,
             "maximized": False, "active": False, "title": None, "geometry": None}
    if not getattr(App, "GuiUp", False):
        return state
    state["gui_available"] = True
    try:
        window = Gui.getMainWindow()
    except Exception:
        window = None
    if window is None:
        return state
    try:
        handle = int(window.effectiveWinId()) or None
    except Exception:
        handle = None
    try:
        frame = window.frameGeometry()
        geometry = {
            "x": int(frame.x()),
            "y": int(frame.y()),
            "width": int(frame.width()),
            "height": int(frame.height()),
        }
    except Exception:
        geometry = None
    try:
        title = str(window.windowTitle())
    except Exception:
        title = None
    state.update({
        "main_window_available": True,
        "handle": handle,
        "visible": bool(window.isVisible()),
        "minimized": bool(window.isMinimized()),
        "maximized": bool(window.isMaximized()),
        "active": bool(window.isActiveWindow()),
        "title": title,
        "geometry": geometry,
    })
    return state
