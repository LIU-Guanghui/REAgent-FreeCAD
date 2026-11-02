"""Qt bindings for Source UI components; importing starts no task or thread."""

from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui
