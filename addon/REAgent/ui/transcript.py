"""Bounded plain-text messages, with user bubbles right and assistant bubbles left."""

from collections import deque

from REAgent.ui import QtCore, QtWidgets


class Transcript(QtWidgets.QScrollArea):
    def __init__(self):
        super().__init__()
        self._rows = deque()
        self._agent = None
        self._chars = 0
        self.setObjectName("REAgentTranscript")
        self.setStyleSheet("QScrollArea#REAgentTranscript {background:white; border:1px solid #aaa; border-radius:3px;}"
                           "QWidget#REAgentMessages {background:white; color:#222;}")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        body.setObjectName("REAgentMessages")
        body.setAutoFillBackground(True)
        self.setWidget(body)
        self._layout = QtWidgets.QVBoxLayout(body)
        self._layout.setSizeConstraint(QtWidgets.QLayout.SetMinAndMaxSize)
        self._layout.addStretch(1)
        self.verticalScrollBar().rangeChanged.connect(lambda _low, high: self.verticalScrollBar().setValue(high))
        self.viewport().installEventFilter(self)

    def clear(self):
        while self._rows:
            self._remove_first()
        self._agent = None

    def _remove_first(self):
        row, bubble, label, _role = self._rows.popleft()
        self._chars -= len(label.text())
        if label is self._agent:
            self._agent = None
        self._layout.removeWidget(row)
        row.deleteLater()

    def message(self, role, text):
        row = QtWidgets.QWidget()
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(0, 3, 0, 3)
        bubble = QtWidgets.QFrame()
        content = QtWidgets.QVBoxLayout(bubble)
        bubble.setStyleSheet("QFrame {background:white; color:#222; border-radius:6px;}")
        label = QtWidgets.QLabel(str(text)[-32768:])
        label.setTextFormat(QtCore.Qt.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        label.setMinimumWidth(0)
        label.setSizePolicy(QtWidgets.QSizePolicy.Preferred if role == "user" else QtWidgets.QSizePolicy.Ignored,
                            QtWidgets.QSizePolicy.Preferred)
        content.addWidget(label)
        if role == "user":
            bubble.setStyleSheet("QFrame {background:#e3edff; color:#172b4d; border-radius:8px;}")
            line.addStretch(1)
            line.addWidget(bubble)
        else:
            if role == "status":
                bubble.setStyleSheet("QFrame {background:#f0f0f0; color:#444; border-radius:6px;}")
            line.addWidget(bubble, 1)
        self._layout.insertWidget(self._layout.count() - 1, row)
        self._rows.append((row, bubble, label, role))
        self._chars += len(label.text())
        self._limit()
        self._widths()
        return label

    def begin_reply(self):
        self._agent = None

    def append(self, text):
        if not text:
            return
        if self._agent is None:
            self._agent = self.message("agent", text)
        else:
            previous = self._agent.text()
            value = (previous + str(text))[-32768:]
            self._chars += len(value) - len(previous)
            self._agent.setText(value)
            self._limit()
            self._widths()

    def _limit(self):
        while len(self._rows) > 100 or self._chars > 131072:
            self._remove_first()

    def _widths(self):
        outer = self._layout.contentsMargins()
        width = max(0, self.viewport().width() - outer.left() - outer.right())
        for row, bubble, _label, role in self._rows:
            margins = row.layout().contentsMargins()
            available = max(0, width - margins.left() - margins.right())
            bubble.setMinimumWidth(0 if role == "user" else available)
            bubble.setMaximumWidth(available)
            bubble.updateGeometry()

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() == QtCore.QEvent.Resize:
            self._widths()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_layout"):
            self._widths()
