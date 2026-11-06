"""Bounded, selectable conversation text and compact bubbles."""

from dataclasses import dataclass

from REAgent.ui import QtCore, QtGui, QtWidgets


@dataclass
class Message:
    row: object
    editor: object
    bubble: object
    role: str
    text: str
    request: tuple | None


class _MessageEditor(QtWidgets.QTextEdit):
    def wheelEvent(self, event):
        # Let Qt propagate scrolling to the transcript's viewport.
        event.ignore()


class Transcript(QtWidgets.QScrollArea):
    def __init__(self):
        super().__init__()
        self._rows = []
        self._chars = 0
        self.setObjectName("REAgentTranscript")
        self.setStyleSheet("QScrollArea#REAgentTranscript {background:white; border:1px solid #aaa; border-radius:3px;}"
                           "QWidget#REAgentMessages {background:white; color:#222;}"
                           "QFrame#REAgentMessageRow {border:none; border-bottom:1px solid #e8e8e8;}")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        body.setObjectName("REAgentMessages")
        body.setAutoFillBackground(True)
        self.setWidget(body)
        self._layout = QtWidgets.QVBoxLayout(body)
        self._layout.setSpacing(4)
        self._layout.addStretch(1)
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(lambda _low, high: bar.setValue(high))
        self.viewport().installEventFilter(self)

    def clear(self):
        for message in self._rows[:]:
            self._remove(message)

    def _remove(self, message):
        self._rows.remove(message)
        self._chars -= len(message.text)
        self._layout.removeWidget(message.row)
        message.row.hide()
        message.row.deleteLater()

    def discard_previews(self, *, request=None):
        for message in self._rows[:]:
            if message.request is not None and (request is None or message.request == request):
                self._remove(message)

    def message(self, role, text, *, request=None, complete=False):
        message = next((row for row in self._rows if row.request == request), None) if request else None
        if message is None:
            row = QtWidgets.QFrame()
            row.setObjectName("REAgentMessageRow")
            line = QtWidgets.QHBoxLayout(row)
            line.setContentsMargins(0, 8, 0, 8)
            editor = _MessageEditor()
            editor.setFrameShape(QtWidgets.QFrame.NoFrame)
            editor.setReadOnly(True)
            editor.setFocusPolicy(QtCore.Qt.ClickFocus)
            editor.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
            editor.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
            editor.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            editor.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            editor.setStyleSheet("QTextEdit {background:transparent; color:#222; border:none; border-radius:0; padding:0;}")
            editor.document().setDocumentMargin(0)
            editor.viewport().installEventFilter(self)
            editor.installEventFilter(self)
            bubble = None
            if role in ("user", "error"):
                bubble = QtWidgets.QFrame()
                bubble.setObjectName("REAgentMessageBubble")
                background = "#e3edff" if role == "user" else "#f8eae7"
                bubble.setStyleSheet("QFrame#REAgentMessageBubble {background:" + background
                                     + "; border:none; border-radius:8px;}")
                content = QtWidgets.QVBoxLayout(bubble)
                content.setContentsMargins(9, 7, 9, 7)
                content.addWidget(editor)
                alignment = QtCore.Qt.AlignRight if role == "user" else QtCore.Qt.AlignLeft
                line.addWidget(bubble, 0, alignment)
            else:
                line.addWidget(editor)
            self._layout.insertWidget(self._layout.count() - 1, row)
            message = Message(row, editor, bubble, role, "", request)
            self._rows.append(message)
        value = str(text) if complete or request is None else message.text + str(text)
        value = value[-32768:]
        self._chars += len(value) - len(message.text)
        message.text = value
        message.editor.setPlainText(value)
        if complete:
            message.request = None
        while len(self._rows) > 100 or self._chars > 131072:
            oldest = next((row for row in self._rows if row.role == "progress"), self._rows[0])
            self._remove(oldest)
        self._widths()

    def _widths(self):
        outer = self._layout.contentsMargins()
        available = max(1, self.viewport().width() - outer.left() - outer.right())
        for message in self._rows:
            editor = message.editor
            width = available
            if message.bubble is not None:
                natural = QtGui.QTextDocument()
                natural.setDefaultFont(editor.font())
                natural.setDocumentMargin(editor.document().documentMargin())
                natural.setPlainText(message.text)
                natural.setTextWidth(-1)
                margins = message.bubble.layout().contentsMargins()
                padding = margins.left() + margins.right()
                width = min(max(1, available - padding), max(1, int(natural.idealWidth()) + 1))
                message.bubble.setFixedWidth(width + padding)
            editor.setFixedWidth(width)
            editor.document().setTextWidth(width)
            height = editor.document().size().height()
            editor.setFixedHeight(max(1, int(height + 0.999)))

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.viewport() and kind == QtCore.QEvent.Resize:
            self._widths()
        elif kind == QtCore.QEvent.FontChange:
            QtCore.QTimer.singleShot(0, self._widths)
        return super().eventFilter(watched, event)
