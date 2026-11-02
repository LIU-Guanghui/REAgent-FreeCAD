"""GUI-only native profile storage; callers pass detached text and expected state."""

import FreeCAD as App
from PySide import QtCore


_PREFERENCES = "User parameter:BaseApp/Preferences/Mod/REAgent/Model"


def _preferences():
    application = QtCore.QCoreApplication.instance()
    if application is None or QtCore.QThread.currentThread() != application.thread():
        raise RuntimeError("Model settings storage must be called on the GUI thread")
    return App.ParamGet(_PREFERENCES)


def read_profiles():
    return _preferences().GetString("profiles", "")


def save_profiles(expected, value):
    preferences = _preferences()
    previous = preferences.GetString("profiles", "")
    if previous != expected:
        raise ValueError("Saved profiles changed while this form was open. Cancel and reopen Settings before saving.")
    try:
        preferences.SetString("profiles", value)
        App.saveParameter()
    except RuntimeError:
        preferences.SetString("profiles", previous)
        raise
