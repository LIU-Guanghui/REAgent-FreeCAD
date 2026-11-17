"""Image, status and Journal windows; SourceWindowController owns their lifecycle."""

import json

from REAgent.ui import QtCore, QtGui, QtWidgets
from REAgent.ui.input_images import read_input
from REAgent.ui.panel import plain_label
from REAgent.application.presentation import read_preview_image

class ImageView(QtWidgets.QGraphicsView):
    zoom_changed = QtCore.Signal(float)
    MIN_ZOOM = 0.1
    MAX_ZOOM = 16.0

    def __init__(self):
        super().__init__()
        self._fit = False
        scene = QtWidgets.QGraphicsScene(self)
        self.setScene(scene)
        self._item = scene.addPixmap(QtGui.QPixmap())
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)

    def set_image(self, pixmap):
        self._fit = False
        self.resetTransform()
        self._item.setPixmap(pixmap)
        self.scene().setSceneRect(QtCore.QRectF(pixmap.rect()))
        loaded = not pixmap.isNull()
        self.setEnabled(loaded)
        if loaded:
            self.fit_image()
        return loaded

    def fit_image(self):
        if self._item.pixmap().isNull():
            return
        self._fit = True
        self.resetTransform()
        self.fitInView(self._item, QtCore.Qt.KeepAspectRatio)
        self.zoom_changed.emit(self.transform().m11())

    def set_zoom(self, value):
        if self._item.pixmap().isNull():
            return
        self._fit = False
        value = max(self.MIN_ZOOM, min(self.MAX_ZOOM, value))
        factor = value / self.transform().m11()
        self.scale(factor, factor)
        self.zoom_changed.emit(value)

    def zoom_by(self, factor):
        current = self.transform().m11()
        if factor < 1 and current <= self.MIN_ZOOM:
            return
        self.set_zoom(current * factor)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta and not self._item.pixmap().isNull():
            self.zoom_by(1.25 if delta > 0 else 0.8)
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_fit", False):
            self.fit_image()

class ImageViewer(QtWidgets.QDialog):
    """Nonmodal task images; load only the selected file."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Images")
        self._entries = []
        layout = QtWidgets.QVBoxLayout(self)
        self.select = QtWidgets.QComboBox()
        layout.addWidget(self.select)
        self.identity = plain_label()
        layout.addWidget(self.identity)
        self.image = ImageView()
        controls = QtWidgets.QHBoxLayout()
        self.previous = QtWidgets.QPushButton("Previous")
        self.next = QtWidgets.QPushButton("Next")
        self.previous.clicked.connect(lambda: self.select.setCurrentIndex(self.select.currentIndex() - 1))
        self.next.clicked.connect(lambda: self.select.setCurrentIndex(self.select.currentIndex() + 1))
        controls.addWidget(self.previous)
        controls.addWidget(self.next)
        for label, callback in (("−", lambda: self.image.zoom_by(0.8)), ("+", lambda: self.image.zoom_by(1.25)),
                                ("100%", lambda: self.image.set_zoom(1.0)), ("Fit", self.image.fit_image)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        self.zoom = plain_label()
        self.image.zoom_changed.connect(lambda value: self.zoom.setText(str(round(value * 100)) + "%"))
        controls.addWidget(self.zoom)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addWidget(self.image, 1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        layout.addWidget(close)
        self.select.currentIndexChanged.connect(self._display)
        self.resize(900, 680)

    def update_entries(self, entries):
        if [entry["image_id"] for entry in entries] == [entry["image_id"] for entry in self._entries]:
            return
        previous = self.select.currentIndex()
        selected_id = self._entries[previous]["image_id"] if 0 <= previous < len(self._entries) else None
        selected = next((index for index, entry in enumerate(entries) if entry["image_id"] == selected_id), -1)
        self._entries = list(entries)
        self.select.blockSignals(True)
        self.select.clear()
        for number, entry in enumerate(entries, 1):
            self.select.addItem("%d · %s" % (number, entry["label"]))
        self.select.setCurrentIndex(max(0, selected))
        self.select.blockSignals(False)
        if selected < 0:
            self._display(0)
        else:
            self.previous.setEnabled(selected > 0)
            self.next.setEnabled(selected + 1 < len(entries))

    def _display(self, index):
        if not 0 <= index < len(self._entries):
            return
        entry = self._entries[index]
        self.previous.setEnabled(index > 0)
        self.next.setEnabled(index + 1 < len(self._entries))
        reference = entry.get("input")
        try:
            if reference is not None:
                pixmap = QtGui.QPixmap.fromImage(read_input(reference))
            else:
                pixmap = QtGui.QPixmap()
                pixmap.loadFromData(read_preview_image(entry), "PNG")
            if not self.image.set_image(pixmap):
                raise ValueError("Image decoding failed")
        except (OSError, KeyError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            self.image.set_image(QtGui.QPixmap())
            self.zoom.setText("—")
            self.identity.setText("Image unavailable: " + str(exc)[:400])
            return
        if reference is not None:
            self.identity.setText("Source image %s · %s" % (reference["index"], reference["filename"]))
            self.identity.setToolTip(reference["path"])
            return
        artifact = entry["artifact"]
        self.identity.setText("Capture %s · %s · historical revision %s" % (
            artifact["capture_number"], {"source": "Source", "target": "Target", "paired": "Source / Target"}[entry["role"]], str(artifact["revision"])[:100]))
        self.identity.setToolTip(entry["image_id"] + "\n" + str(artifact["path"]))

class TaskViewer(QtWidgets.QDialog):
    """Bounded presentation only; SourceWindowController owns lifecycle and action guards."""

    def __init__(self, parent, kind):
        super().__init__(parent)
        self.setWindowTitle({"status": "Status", "journal": "Journal", "comparison": "Comparison"}[kind])
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.addWidget(self.summary)
        self.technical = QtWidgets.QPlainTextEdit()
        self.technical.setReadOnly(True)
        self.technical.setMaximumHeight(260)
        toggle = QtWidgets.QCheckBox("Technical details")
        toggle.toggled.connect(self.technical.setVisible)
        layout.addWidget(toggle)
        layout.addWidget(self.technical)
        self.technical.hide()
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.resize(700, 520)

    def update_content(self, summary, details):
        for edit, value in ((self.summary, summary), (self.technical, json.dumps(details, ensure_ascii=False, indent=2))):
            text = value[:131072]
            if len(value) > 131072:
                suffix = "\n[Display truncated; task data is retained.]"
                text = value[:131072 - len(suffix)] + suffix
            if edit.toPlainText() != text:
                edit.setPlainText(text)
