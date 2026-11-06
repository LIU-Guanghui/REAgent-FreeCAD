"""Main-window owner of one task, worker, presentation and cooperative shutdown."""

import json
import os
from datetime import datetime
from enum import Enum

import FreeCADGui as Gui

from REAgent.ui import QtCore, QtWidgets
from REAgent.model.limits import CONTEXT_RESERVE_TOKENS
from REAgent.ui.panel import Panel
from REAgent.ui.viewers import ImageViewer, TaskViewer
from REAgent.ui.comparison import ComparisonDialog, comparison_text
from REAgent.session.state_snapshots import preview_entries
from REAgent.session.state_snapshots import tool_message
from REAgent.ui.settings import SettingsDialog, saved_settings
from REAgent.ui.worker import Worker, tool_fault


def short_token_count(value):
    if value < 1000:
        return str(value)
    scale, suffix = (1000000, "M") if value >= 1000000 else (1000, "K")
    tenths = (value * 10 + scale // 2) // scale
    if suffix == "K" and tenths >= 10000:
        scale, suffix = 1000000, "M"
        tenths = (value * 10 + scale // 2) // scale
    return str(tenths // 10) + ("." + str(tenths % 10) if tenths % 10 else "") + suffix


class CloseIntent(Enum):
    TASK = "task"
    RELEASE = "release"
    EXIT = "exit"


class ClosePhase(Enum):
    WAITING = "waiting"
    ENDING = "ending"
    ENDED = "ended"
    THREAD_FINISHING = "thread_finishing"
    NATIVE_CLOSE = "native_close"


class SourceWindowController(QtCore.QObject):
    def __init__(self, window, release_role, *, read_settings, save_settings):
        super().__init__(window)
        self._window = window
        self._release_role = release_role
        self._read_settings, self._save_settings = read_settings, save_settings
        self._worker = None
        self._worker_failed = False
        self._worker_retiring = False
        self._busy = False
        self._action = None
        self._progress = None
        self._snapshot = {"status": "idle", "controller": None}
        self._task_open = False
        self._results = ()
        self._config = None
        self._pending_config = None
        self._profile_name = None
        self._pending_profile = None
        self._draft_credentials = None
        self._credential_dialog = None
        self._close_intent: CloseIntent | None = None
        self._close_phase: ClosePhase | None = None
        self._native_close_permit = False
        self._window_gone = False
        self._dialog = None
        self._dialog_wait = False
        self._input_dialog = None
        self._viewer = None
        self._viewer_kind = None
        self._snapshot_received_at = None
        self._journal_cache = {}
        self._image_entries = {}
        self._presentation_session = None
        self._task_artifacts = {}
        self._presentation_task = 0
        self._action_number = None
        self._obsolete_generation = -1
        self._presented = set()
        self._reports_presented = set()
        self._comparison_presented = set()
        self._comparison = None
        self._status_notice = None
        self._journal_fault = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._wait_notice)
        self._workbench_timer = QtCore.QTimer(self)
        self._workbench_timer.setSingleShot(True)
        self._workbench_timer.timeout.connect(self._sync_workbench)
        self.panel = Panel(self, window)
        window.workbenchActivated.connect(self._workbench_changed)
        self.panel.visibilityChanged.connect(self._visibility_changed)
        window.installEventFilter(self)
        window.destroyed.connect(self._window_destroyed)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._application_quitting)
        try:
            saved = saved_settings(self._read_settings)
            if saved is not None:
                self._config, self._draft_credentials, self._profile_name = saved
        except ValueError as exc:
            self._fault("Saved model settings could not be restored. " + str(exc)[:500] + " Open Settings to correct them.")
        self._render()

    def show(self):
        if not self._in_workbench():
            self.panel.hide()
            return
        self.panel.show()
        self.panel.raise_()

    def _in_workbench(self):
        workbench = Gui.activeWorkbench()
        # A newly active handler may not have its internal __Workbench__ yet.
        return workbench is not None and workbench is Gui.listWorkbenches().get("REAgentWorkbench")

    def _workbench_changed(self, name):
        if name != "REAgentWorkbench":
            self.panel.hide()
        self._workbench_timer.start(0)

    def _sync_workbench(self):
        if self._in_workbench():
            self.show()
        else:
            self.panel.hide()

    def _visibility_changed(self, visible):
        # FreeCAD may restore Dock visibility after the workbench exit callback.
        if visible and not self._in_workbench():
            self._workbench_timer.start(0)

    def can_release(self):
        return (not self._busy and not self._task_open and not self._snapshot.get("controller")
                and (self._worker is None or not self._worker.isRunning())
                and not self._worker_retiring and not self._worker_failed)

    def _notice(self, message):
        self._status_notice = message
        self.panel.status.setToolTip(message)

    def _fault(self, message):
        self.panel.message("error", message)

    def _model_error(self, error):
        text = error["code"] + ": " + error["message"]
        request = (error.get("diagnostics") or {}).get("request") or {}
        if request.get("output_tokens"):
            text += ". Output budget: {:,} tokens".format(request["output_tokens"])
            if request.get("kind") == "model":
                text += (". The delivery receipt is retained; its report stopped." if request.get("purpose") == "report" else
                         ". No tools from this failed decision were dispatched. Send a new message when ready.")
        self._fault(text)

    def _can_save(self):
        facts = self._snapshot.get("controller") or {}
        # A journal block alone must not prevent explicit preservation of a
        # known Target. Controller/native guards decide the actual save result.
        return bool(facts.get("target_document") and facts.get("session_status") == "ready"
                    and not facts.get("write_block_sources", {}).get("controller"))

    def _can_act(self, *, allow_busy=False, closing=False):
        return not ((self._busy and not allow_busy) or self._worker_failed or self._worker_retiring
                    or self._input_dialog is not None or (self._close_intent and not closing))

    def _render(self):
        facts = self._snapshot.get("controller") or {}
        active = bool(facts)
        chatting = self._snapshot.get("mode") == "chat"
        live = active or chatting
        idle = self._can_act()
        writable = active and facts.get("session_status") == "ready" and not facts.get("target_writes_blocked")
        viewing = self._input_dialog is None and self._close_intent is None
        enabled = {"settings": idle and (not active or self._snapshot.get("status") in ("paused", "ready")),
                   "rebuild": idle and self._task_open and not active, "pause": self._busy and self._close_intent is None,
                   "compact": idle and live and bool((self._snapshot.get("context") or {}).get("can_compact")),
                   "compare": idle and writable and not facts.get("source_basis_unknown"),
                   "save": idle and self._can_save(), "journal": viewing and bool(self._journal_cache),
                   "close": (self._task_open or live or self._busy) and viewing and not self._worker_failed,
                   "status": viewing, "images": viewing and bool(self._image_entries)}
        can_send = self._task_open and self._can_act(allow_busy=live)
        self.panel.set_controls(enabled, can_new_task=idle and not self._task_open and not live, can_send=can_send)
        self.panel.images.setToolTip("Task is ending." if self._close_intent else
            "Close the input form first." if self._input_dialog is not None else
            "No prepared images yet. Source initial views appear after capture and preparation." if not self._image_entries else
            "Browse all %d prepared task images." % len(self._image_entries))
        status = {"idle": "Ready", "ready": "Ready", "closed": "Ready", "paused": "Paused"}.get(self._snapshot.get("status"), "Working")
        if self._busy:
            status = "Pausing" if self._snapshot.get("pause", {}).get("paused") else "Working"
        action = self._snapshot.get("action") or {}
        resume_notice = ""
        if action.get("scope") == "reconstruct":
            resume_notice = "Reconstruction request: " + str(action.get("source_preview") or "")
        if self._close_intent:
            status = "Closing"
        if self._worker_failed:
            status = "Error"
        elif self._worker_retiring:
            status = "Closing"
        elif active and (not writable or facts.get("source_basis_unknown")) and not self._busy and not self._close_intent:
            status = "Blocked"
        notice = self._status_notice or ""
        self.panel.status.setText(status)
        self.panel.status.setToolTip("\n".join(filter(None, (resume_notice, notice, str(self._snapshot.get("reason") or "")))))
        self.panel.set_context(self.context_view())
        self._update_viewer()

    def details(self):
        context = self._snapshot.get("context")
        if context is None or not (self._snapshot.get("controller") or self._snapshot.get("mode") == "chat"):
            context = {"status": "not_started"}
            if self._config is not None:
                context.update(model=self._config.model, capacity_tokens=self._config.context_tokens,
                               output_tokens=self._config.output_tokens, reserve_tokens=CONTEXT_RESERVE_TOKENS,
                               reasoning_effort=self._config.reasoning_effort)
        return {"snapshot": self._snapshot, "task_open": self._task_open, "last_completed_batch": [
            {**{key: row[key] for key in ("function", "action_number", "request_number", "response_id",
                                         "generation", "tool_call_id", "is_error", "side_effect") if key in row},
             "result": row["value"]} for row in self._results], "last_progress": self._progress,
                "snapshot_received_at": self._snapshot_received_at, "context": context}

    def new_task(self):
        if (self._task_open or not self._can_act() or self._snapshot.get("controller")
                or self._snapshot.get("mode") == "chat"):
            self._notice("End the current task before starting another.")
            return False
        # This is an empty UI task; the first message creates the real conversation.
        self._task_open = True
        self._presentation_task += 1
        self._obsolete_generation = -1
        self._status_notice = None
        self._snapshot = {"status": "idle", "controller": None, "mode": None,
                          "model_configured": self._snapshot.get("model_configured", False)}
        self._results, self._progress = (), None
        self._reset_views()
        self.panel.reset_task(clear=True)
        self._render()
        self.panel.input.setFocus()
        return True

    def send(self, text):
        if not self._task_open:
            return False
        facts = self._snapshot.get("controller")
        if not facts and not self._busy:
            if not self._snapshot.get("model_configured") and (self._config is None or self._draft_credentials is None):
                self.configure()
                if self._config is None or self._draft_credentials is None:
                    return False
            accepted = self.action("chat", {"config": self._config, "credentials": self._draft_credentials,
                                             "text": text})
            if accepted:
                self.panel.message("user", text)
            return accepted
        if (self._busy or not facts or facts.get("session_status") != "ready"
                or facts.get("target_writes_blocked")):
            accepted = self.supplement(text)
        else:
            accepted = self.action("advance", {"intent": text})
        if accepted and text:
            self.panel.message("user", text)
        return accepted

    def configure(self):
        if not self._can_act():
            return self._notice("Wait for the current action before changing settings.")
        locked = bool(self._snapshot.get("controller"))
        dialog = SettingsDialog(self.panel, read_settings=self._read_settings, save_settings=self._save_settings, config=self._config, profile_name=self._profile_name, lock_model=locked)
        if self.present(dialog) == QtWidgets.QDialog.Accepted:
            if self._snapshot.get("model_configured"):
                if self.action("configure", {"config": dialog.config, "credentials": dialog.take_credentials()}):
                    self._pending_config = dialog.config
                    self._pending_profile = dialog.profile_name
                    self._credential_dialog = dialog
                else:
                    dialog.clear_credentials()
                    dialog.deleteLater()
            else:
                self._clear_credentials()
                self._config = dialog.config
                self._profile_name = dialog.profile_name
                self._draft_credentials = dialog.take_credentials()
                self._credential_dialog = dialog
                self._notice("Model settings ready." + (" API key is available for this session only." if dialog.session_key_only else ""))
        else:
            dialog.deleteLater()
        self._render()

    def _clear_credentials(self):
        if self._draft_credentials is not None:
            self._draft_credentials.clear()
            self._draft_credentials = None
        if self._credential_dialog is not None:
            self._credential_dialog.clear_credentials()
            self._credential_dialog.deleteLater()
            self._credential_dialog = None

    def present(self, dialog):
        """Keep modal input separate from execution and close its form before exit."""
        if not self._can_act():
            return QtWidgets.QDialog.Rejected
        self._input_dialog = dialog
        self._render()
        try:
            result = dialog.exec_()
            return QtWidgets.QDialog.Rejected if self._close_intent else result
        finally:
            self._input_dialog = None
            self._render()
            if self._close_intent:
                QtCore.QTimer.singleShot(0, lambda: self._advance_close("progress"))

    def start(self, source, output, objective):
        if not self._task_open or self._snapshot.get("controller"):
            self._notice("Finish the current action/task before starting another.")
            return False
        if (not os.path.isabs(source) or not os.path.isfile(source)
                or os.path.splitext(source)[1].lower() not in (".step", ".stp")
                or not os.path.isabs(output) or not os.path.isdir(output)):
            self._notice("Choose an existing absolute STEP path and an existing absolute output directory.")
            return False
        if not self._snapshot.get("model_configured") and (self._config is None or self._draft_credentials is None):
            self.configure()
            if self._config is None or self._draft_credentials is None:
                return False
        if self.action("start", {"config": self._config, "credentials": self._draft_credentials,
                                 "task": {"source_path": source, "output_dir": output, "observation_policy": "visual"},
                                 "objective": objective}):
            self._reset_views()
            self._results = ()
            self._progress = None
            self.panel.reset_task()
            self._notice("STEP: " + source + "\nOutput: " + output)
            self._render()
            return True
        return False

    def action(self, action, arguments=None, *, closing=False):
        if not self._can_act(closing=closing):
            self._notice("The original worker is busy or closing; this action was not queued.")
            return False
        if action not in ("start", "chat", "configure", "advance", "compact", "close") and not self._snapshot.get("controller"):
            self._notice("There is no retained task for this action.")
            return False
        if self._worker is None:
            if action not in ("start", "chat"):
                return False
            self._worker = Worker(self)
            self._worker.ready.connect(self._drain, QtCore.Qt.QueuedConnection)
            self._worker.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
            self._worker.start()
        try:
            if not self._worker.isRunning():
                self._worker.reactivate()
            self._action_number = self._worker.submit(action, arguments, presentation_task=self._presentation_task)
        except (ValueError, RuntimeError) as exc:
            self._notice(str(exc))
            return False
        self._busy = True
        self._action = action
        self._presented.clear()
        self._obsolete_generation = -1
        self._status_notice = None
        if closing:
            self._timer.start(10000)
        self._render()
        return True

    def pause(self):
        if self._worker is not None:
            self._worker.pause()
            self._invalidate_previews()
            self._notice("Pause requested. The same worker will collect any in-flight result.")
            self._render()

    def supplement(self, text):
        if (not self._can_act(allow_busy=True)
                or not (self._snapshot.get("controller") or self._snapshot.get("mode") == "chat")):
            self._notice("A correction needs the current live task outside shutdown.")
            return False
        try:
            self._worker.supplement(text)
        except (ValueError, RuntimeError) as exc:
            self._notice(str(exc))
            return False
        self._invalidate_previews()
        facts = self._snapshot.get("controller") or {}
        if facts.get("target_writes_blocked"):
            self._notice("Change request queued. Target writes are blocked. Inspect Status for the cause.")
        else:
            self._notice("Message queued; processing stays paused." if self._snapshot.get("pause", {}).get("manual") else
                         "Message queued. The current operation will finish before the Target conversation is processed.")
        self._render()
        return True

    def _invalidate_previews(self):
        self._snapshot = self._worker.snapshot()
        self._obsolete_generation = self._snapshot["pause"].get("generation", -1)
        self.panel.text.discard_previews()

    def compare(self):
        facts = self._snapshot.get("controller") or {}
        dialog = ComparisonDialog(self.panel, facts)
        accepted = self.present(dialog) == QtWidgets.QDialog.Accepted
        options, request = dialog.options, dialog.view_request
        dialog.deleteLater()
        if not accepted:
            return False
        return self.action("compare", {"options": options, "view_request": request})

    def context_view(self):
        details = self.details()
        context = details["context"]
        capacity = context.get("capacity_tokens")
        if context.get("status") != "available" or capacity is None:
            if capacity is None:
                return "—", "— / —", "Model not configured."
            active = self._snapshot.get("controller") or self._snapshot.get("mode") == "chat"
            if not details["task_open"] and not active:
                reason = "Task ended." if self._snapshot.get("status") == "closed" else "No active task."
            elif context.get("status") == "unavailable":
                reason = "Estimate unavailable: " + str(context.get("error") or "reason not reported") + "."
            elif not active:
                reason = "Conversation not started."
            else:
                reason = "Context estimate is not ready for this task."
            return "—", "— / " + short_token_count(capacity), reason + " Capacity: {:,} tokens.".format(capacity)
        used = context["estimate"]["input_tokens_estimate"]
        percent = (200 * used + capacity) // (2 * capacity)
        tooltip = "Estimated input: {:,} / {:,} tokens. Basis: {}. Method: {}.".format(
            used, capacity, context["basis"], context["estimate"]["method"])
        if context.get("pending_results") or self._snapshot.get("pause", {}).get("queued") or self._busy:
            tooltip += " In-flight results or queued corrections are not fully included."
        return str(percent) + "%", "~" + short_token_count(used) + " / " + short_token_count(capacity), tooltip

    def _close_viewer(self):
        viewer, self._viewer = self._viewer, None
        self._viewer_kind = None
        if viewer is not None:
            viewer.finished.disconnect()
            viewer.close()
            viewer.deleteLater()

    def _reset_views(self):
        self._close_viewer()
        self._snapshot_received_at = None
        self._journal_cache = {}
        self._image_entries.clear()
        self._presentation_session = None
        self._task_artifacts = {}
        self._reports_presented.clear()
        self._comparison_presented.clear()
        self._comparison = None
        self._journal_fault = None

    def _register_images(self, images, value, session_id):
        if not images or session_id is None or session_id != self._presentation_session:
            return
        try:
            for entry in preview_entries(images, value, self._task_artifacts):
                self._image_entries.setdefault((session_id, entry["image_id"]), entry)
        except (KeyError, TypeError, ValueError) as exc:
            key = ("image_registration", str(exc)[:300])
            if key not in self._presented:
                self._presented.add(key)
                self._fault("Image registration unavailable. " + key[1])

    def _task_ended(self, journal=None):
        self._task_open = False
        self._snapshot = {**self._snapshot, "status": "closed"}
        if journal is not None:
            self._journal_cache["metadata"] = journal

    def open_viewer(self, kind):
        if kind not in ("status", "journal", "images", "comparison") or self._close_intent or self._input_dialog is not None:
            return False
        if kind == "journal" and not self._journal_cache:
            return False
        if kind == "images" and not self._image_entries:
            return False
        if kind == "comparison" and self._comparison is None:
            return False
        if self._viewer is not None and self._viewer_kind == kind:
            self._viewer.raise_()
            self._viewer.activateWindow()
            return True
        self._close_viewer()
        viewer = ImageViewer(self._window) if kind == "images" else TaskViewer(self._window, kind)
        self._viewer, self._viewer_kind = viewer, kind
        def finished(_result):
            if self._viewer is viewer:
                self._viewer, self._viewer_kind = None, None
            viewer.deleteLater()
        viewer.finished.connect(finished)
        self._update_viewer()
        viewer.show()
        return True

    def _receive_journal(self, event):
        cache = self._journal_cache
        session_id = event["session_id"]
        if self._presentation_session is not None and self._presentation_session != session_id:
            return
        self._presentation_session = session_id
        if cache and cache.get("session_id") != session_id:
            return  # Old task notifications cannot replace the new task's cache.
        cache.update(session_id=session_id, metadata=event["journal"])
        journal = event["journal"]
        error = journal.get("error")
        if error and self._journal_fault != error:
            self._journal_fault = error
            self._fault("Journal could not be written. " + str(error.get("message") or error.get("stage"))
                        + "\nInspect the Journal and the original output directory.")
        elif journal.get("complete"):
            self._journal_fault = None
        by_sequence = {row["sequence"]: row for row in cache.get("events", [])}
        by_sequence.update({row["sequence"]: row for row in event["events"]})
        rows = sorted(by_sequence.values(), key=lambda row: row["sequence"])
        retained, size = [], 0
        for row in reversed(rows[-200:]):
            cost = len(str(row))
            if size + cost > 98304:
                break
            retained.append(row)
            size += cost
        cache["events"] = list(reversed(retained))

    def _update_viewer(self):
        if self._viewer is None:
            return
        if self._viewer_kind == "images":
            self._viewer.update_entries(list(self._image_entries.values()))
            return
        if self._viewer_kind == "status":
            details = self.details()
            self._viewer.update_content(self.panel.status_text(details), details)
            return
        if self._viewer_kind == "comparison":
            comparison = self._comparison or {}
            reference = (self._snapshot.get("controller") or {}).get("recent_manual_comparison") or {}
            if reference.get("comparison_id") == comparison.get("comparison_id"):
                comparison = {**comparison, "reference": reference}
            self._viewer.update_content(comparison_text(comparison), comparison)
            return
        cache = self._journal_cache
        metadata = cache.get("metadata") or {}
        events = cache.get("events", [])
        rows = ["Task log: " + str(cache.get("session_id") or "Unavailable"),
                "Path: " + str(metadata.get("path") or "Unavailable"),
                "Recorded / persisted sequence: {} / {}".format(metadata.get("latest_sequence", 0), metadata.get("persisted_sequence", 0)),
                "Writing complete: " + str(metadata.get("complete", "unknown")),
                "Recording complete: " + str(metadata.get("recording_complete", "unknown"))]
        if metadata.get("error"):
            error = metadata["error"]
            rows.append("Log error: " + str(error.get("message") or error.get("exception_type"))[:500])
        if not metadata.get("complete", True):
            rows.append("Unpersisted facts remain in memory. End task retries recoverable I/O once.")
        if metadata.get("dropped_events"):
            rows.append("Lost events: " + str(metadata["dropped_events"]) + "; flush cannot restore them.")
        if metadata.get("residual_files"):
            rows.append("Cleanup residue remains; see technical details.")
        sequences = [row["sequence"] for row in events]
        if (sequences and sequences[0] > 1
                or any(right != left + 1 for left, right in zip(sequences, sequences[1:]))
                or metadata.get("latest_sequence", 0) > (sequences[-1] if sequences else 0)):
            rows.append("Showing a bounded recent process; omitted sequences remain in the original JSON when persisted.")
        rows.extend(("", json.dumps(events, ensure_ascii=False, indent=2) if events else "No displayable events yet."))
        self._viewer.update_content("\n".join(rows), {"journal": metadata, "events": events})

    def _request_key(self, event):
        return (event["presentation_task"], event["action_number"], event["generation"], event["request_number"])

    def _present_result(self, event):
        value = event["result"]
        if event.get("comparison_id"):
            key = (event["comparison_id"], event["comparison_step"], value.get("operation_id"))
            if key in self._comparison_presented:
                return
            self._comparison_presented.add(key)
        if tool_fault(event):
            if value.get("code") in ("invalid_tool_request", "batch_rejected"):
                key = ("batch_preflight", event.get("response_id"))
            else:
                key = ("tool", event.get("response_id"),
                       event.get("tool_call_id") or value.get("operation_id") or event.get("function"))
            if key not in self._presented:
                self._presented.add(key)
                text = tool_message(event)
                detail = value.get("detail") or {}
                reason = detail.get("reason")
                if reason in ("pending_transaction", "gui_edit_in_progress"):
                    text += "\nFinish or cancel the active edit in the Target window, then observe again."
                elif reason in ("empty_stack", "stack_top_mismatch", "transaction_not_owned", "foreign_redo", "history_state_changed"):
                    text += "\nInspect the Target's native Undo/Redo history before retrying."
                elif event["side_effect"] == "unknown":
                    if detail.get("committed"):
                        text += "\nThe transaction committed, but the final model check failed."
                    text += "\nTarget state is unconfirmed. Inspect Status before making more changes."
                elif value.get("restored") or detail.get("model_restored"):
                    text += "\nThe original model was restored."
                    if detail.get("redo_changed"):
                        text += " The native redo branch changed."
                elif event["side_effect"] == "none" and event.get("is_error"):
                    text += "\nThis operation did not change the model."
                elif event["side_effect"] == "committed" and event.get("is_error"):
                    text += "\nA change was committed. Inspect the actual state and Journal."
                unexecuted = event.get("unexecuted")
                if unexecuted:
                    count = unexecuted["count"]
                    label = "operation was" if count == 1 else "operations were"
                    text += f"\n{count} other {label} not executed. {unexecuted['reason']}"
                self._fault(text)
        elif event.get("function") == "agent_save" and value.get("saved") is True:
            self._notice("Model saved to " + str(value["saved_to"]))
        self._register_images(event["images"], value, event.get("session_id"))

    def _receive_state(self, snapshot):
        self._snapshot = snapshot
        self._snapshot_received_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        facts = snapshot.get("controller") or {}
        if facts.get("session_id"):
            self._presentation_session = facts["session_id"]
            self._task_artifacts = facts.get("artifacts") or self._task_artifacts
        self._present_report((snapshot.get("report") or {}).get("reply"))

    def _present_report(self, report):
        if not report or not report.get("text"):
            return
        key = (self._presentation_task, report["run_id"], report["response_id"])
        if key not in self._reports_presented:
            self.panel.message("agent", report["text"], complete=True)
            self._reports_presented.add(key)

    def _receive_event(self, event):
        if (event["presentation_task"] != self._presentation_task
                or event["action_number"] != self._action_number):
            return
        kind = event["event"]
        if kind == "journal_update":
            self._receive_journal(event)
        elif kind in ("text_preview", "model_reply", "request_discarded"):
            key = self._request_key(event)
            if kind == "model_reply" and event.get("retained_report"):
                self._present_report(event)
            elif kind == "request_discarded" or event["generation"] < self._obsolete_generation:
                self.panel.text.discard_previews(request=key)
            elif kind == "model_reply" and event["adopted"] and key not in self._presented:
                self._presented.add(key)
                if event["text"]:
                    self.panel.message("progress" if event["progress"] else "agent", event["text"],
                                       request=key, complete=True)
                else:
                    self.panel.text.discard_previews(request=key)
            elif kind == "text_preview" and key not in self._presented:
                self.panel.message("agent", event["text"], request=key)
        elif kind == "state":
            self._receive_state({key: value for key, value in event.items() if key not in ("event", "images")})
        elif kind == "tool_result":
            self._present_result(event)
            self._results = ({**event, "value": event["result"]},)
            self._progress = {**event, "images": ()}
        elif kind in ("tool_progress", "tool_started"):
            self._progress = {key: value for key, value in event.items() if key not in ("event", "images")}
        elif kind == "error":
            key = ("terminal", event.get("request_number"), event["code"])
            journal_codes = ("model_journal_pending", "context_journal_pending", "journal_pending")
            if key not in self._presented and not (self._journal_fault and event["code"] in journal_codes):
                self._presented.add(key)
                self._model_error(event)

    @QtCore.Slot()
    def _drain(self):
        if self._worker is None:
            return
        events, completion = self._worker.drain()
        for event in events:
            if event["event"] == "journal_update":
                self._receive_event(event)
        for event in events:
            if event["event"] != "journal_update":
                self._receive_event(event)
        if completion is not None:
            self._busy = False
            self._action = None
            self._receive_state(completion["snapshot"])
            for event in completion["presentation_fallback"]:
                self._receive_event(event)
            self.panel.text.discard_previews()
            if completion["action"] == "configure":
                if completion["ok"]:
                    self._config = self._pending_config
                    self._profile_name = self._pending_profile
                    self._notice("Model settings applied." + (" API key is available for this session only."
                                 if self._credential_dialog is not None and self._credential_dialog.session_key_only else ""))
                self._pending_config = None
                self._pending_profile = None
            if completion["new_results"]:
                self._results = completion["results"]
                for row in self._results:
                    self._present_result({**row, "result": row["value"], "session_id": self._presentation_session})
            for row in completion.get("comparison_results", ()):
                self._present_result({**row, "result": row["value"], "session_id": self._presentation_session})
            comparison = completion.get("comparison")
            if comparison is not None:
                changed = comparison.get("comparison_id") != (self._comparison or {}).get("comparison_id")
                self._comparison = comparison
                if changed and not self._close_intent:
                    self.open_viewer("comparison")
            if completion["credentials_consumed"]:
                self._clear_credentials()
            if completion["error"]:
                self._model_error(completion["error"])
            if completion["action"] == "close":
                final = completion["results"][-1]["value"] if completion["new_results"] else {}
                self._task_ended(final.get("journal"))
                if self._snapshot.get("controller"):
                    self._notice("Task ended locally. Use End task to retry pending cleanup.")
                else:
                    self._notice("Task ended. Surviving documents remain open.")
            if completion["presentation_backlog"]:
                self._notice("Paused after presentation backlog. Inspect retained results in Status.")
            if completion["paused_before_start"]:
                self._notice("Paused before this action began.")
            self._dismiss_wait()
            if self._close_intent and completion["action"] == "close":
                self._advance_close("task_completed")
        if completion is not None or events:
            self._render()
        self._advance_close("progress")

    def _dismiss_wait(self):
        if self._dialog_wait:
            dialog, self._dialog = self._dialog, None
            self._dialog_wait = False
            dialog.close()
            dialog.deleteLater()

    def request_close(self, intent):
        self._advance_close("request", intent=CloseIntent(intent))

    def cancel_close(self):
        self._advance_close("cancel")
        self._timer.stop()
        if self._dialog is not None:
            dialog, self._dialog = self._dialog, None
            self._dialog_wait = False
            dialog.close()
            dialog.deleteLater()
        self.pause()
        self._notice("Close intent cancelled. Already dispatched work still returns; no model continuation was requested.")
        self._render()

    def _advance_close(self, event, *, intent=None):
        """Commit the two close axes before callbacks; worker facts stay independent."""
        next_intent, next_phase = self._close_intent, self._close_phase
        step = None
        if event == "request":
            if next_intent is None:
                next_intent, next_phase = intent, ClosePhase.WAITING
                step = "request"
            else:
                if intent is CloseIntent.EXIT or intent is CloseIntent.RELEASE and next_intent is CloseIntent.TASK:
                    next_intent = intent
                    step = "upgrade"
                else:
                    step = "show"
        elif event in ("cancel", "native_returned"):
            next_intent = next_phase = None
        elif event == "task_completed":
            if next_intent is not None:
                next_phase = ClosePhase.ENDED
        elif event in ("progress", "worker_finished"):
            if (next_intent is None or self._busy or self._dialog is not None
                    or self._input_dialog is not None or self._worker_failed or self._worker_retiring
                    or next_phase is ClosePhase.NATIVE_CLOSE):
                return
            if next_phase is ClosePhase.WAITING and (self._snapshot.get("controller") or self._snapshot.get("mode") == "chat"):
                next_phase, step = ClosePhase.ENDING, "end"
            elif next_intent is CloseIntent.TASK:
                next_intent = next_phase = None
                step = "task"
            elif next_intent is CloseIntent.RELEASE and self._snapshot.get("controller"):
                step = "release_pending"
            elif self._worker is not None and self._worker.isRunning():
                next_phase, step = ClosePhase.THREAD_FINISHING, "thread"
            elif next_intent is CloseIntent.EXIT:
                next_phase, step = ClosePhase.NATIVE_CLOSE, "native"
            else:
                step = "release"
        else:
            raise ValueError("Invalid close event")
        self._close_intent, self._close_phase = next_intent, next_phase

        if step in ("upgrade", "show"):
            if step == "upgrade":
                self._render()
            self.show()
        elif step == "request":
            self._close_viewer()
            if self._busy:
                self.pause()
            self._timer.start(10000)
            self._render()
            if self._input_dialog is not None:
                self._input_dialog.reject()
                return
            self._advance_close("progress")
        elif step == "end":
            if not self.action("close", closing=True):
                self.cancel_close()
        elif step in ("task", "release_pending", "thread", "native", "release"):
            if self._task_open:
                self._task_ended()
            if step == "task":
                self._timer.stop()
                self._render()
            elif step == "release_pending":
                self._notice("Source or journal cleanup is pending. End task can retry; the main window can still exit.")
                self.cancel_close()
            elif step == "thread":
                self._worker_retiring = True
                try:
                    if self._close_intent is CloseIntent.EXIT:
                        self._worker.park()
                    else:
                        self._worker.shutdown()
                    self._timer.start(10000)
                except RuntimeError as exc:
                    self._worker_retiring = False
                    self._notice(str(exc))
                    self.cancel_close()
            else:
                self._timer.stop()
                if step == "native":
                    QtCore.QTimer.singleShot(0, self._native_close)
                else:
                    self._release()

    def _native_close(self):
        if self._window_gone or self._close_intent is not CloseIntent.EXIT or self._worker is not None and self._worker.isRunning():
            return
        metadata = self._journal_cache.get("metadata") or {}
        if metadata and not metadata.get("complete"):
            self._notice("The log is incomplete; some facts exist only in this process's memory.")
        self._native_close_permit = True
        accepted = False
        try:
            accepted = self._window.close()
        except RuntimeError as exc:
            if not self._window_gone:
                self._notice("Native window close failed: " + str(exc)[:300])
        finally:
            self._native_close_permit = False
            self._advance_close("native_returned")
        if not self._window_gone:
            if not accepted:
                self._notice("Window close cancelled. The task remains ended; images and journal are available.")
            self._render()

    def _window_destroyed(self, *_args):
        self._window_gone = True
        self._image_entries.clear()
        self._journal_cache = {}
        if self._draft_credentials is not None:
            self._draft_credentials.clear()
        self._draft_credentials = self._credential_dialog = None

    def _application_quitting(self):
        # Every coordinated native Close parks run before allowing destruction.
        self._clear_credentials()

    @QtCore.Slot()
    def _wait_notice(self):
        if not self._close_intent:
            return
        if self._dialog is not None:
            self._timer.start(10000)
            return
        dialog = QtWidgets.QMessageBox(self._window)
        dialog.setWindowTitle("REAgent is still collecting the current result")
        dialog.setTextFormat(QtCore.Qt.PlainText)
        dialog.setText("The 10-second prompt is not a cancellation deadline. The same worker and task remain held. "
                       "Current state: " + str(self._snapshot.get("status")) + " / " + (self._close_phase.value if self._close_phase is not None else "None")
                       + "\nLast reported operation: " + str(self._progress or "none")[:4096])
        wait = dialog.addButton("Keep waiting", QtWidgets.QMessageBox.AcceptRole)
        dialog.addButton("Cancel close", QtWidgets.QMessageBox.RejectRole)
        self._dialog = dialog
        self._dialog_wait = True

        def chosen(_result):
            if self._dialog is not dialog:
                return
            button = dialog.clickedButton()
            self._dialog = None
            self._dialog_wait = False
            dialog.deleteLater()
            if button is wait:
                self._timer.start(10000)
                self._advance_close("progress")
            else:
                self.cancel_close()

        dialog.finished.connect(chosen)
        dialog.open()

    @QtCore.Slot()
    def _finished(self):
        if self._worker is not None and self._worker.isRunning():
            QtCore.QTimer.singleShot(0, self._finished)
            return
        parked = self._worker is not None and self._worker.parked()
        coordinated = self._worker_retiring and (parked or self._worker.shutdown_completed())
        if not coordinated:
            # Draining may otherwise advance close and misclassify this exit.
            self._worker_failed = True
        self._drain()
        self._dismiss_wait()
        if not coordinated:
            self._timer.stop()
            self._fault("Worker ended unexpectedly. Inspect Status for the last confirmed CAD state.")
            if self._close_intent:
                self.cancel_close()
            self._render()
            return
        self._worker_retiring = False
        if not parked:
            worker, self._worker = self._worker, None
            worker.deleteLater()
        self._render()
        self._advance_close("worker_finished")

    def _release(self):
        if not self.can_release():
            return
        try:
            if not self._release_role():
                self.cancel_close()
                return
        except Exception as exc:
            self._fault("Role release failed. " + type(exc).__name__)
            self.cancel_close()
            return
        self._reset_views()
        self._clear_credentials()
        self._workbench_timer.stop()
        self._window.workbenchActivated.disconnect(self._workbench_changed)
        self.panel.visibilityChanged.disconnect(self._visibility_changed)
        self._window.removeEventFilter(self)
        self._window.removeDockWidget(self.panel)
        self.panel.deleteLater()
        self._window._reagent_source_controller = None
        self.deleteLater()

    def eventFilter(self, watched, event):
        if watched is self._window and event.type() == QtCore.QEvent.Close:
            if self._native_close_permit:
                self._native_close_permit = False
                return False
            event.ignore()
            self.request_close("exit")
            return True
        return False
