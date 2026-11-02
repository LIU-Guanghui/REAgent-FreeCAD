"""A conversation Dock with fixed human commands and on-demand status."""

import os

from REAgent.ui import QtCore, QtGui, QtWidgets
from REAgent.ui.transcript import Transcript


_DEFAULT_GOAL = "Reconstruct the STEP using editable native FreeCAD features; inspect the result and report unresolved questions."


def plain_label(text=""):
    """A plain-text, word-wrapped label; the only shared widget constructor."""
    label = QtWidgets.QLabel(text)
    label.setTextFormat(QtCore.Qt.PlainText)
    label.setWordWrap(True)
    return label


class MessageInput(QtWidgets.QPlainTextEdit):
    submitted = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._composing = False
        self._ime_committed = False
        self._ime_timer = QtCore.QTimer(self)
        self._ime_timer.setSingleShot(True)
        self._ime_timer.timeout.connect(self._end_ime_commit)

    def inputMethodEvent(self, event):
        if self._composing and event.commitString():
            self._ime_committed = True
            self._ime_timer.start(0)
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def _end_ime_commit(self):
        self._ime_committed = False

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not self._composing:
            if self._ime_committed:
                pass
            elif event.modifiers() & (QtCore.Qt.AltModifier | QtCore.Qt.ShiftModifier):
                self.insertPlainText("\n")
            elif not event.isAutoRepeat():
                self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Panel(QtWidgets.QDockWidget):
    def __init__(self, manager, window):
        super().__init__("REAgent", window)
        self.setObjectName("REAgentTaskPanel")
        self._manager = manager
        self._can_send = False
        self._rejected_command = None
        self._dismissed_completion = None
        body = QtWidgets.QWidget()
        self.setWidget(body)
        layout = QtWidgets.QVBoxLayout(body)
        row = QtWidgets.QHBoxLayout()
        self.status = plain_label("Ready")
        row.addWidget(self.status, 1)
        self.settings = QtWidgets.QPushButton("Settings")
        self.settings.clicked.connect(manager.configure)
        row.addWidget(self.settings)
        layout.addLayout(row)
        self.commands = {}
        self._callbacks = {}
        for name, label, callback in (
            ("rebuild", "Rebuild model…", self.start_task),
            ("compact", "Compact context", lambda: manager.action("compact")),
            ("status", "Status", lambda: manager.open_viewer("status")),
        ):
            action = QtGui.QAction(label, self)
            action.setToolTip("Shortcut: /" + name)
            action.triggered.connect(lambda _checked=False, command=name: self.run_command(command))
            self.commands[name] = action
            self._callbacks[name] = callback
        self.text = Transcript()
        layout.addWidget(self.text, 1)
        self._suggestions = QtWidgets.QListWidget()
        self._suggestions.setFocusPolicy(QtCore.Qt.NoFocus)
        self._suggestions.setMaximumHeight(150)
        self._suggestions.itemClicked.connect(self._complete_command)
        self._suggestions.hide()
        layout.addWidget(self._suggestions)
        input_area = QtWidgets.QGridLayout()
        context = QtWidgets.QHBoxLayout()
        self.context_label = plain_label("Context used")
        self.context_label.setForegroundRole(QtGui.QPalette.Mid)
        self.context_percent = plain_label("—")
        self.context_count = plain_label("— / —")
        for label in (self.context_label, self.context_percent, self.context_count):
            label.setWordWrap(False)
        self.context_count.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.context_count.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        context.addWidget(self.context_label)
        context.addWidget(self.context_percent)
        context.addStretch(1)
        context.addWidget(self.context_count, 1)
        input_area.addLayout(context, 0, 0)
        self.input = MessageInput()
        self.input.setMaximumHeight(90)
        self.input.textChanged.connect(self._update_send)
        self.input.submitted.connect(self._submit_input)
        self.input.installEventFilter(self)
        input_area.addWidget(self.input, 1, 0)
        buttons = QtWidgets.QVBoxLayout()
        self.send = QtWidgets.QPushButton("Send")
        self.send.clicked.connect(self._send)
        buttons.addWidget(self.send)
        self.pause = QtWidgets.QPushButton("Pause")
        self.pause.clicked.connect(manager.pause)
        buttons.addWidget(self.pause)
        input_area.addLayout(buttons, 1, 1)
        input_area.setColumnStretch(0, 1)
        layout.addLayout(input_area)
        row = QtWidgets.QHBoxLayout()
        self.new_task = QtWidgets.QPushButton("New task")
        self.new_task.clicked.connect(manager.new_task)
        row.addWidget(self.new_task)
        self.end_task = QtWidgets.QPushButton("End task")
        self.end_task.clicked.connect(lambda: manager.request_close("task"))
        row.addWidget(self.end_task)
        self.save = QtWidgets.QPushButton("Save")
        self.save.clicked.connect(lambda: manager.action("save"))
        row.addWidget(self.save)
        self.images = QtWidgets.QPushButton("Image")
        self.images.clicked.connect(lambda: manager.open_viewer("images"))
        row.addWidget(self.images)
        self.journal = QtWidgets.QPushButton("Journal")
        self.journal.clicked.connect(lambda: manager.open_viewer("journal"))
        row.addWidget(self.journal)
        row.addStretch(1)
        layout.addLayout(row)
        window.addDockWidget(QtCore.Qt.RightDockWidgetArea, self)

    def set_controls(self, enabled, *, can_new_task, can_send):
        for name, action in self.commands.items():
            action.setEnabled(enabled[name])
        self.settings.setEnabled(enabled["settings"])
        self.new_task.setEnabled(can_new_task)
        self.images.setEnabled(enabled["images"])
        self.journal.setEnabled(enabled["journal"])
        self.journal.setToolTip("" if enabled["journal"] else "No retained CAD task log is available.")
        self._can_send = can_send
        self.pause.setEnabled(enabled["pause"])
        self.end_task.setEnabled(enabled["close"])
        self.save.setEnabled(enabled["save"])
        self._update_send()

    def _submit_input(self):
        text = self.input.toPlainText().strip()
        if text.startswith("/"):
            problem = self._command_problem(text)
            if problem:
                self._show_command_problem(problem)
                return
        if self.send.isEnabled():
            self._send()

    def _command_problem(self, text):
        hint = "Valid commands: " + ", ".join("/" + name for name in self.commands) + "."
        parts = text.split()
        if len(parts) != 1:
            return "Commands do not accept inline arguments. " + hint
        name = parts[0][1:]
        action = self.commands.get(name)
        if action is None:
            return "Unknown command. " + hint
        if not action.isEnabled():
            return "Command unavailable in the current task state: /" + name
        return ""

    def _show_command_problem(self, problem):
        key = (self.input.toPlainText(), problem)
        if self._rejected_command != key:
            self.message("status", problem)
            self._rejected_command = key

    def set_context(self, view):
        percent, count, tooltip = view
        self.context_percent.setText(percent)
        self.context_count.setText(count)
        for label in (self.context_label, self.context_percent, self.context_count):
            label.setToolTip(tooltip)

    def _update_send(self):
        text = self.input.toPlainText().strip()
        if self._rejected_command is not None and self._rejected_command[0] != self.input.toPlainText():
            self._rejected_command = None
        if text.startswith("/"):
            problem = self._command_problem(text)
            self.send.setEnabled(not problem)
            self.send.setToolTip(problem)
        else:
            self.send.setEnabled(self._can_send and bool(text))
            self.send.setToolTip("")
        self._update_suggestions()

    def _update_suggestions(self):
        raw = self.input.toPlainText()
        if raw != self._dismissed_completion:
            self._dismissed_completion = None
        previous = self._suggestions.currentItem()
        selected = previous.data(QtCore.Qt.UserRole) if previous is not None else None
        self._suggestions.clear()
        if (not self.input.hasFocus() or self.input._composing or self.input._ime_committed
                or not raw.startswith("/") or any(char.isspace() for char in raw)
                or raw == self._dismissed_completion
                or (raw[1:] in self.commands and self.commands[raw[1:]].isEnabled())):
            self._suggestions.hide()
            return
        names = [name for name in self.commands if name.startswith(raw[1:].lower())]
        available = []
        for name in names:
            item = QtWidgets.QListWidgetItem("/" + name + "    " + self.commands[name].text())
            item.setData(QtCore.Qt.UserRole, name)
            if self.commands[name].isEnabled():
                available.append(name)
            else:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
            self._suggestions.addItem(item)
        selected = selected if selected in available else available[0] if available else None
        self._suggestions.setCurrentRow(names.index(selected) if selected is not None else -1)
        self._suggestions.setVisible(bool(names))

    def _move_suggestion(self, step):
        rows = [index for index in range(self._suggestions.count())
                if self._suggestions.item(index).flags() & QtCore.Qt.ItemIsEnabled]
        if not rows:
            return
        current = self._suggestions.currentRow()
        row = rows[(rows.index(current) + step) % len(rows)] if current in rows else rows[0] if step > 0 else rows[-1]
        self._suggestions.setCurrentRow(row)
        self._suggestions.scrollToItem(self._suggestions.currentItem())

    def _complete_command(self, item):
        if item is None:
            return
        name = item.data(QtCore.Qt.UserRole)
        if name not in self.commands or not self.commands[name].isEnabled():
            self._suggestions.hide()
            return
        self.input.setPlainText("/" + name)
        self.input.moveCursor(QtGui.QTextCursor.End)
        self._suggestions.hide()
        self.input.setFocus()

    def eventFilter(self, watched, event):
        if watched is getattr(self, "input", None):
            if event.type() == QtCore.QEvent.FocusOut:
                self._suggestions.hide()
            elif event.type() == QtCore.QEvent.FocusIn:
                self._update_suggestions()
            elif (event.type() == QtCore.QEvent.KeyPress and self._suggestions.isVisible()
                  and not self.input._composing and not self.input._ime_committed
                  and not event.modifiers() & (QtCore.Qt.AltModifier | QtCore.Qt.ShiftModifier
                                               | QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier)):
                key = event.key()
                if key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
                    self._move_suggestion(-1 if key == QtCore.Qt.Key_Up else 1)
                elif key in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                    if not event.isAutoRepeat():
                        self._complete_command(self._suggestions.currentItem())
                elif key == QtCore.Qt.Key_Escape:
                    self._dismissed_completion = self.input.toPlainText()
                    self._suggestions.hide()
                else:
                    return False
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _send(self):
        if not self.send.isEnabled():
            return
        text = self.input.toPlainText().strip()
        if text.startswith("/"):
            problem = self._command_problem(text)
            if problem:
                self._show_command_problem(problem)
                return
            accepted = self.run_command(text[1:])
        else:
            accepted = self._manager.send(text)
        if accepted:
            self.input.clear()

    def run_command(self, name):
        problem = self._command_problem("/" + name)
        if problem:
            self._show_command_problem(problem)
            return False
        return self._callbacks[name]() is not False

    def _path_row(self, form, title, directory):
        widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        edit = QtWidgets.QLineEdit()
        button = QtWidgets.QPushButton("…")

        def browse():
            if directory:
                selected = QtWidgets.QFileDialog.getExistingDirectory(self, title, edit.text())
            else:
                selected = QtWidgets.QFileDialog.getOpenFileName(self, title, edit.text(), "STEP (*.step *.stp)")[0]
            if selected:
                edit.setText(selected)

        button.clicked.connect(browse)
        row.addWidget(edit)
        row.addWidget(button)
        form.addRow(title, widget)
        return edit

    def start_task(self, objective=None):
        if objective is None:
            objective = self.input.toPlainText().strip()
            if objective.startswith("/"):
                objective = ""
        objective = objective or _DEFAULT_GOAL
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Rebuild model")
        layout = QtWidgets.QVBoxLayout(dialog)
        form = QtWidgets.QFormLayout()
        source = self._path_row(form, "STEP file", False)
        output = self._path_row(form, "Save to", True)
        output.setPlaceholderText("Folder for the model and task log")

        def suggest_output(path):
            if not output.text().strip() and os.path.isfile(path):
                output.setText(os.path.dirname(path))

        source.textChanged.connect(suggest_output)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Start")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        accepted = self._manager.present(dialog) == QtWidgets.QDialog.Accepted
        source_path, output_dir = source.text(), output.text()
        dialog.deleteLater()
        if accepted and self._manager.start(source_path, output_dir, objective):
            self.message("user", objective)
            self.input.clear()
            return True
        return False

    @staticmethod
    def compaction_text(change):
        before, after = change["before"], change["after"]
        label = "Context image payload reduced" if change["reason"] == "image_pressure" else "Context compacted"
        text = (label + ": Local estimate {:,} → {:,} tokens; request bytes {:,} → {:,}.".format(
            before["local_tokens_estimate"], after["local_tokens_estimate"],
            before["request_bytes"], after["request_bytes"]))
        if change["estimate_method_changed"]:
            text += " Capacity estimate method changed; the two capacity estimates are not directly comparable."
        return text

    def status_text(self, details):
        snapshot, context = details["snapshot"], details["context"]
        facts = snapshot.get("controller") or {}
        artifacts = facts.get("artifacts") or {}
        state = str(snapshot.get("status", "idle")).replace("_", " ")
        rows = ["Task", "Last reported state: " + state[:1].upper() + state[1:],
                "Snapshot received: " + str(details["snapshot_received_at"] or "Not received"),
                "Type: " + ("CAD reconstruction" if facts else "Conversation" if snapshot.get("mode") == "chat" else
                            "Ready for the first message" if details["task_open"] else "No active task"),
                "", "Model: " + str(context.get("model") or "Not configured"), "", "Context"]
        capacity = context.get("capacity_tokens")
        if capacity is not None:
            rows.extend(("Capacity: {:,} tokens".format(capacity),
                         "Output budget: {:,} tokens".format(context["output_tokens"]),
                         "Working reserve: {:,} tokens".format(context["reserve_tokens"])))
        if context.get("status") == "available":
            estimate = context["estimate"]
            rows.append("Estimated input: ~{:,} tokens ({:.1f}%)".format(
                estimate["input_tokens_estimate"], context["occupancy_percent_estimate"]))
            rows.append("Estimate basis: " + ("Service usage plus new suffix" if estimate["method"].startswith("service_")
                                               else "Conservative local bound"))
            rows.append("Estimated room after output/reserve: ~{:,} tokens".format(context["remaining_tokens_estimate"]))
            if not estimate["image_soft_target_met"]:
                rows.append("Protected image groups exceed the soft target; hard limits still apply.")
            if context.get("pending_results") or snapshot.get("pending_supplements"):
                rows.append("In-flight results or queued corrections are not fully included.")
        else:
            rows.append("Input estimate: " + ("Conversation not started" if context.get("status") == "not_started" else "Unavailable"))
        report = context.get("last_service_report")
        if report is not None:
            count = (report.get("usage") or {}).get("input_tokens")
            rows.extend(("", "Last service report (historical): " + ("Summary" if report["kind"] == "summary" else "Main request"),
                         "Input: " + ("Unknown" if count is None else "{:,} tokens".format(count))))
        change = context.get("last_compaction")
        if change is not None:
            rows.extend(("", self.compaction_text(change)))
            usage = change.get("summary_usage")
            if usage:
                rows.append("Summary cost (reported): Input %s · Output %s tokens" % (
                    usage.get("input_tokens"), usage.get("output_tokens")))
        for total in snapshot.get("usage", {}).values():
            counts = total["counts"]
            rows.extend(("", "Cumulative request usage · " + ("Summary" if total["kind"] == "summary" else "Main"),
                         "Known input: {:,} · Output: {:,} tokens; input unknown for {} requests".format(
                             counts["input_tokens"]["known_sum"], counts["output_tokens"]["known_sum"],
                             counts["input_tokens"]["unknown_requests"])))
        request = snapshot.get("last_model_request") or {}
        if request.get("error"):
            error = request["error"]
            rows.extend(("", "Request failed: " + str(error.get("code")) + " · " + str(error.get("message"))))
        if snapshot.get("pending_model_result"):
            rows.append("Result retained; journal recovery is required before a new decision.")
        if snapshot.get("callback_error"):
            rows.append("Presentation callback failed: " + str(snapshot["callback_error"]))
        if facts:
            rows.extend(("", "CAD documents", "Source: " + str(facts.get("source_document") or "Not created"),
                         "Target: " + str(facts.get("target_document") or "Not created"), "", "Output",
                         "Folder: " + str(artifacts.get("output_dir") or "Not selected"),
                         "Model file (planned): " + str(artifacts.get("model_path") or "Not allocated")))
            saved = facts.get("last_saved_checkpoint")
            if saved:
                rows.append("Last confirmed save: " + str(saved.get("saved_to") or "Path unavailable"))
                rows.append("Subsequent edits may be unsaved.")
            if facts.get("target_writes_blocked"):
                sources = [key[:1].upper() + key[1:] for key, value in facts.get("write_block_sources", {}).items() if value is True]
                rows.append("Modeling blocked: " + (", ".join(sources) or "Inspect task and recovery details"))
            journal = facts.get("journal") or {}
            if journal and not journal.get("complete"):
                rows.append("Journal incomplete; retained facts have not all been persisted.")
        return "\n".join(rows)

    def append(self, text):
        self.text.append(text)

    def begin_reply(self):
        self.text.begin_reply()

    def message(self, role, text):
        self.text.message(role, text)

    def reset_task(self, *, clear=False):
        if clear:
            self.text.clear()
            self.input.clear()
        else:
            self.text.begin_reply()

    def closeEvent(self, event):
        self.hide()
        event.ignore()
