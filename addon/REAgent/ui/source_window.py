"""Main-window owner of one task, worker, presentation and cooperative shutdown."""

import hashlib
import json
import uuid
from datetime import datetime
from enum import Enum

import FreeCADGui as Gui

from REAgent.ui import QtCore, QtWidgets
from REAgent.application.control import (
    FIXED_TOOLS,
    can_save_snapshot,
    mode_controls,
    project_close_terminal,
    project_completion,
)
from REAgent.application.presentation import (
    assemble_details, prepare_comparison, preview_entries, status_text, tool_message,
)
from REAgent.application.profiles import load_model_config, saved_settings, semantic_inputs
from REAgent.application.session import WORKFLOWS, assert_source_release, normalize_objective
from REAgent.json_values import to_json_value
from REAgent.ui.panel import Panel
from REAgent.ui.viewers import ImageViewer, TaskViewer
from REAgent.ui.comparison import ComparisonDialog, comparison_text
from REAgent.ui.settings import SettingsDialog
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
        self._active_key_hash = None
        self._pending_key_hash = None
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
        self._task_prefix = uuid.uuid4().hex
        self._presentation_task = 0
        self._action_number = None
        self._completions = {}
        self._task_close_terminals = {}
        self._pending_followup = None
        self._auto_opened_fixed_task = None
        self._stop_scheduled = False
        self._before_native_close = None
        self._on_stop_aborted = None
        self._cli_exit_requested = False
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

    def can_release(self):
        return (not self._busy and not self._task_open and not self._snapshot.get("controller")
                and (self._worker is None or not self._worker.isRunning())
                and not self._worker_retiring and not self._worker_failed)

    def assert_release(self):
        assert_source_release()

    def notice(self, message):
        self._status_notice = message
        self.panel.status.setToolTip(message)

    def details(self):
        snapshot = (
            self._worker.snapshot()
            if self._busy and self._worker is not None and not self._worker_failed
            else self._snapshot
        )
        return assemble_details(
            snapshot,
            task_open=self._task_open,
            results=self._results,
            progress=self._progress,
            snapshot_received_at=self._snapshot_received_at,
            fallback_config=self._config,
        )

    @staticmethod
    def _key_hash(credentials):
        api_key = credentials.get("api_key") if isinstance(credentials, dict) else None
        if not isinstance(api_key, str) or not api_key:
            return None
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    def _restore_saved_credentials(self):
        if self._snapshot.get("model_configured"):
            return True
        if self._config is not None and self._draft_credentials is not None:
            return True
        if self._config is not None and self._profile_name:
            try:
                config, credentials, profile_name = load_model_config(
                    {"profile": self._profile_name, **self._config},
                    self._read_settings,
                )
                self._config = config
                self._draft_credentials = credentials
                self._profile_name = profile_name
                return True
            except ValueError:
                pass
        try:
            saved = saved_settings(self._read_settings)
        except ValueError:
            saved = None
        if saved is not None and saved[1] is not None:
            self._config, self._draft_credentials, self._profile_name = saved
            return True
        return bool(self._config is not None and self._draft_credentials is not None)

    def _task_id(self, presentation_task=None):
        number = self._presentation_task if presentation_task is None else presentation_task
        return f"{self._task_prefix}-{number}"

    def _ensure_worker(self):
        if self._worker is None:
            self._worker = Worker(self)
            self._worker.ready.connect(self._drain, QtCore.Qt.QueuedConnection)
            self._worker.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
            self._worker.start()
        elif not self._worker.isRunning():
            self._worker.reactivate()
        return self._worker

    def new_task(self):
        if (self._task_open or not self._can_act() or self._snapshot.get("controller")
                or self._snapshot.get("mode") == "chat"):
            self.notice("End the current task before starting another.")
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
            if not self._snapshot.get("model_configured") and not self._restore_saved_credentials():
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
            return self.notice("Wait for the current action before changing settings.")
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
                self.notice("Model settings ready." + (" API key is available for this session only." if dialog.session_key_only else ""))
        else:
            dialog.deleteLater()
        self._render()

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

    def start_task(self, kind, task, objective=None, *, expected_target=None):
        if not self._task_open or self._snapshot.get("controller") or self._snapshot.get("mode") == "fixed":
            self.notice("Finish the current action or task before starting another.")
            return False
        objective = normalize_objective(kind, objective)
        if not self._snapshot.get("model_configured") and not self._restore_saved_credentials():
            self.configure()
            if self._config is None or self._draft_credentials is None:
                return False
        if self.action("start_task", {"config": self._config, "credentials": self._draft_credentials,
                                      "kind": kind, "task": task, "objective": objective,
                                      "expected_target": expected_target}):
            self._reset_views()
            self._results = ()
            self._progress = None
            self.panel.reset_task()
            self._render()
            return True
        return False

    def action(self, action, arguments=None, *, closing=False):
        if not self._can_act(closing=closing):
            self.notice("The original worker is busy or closing; this action was not queued.")
            return False
        if action not in ("start_task", "chat", "configure", "advance", "compact", "close", "exec_fixed") and not self._snapshot.get("controller"):
            self.notice("There is no retained task for this action.")
            return False
        if self._worker is None and action not in ("start_task", "chat", "exec_fixed"):
            return False
        worker = self._ensure_worker()
        try:
            if arguments is not None and arguments.get("config") is not None:
                arguments = {**arguments, "config": dict(arguments["config"])}
            if action == "configure" or (action in ("start_task", "chat") and not self._snapshot.get("model_configured")):
                self._pending_key_hash = self._key_hash((arguments or {}).get("credentials"))
            self._action_number = worker.submit(action, arguments, presentation_task=self._presentation_task)
        except (ValueError, RuntimeError) as exc:
            if action == "configure" or (action in ("start_task", "chat") and not self._snapshot.get("model_configured")):
                self._pending_key_hash = None
            self.notice(str(exc))
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
        if self._pending_followup is not None:
            self._pending_followup["cancelled"] = True
        if self._worker is not None:
            self._worker.pause()
            self._invalidate_previews()
            self.notice("Pause requested. The same worker will collect any in-flight result.")
            self._render()

    def supplement(self, text):
        if (not self._can_act(allow_busy=True)
                or not (self._snapshot.get("controller") or self._snapshot.get("mode") == "chat")):
            self.notice("A correction needs the current live task outside shutdown.")
            return False
        try:
            self._worker.supplement(text)
        except (ValueError, RuntimeError) as exc:
            self.notice(str(exc))
            return False
        self._invalidate_previews()
        facts = self._snapshot.get("controller") or {}
        if facts.get("target_writes_blocked"):
            self.notice("Change request queued. Target writes are blocked. Inspect Status for the cause.")
        else:
            self.notice("Message queued; processing stays paused." if self._snapshot.get("pause", {}).get("manual") else
                         "Message queued. The current operation will finish before the Target conversation is processed.")
        self._render()
        return True

    def compare(self):
        facts = self._snapshot.get("controller") or {}
        dialog = ComparisonDialog(self.panel, facts, geometry=WORKFLOWS[facts["workflow"]].compares_stp)
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

    def request_close(self, intent):
        self._advance_close("request", intent=CloseIntent(intent))

    def _store_task_close_terminal(self, task_id, payload):
        if not task_id:
            return
        self._task_close_terminals[task_id] = to_json_value(payload)
        while len(self._task_close_terminals) > 64:
            self._task_close_terminals.pop(next(iter(self._task_close_terminals)))

    def cancel_close(self):
        if self._pending_followup is not None:
            self._pending_followup["cancelled"] = True
        if self._close_intent is CloseIntent.TASK and self._presentation_task > 0:
            tid = self._task_id()
            has_session = bool(self._snapshot.get("controller") or self._snapshot.get("mode") in ("chat", "fixed"))
            self._store_task_close_terminal(
                tid,
                {
                    "action_status": "completed",
                    "ok": False,
                    "task_id": tid,
                    "ended": False,
                    "session_retained": has_session,
                    "task_open": self._task_open,
                    "error": {"code": "close_cancelled", "message": "Task close was cancelled"},
                },
            )
        self._stop_scheduled = False
        self._cli_exit_requested = False
        self._before_native_close = None
        abort_cb, self._on_stop_aborted = self._on_stop_aborted, None
        if abort_cb is not None:
            abort_cb()
        self._advance_close("cancel")
        self._timer.stop()
        if self._dialog is not None:
            dialog, self._dialog = self._dialog, None
            self._dialog_wait = False
            dialog.close()
            dialog.deleteLater()
        self.pause()
        self.notice("Close intent cancelled. Already dispatched work still returns; no model continuation was requested.")
        self._render()

    def eventFilter(self, watched, event):
        if watched is self._window and event.type() == QtCore.QEvent.Close:
            if self._native_close_permit:
                self._native_close_permit = False
                return False
            event.ignore()
            self.request_close("exit")
            return True
        return False

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
        return can_save_snapshot(self._snapshot)

    def _can_act(self, *, allow_busy=False, closing=False):
        return not ((self._busy and not allow_busy) or self._worker_failed or self._worker_retiring
                    or self._stop_scheduled or self._input_dialog is not None or (self._close_intent and not closing))

    def _mode_controls(self):
        facts = self._snapshot.get("controller") or {}
        workflow = WORKFLOWS.get(facts.get("workflow"))
        return mode_controls(
            snapshot=self._snapshot,
            task_open=self._task_open,
            busy=self._busy,
            worker_failed=self._worker_failed,
            worker_retiring=self._worker_retiring,
            input_dialog_open=self._input_dialog is not None,
            close_intent_active=(self._close_intent is not None or self._stop_scheduled),
            has_journal_cache=bool(self._journal_cache),
            has_image_entries=bool(self._image_entries),
            workflow_names=tuple(WORKFLOWS),
            paired_views=bool(workflow and workflow.paired_views),
        )

    def _render(self):
        facts = self._snapshot.get("controller") or {}
        active = bool(facts)
        writable = active and facts.get("session_status") == "ready" and not facts.get("target_writes_blocked")
        enabled = self._mode_controls()
        self.panel.set_controls(enabled, can_new_task=enabled["new_task"], can_send=enabled["send"])
        self.panel.images.setToolTip("Task is ending." if self._close_intent else
            "Close the input form first." if self._input_dialog is not None else
            "No task images yet." if not self._image_entries else
            "Browse all %d task images." % len(self._image_entries))
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

    def _clear_credentials(self):
        if self._draft_credentials is not None:
            self._draft_credentials.clear()
            self._draft_credentials = None
        if self._credential_dialog is not None:
            self._credential_dialog.clear_credentials()
            self._credential_dialog.deleteLater()
            self._credential_dialog = None

    def _invalidate_previews(self):
        self._snapshot = self._worker.snapshot()
        self._obsolete_generation = self._snapshot["pause"].get("generation", -1)
        self.panel.text.discard_previews()

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
            self._viewer.update_entries(sorted(self._image_entries.values(), key=lambda entry: "input" not in entry))
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
            self.notice("Model saved to " + str(value["saved_to"]))
        self._register_images(event["images"], value, event.get("session_id"))

    def _receive_state(self, snapshot):
        self._snapshot = snapshot
        self._snapshot_received_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        facts = snapshot.get("controller") or {}
        if facts.get("session_id"):
            self._presentation_session = facts["session_id"]
            self._task_artifacts = facts.get("artifacts") or self._task_artifacts
            for item in facts.get("reference_images", []):
                image_id = "reference-" + str(item["index"])
                self._image_entries.setdefault((self._presentation_session, image_id),
                    {"image_id": image_id, "label": item["filename"] + " · Source", "input": item})
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
            if event.get("mode") == "fixed":
                card_key = ("fixed_tool", event["presentation_task"], event["action_number"])
                self.panel.message("progress", tool_message(event), request=card_key, complete=True)
            self._present_result(event)
            self._results = ({**event, "value": event["result"]},)
            self._progress = {**event, "images": ()}
        elif kind == "tool_started":
            if event.get("mode") == "fixed":
                card_key = ("fixed_tool", event["presentation_task"], event["action_number"])
                self.panel.message("progress", tool_message(event, started=True), request=card_key)
            self._progress = {key: value for key, value in event.items() if key not in ("event", "images")}
        elif kind == "tool_progress":
            self._progress = {key: value for key, value in event.items() if key not in ("event", "images")}
        elif kind == "error":
            key = ("terminal", event.get("request_number"), event["code"])
            journal_codes = ("model_journal_pending", "context_journal_pending", "journal_pending")
            if key not in self._presented and not (self._journal_fault and event["code"] in journal_codes):
                self._presented.add(key)
                self._model_error(event)

    def _report_presentation_error(self, context, exc):
        message = f"Presentation update failed ({context}): {type(exc).__name__}: {str(exc)[:300]}"
        try:
            self._fault(message)
        except Exception:
            pass

    @QtCore.Slot()
    def _drain(self):
        if self._worker is None:
            return
        events, completion = self._worker.drain()
        for event in events:
            if event.get("event") == "journal_update":
                try:
                    self._receive_event(event)
                except Exception as exc:
                    self._report_presentation_error("journal_update", exc)
        for event in events:
            if event.get("event") != "journal_update":
                try:
                    self._receive_event(event)
                except Exception as exc:
                    self._report_presentation_error(event.get("event", "event"), exc)
        if completion is not None:
            self._busy = False
            self._action = None
            recorded = False
            try:
                try:
                    self._receive_state(completion["snapshot"])
                except Exception as exc:
                    self._snapshot = completion["snapshot"]
                    self._report_presentation_error("state", exc)
                for event in completion["presentation_fallback"]:
                    try:
                        self._receive_event(event)
                    except Exception as exc:
                        self._report_presentation_error(event.get("event", "fallback"), exc)
                try:
                    self.panel.text.discard_previews()
                except Exception as exc:
                    self._report_presentation_error("discard_previews", exc)
                if completion["action"] == "configure":
                    if completion["ok"]:
                        self._config = self._pending_config
                        self._profile_name = self._pending_profile
                        self._active_key_hash = self._pending_key_hash
                        self.notice("Model settings applied." + (" API key is available for this session only."
                                     if self._credential_dialog is not None and self._credential_dialog.session_key_only else ""))
                    self._pending_config = None
                    self._pending_profile = None
                    self._pending_key_hash = None
                elif completion["action"] in ("start_task", "chat"):
                    if self._snapshot.get("model_configured") and self._pending_key_hash is not None:
                        self._active_key_hash = self._pending_key_hash
                    self._pending_key_hash = None
                if not self._snapshot.get("model_configured"):
                    self._active_key_hash = None
                if completion["new_results"]:
                    self._results = completion["results"]
                    for row in self._results:
                        try:
                            self._present_result({**row, "result": row["value"], "session_id": self._presentation_session})
                        except Exception as exc:
                            self._report_presentation_error("tool_result", exc)
                for row in completion.get("comparison_results", ()):
                    try:
                        self._present_result({**row, "result": row["value"], "session_id": self._presentation_session})
                    except Exception as exc:
                        self._report_presentation_error("comparison_result", exc)
                comparison = completion.get("comparison")
                if comparison is not None:
                    changed = comparison.get("comparison_id") != (self._comparison or {}).get("comparison_id")
                    self._comparison = comparison
                    if changed and not self._close_intent:
                        try:
                            self.open_viewer("comparison")
                        except Exception as exc:
                            self._report_presentation_error("comparison", exc)
                if completion["credentials_consumed"]:
                    self._clear_credentials()
                if completion["error"]:
                    try:
                        self._model_error(completion["error"])
                    except Exception as exc:
                        self._report_presentation_error("error", exc)
                if completion["action"] == "close":
                    final = completion["results"][-1]["value"] if completion["new_results"] else {}
                    self._task_ended(final.get("journal"))
                    if self._snapshot.get("controller"):
                        self.notice("Task ended locally. Use End task to retry pending cleanup.")
                    else:
                        self.notice("Task ended. Surviving documents remain open.")
                elif completion["action"] == "exec_fixed":
                    fn = completion.get("command")
                    if self._task_open and not self._snapshot.get("controller"):
                        final = completion["results"][-1]["value"] if completion["new_results"] else {}
                        if fn in ("agent_close_session", "agent_manage_journal"):
                            self._task_ended(final.get("journal"))
                        elif (
                            fn == "agent_open_session"
                            and self._auto_opened_fixed_task == completion.get("presentation_task")
                        ):
                            self._task_ended(final.get("journal"))
                    if fn == "agent_open_session":
                        self._auto_opened_fixed_task = None
                if completion["presentation_backlog"]:
                    self.notice("Paused after presentation backlog. Inspect retained results in Status.")
                if completion["paused_before_start"]:
                    self.notice("Paused before this action began.")
                self._record_completion(completion)
                recorded = True
            finally:
                if not recorded:
                    self._record_completion(completion)
                self._dismiss_wait()
                if self._close_intent and completion["action"] == "close":
                    self._advance_close("task_completed")
        if completion is not None or events:
            try:
                self._render()
            except Exception as exc:
                self._report_presentation_error("render", exc)
        self._advance_close("progress")

    def _dismiss_wait(self):
        if self._dialog_wait:
            dialog, self._dialog = self._dialog, None
            self._dialog_wait = False
            dialog.close()
            dialog.deleteLater()

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
            closing_tid = self._task_id() if self._presentation_task > 0 else None
            if self._task_open:
                self._task_ended()
            if step == "task":
                self._timer.stop()
                self._render()
                if closing_tid and closing_tid not in self._task_close_terminals:
                    self._store_task_close_terminal(
                        closing_tid,
                        {
                            "action_status": "completed",
                            **project_close_terminal(
                                task_id=closing_tid,
                                snapshot=self._snapshot,
                                task_open=self._task_open,
                                journal_metadata=self._journal_cache.get("metadata"),
                            ),
                        },
                    )
            elif step == "release_pending":
                self.notice("Source or journal cleanup is pending. End task can retry; the main window can still exit.")
                self.cancel_close()
            elif step == "thread":
                self._worker_retiring = True
                try:
                    if self._close_intent is CloseIntent.EXIT and not self._cli_exit_requested:
                        self._worker.park()
                    else:
                        self._worker.shutdown()
                    self._timer.start(10000)
                except RuntimeError as exc:
                    self._worker_retiring = False
                    self.notice(str(exc))
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
        before_cb, self._before_native_close = self._before_native_close, None
        if before_cb is not None:
            try:
                before_cb()
            except Exception as exc:
                if not self._window_gone:
                    self.notice("Temp document cleanup failed: " + str(exc)[:300])
        metadata = self._journal_cache.get("metadata") or {}
        if metadata and not metadata.get("complete"):
            self.notice("The log is incomplete; some facts exist only in this process's memory.")
        self._native_close_permit = True
        accepted = False
        try:
            accepted = self._window.close()
        except RuntimeError as exc:
            if not self._window_gone:
                self.notice("Native window close failed: " + str(exc)[:300])
        finally:
            self._native_close_permit = False
            self._advance_close("native_returned")
        if self._cli_exit_requested or self._stop_scheduled:
            self._cli_exit_requested = False
            self._stop_scheduled = False
            abort_cb, self._on_stop_aborted = self._on_stop_aborted, None
            if accepted:
                try:
                    self._release_role()
                except Exception as exc:
                    if not self._window_gone:
                        self.notice("Bridge stop failed after close: " + str(exc)[:300])
            elif abort_cb is not None:
                abort_cb()
        if not self._window_gone:
            if not accepted:
                self.notice("Window close cancelled. The task remains ended; images and journal are available.")
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
            self._release_role()
        except Exception as exc:
            self._cli_exit_requested = False
            self._before_native_close = None
            abort_cb, self._on_stop_aborted = self._on_stop_aborted, None
            if abort_cb is not None:
                abort_cb()
            self._stop_scheduled = False
            self._fault(f"Role release failed. {exc}")
            self.cancel_close()
            return
        self._stop_scheduled = False
        self._cli_exit_requested = False
        self._before_native_close = None
        self._on_stop_aborted = None
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

    def _record_completion(self, completion):
        p_task = completion.get("presentation_task")
        task_id = None if not p_task else self._task_id(p_task)
        action_number = completion["action_number"]
        messages = [{"role": row.role, "text": row.text} for row in self.panel.text._rows[-32:]]
        entry = project_completion(
            completion,
            task_id=task_id,
            task_open=self._task_open,
            messages=messages,
            journal_metadata=self._journal_cache.get("metadata"),
        )
        if (
            self._pending_followup is not None
            and self._pending_followup["task_id"] == task_id
            and self._pending_followup["configure_action"] == action_number
            and completion["action"] == "configure"
        ):
            followup, self._pending_followup = self._pending_followup, None
            if followup.get("cancelled"):
                entry["ok"] = False
                entry["cancelled"] = True
                entry["followup_error"] = {
                    "code": "action_cancelled",
                    "message": f"{followup['kind']} was cancelled by pause or close before dispatch",
                }
            elif entry["ok"]:
                try:
                    kind, next_action = self._dispatch_followup(followup)
                    if kind == "queued":
                        entry["queued"] = True
                        entry["followup_queued"] = True
                        entry["followup_action"] = "supplement"
                    else:
                        entry["followup_action_number"] = next_action
                except Exception as exc:
                    entry["ok"] = False
                    entry["followup_error"] = {"code": "invalid_request", "message": str(exc)}
        self._completions[(task_id, action_number)] = entry
        if completion["action"] == "close" and task_id:
            self._store_task_close_terminal(task_id, {**entry, "action_status": "completed"})
        while len(self._completions) > 128:
            self._completions.pop(next(iter(self._completions)))

    def _dispatch_followup(self, followup):
        enabled = self._mode_controls()
        kind = followup["kind"]
        if kind == "start_task":
            wf = followup["workflow"]
            if not enabled.get(wf):
                raise ValueError("Task is no longer eligible for start_task after configure")
            if not self.start_task(wf, followup["task"], followup["objective"], expected_target=followup.get("expected_target")):
                raise ValueError("Failed to dispatch start_task after configure")
            self.panel.message("user", followup["objective"])
            return "dispatched", self._action_number
        if kind == "send":
            if not enabled.get("send"):
                raise ValueError("Task is no longer eligible for send after configure")
            prev_action = self._action_number
            if not self.send(followup["text"]):
                raise ValueError("Failed to dispatch send after configure")
            if self._action_number == prev_action:
                return "queued", None
            return "dispatched", self._action_number
        raise ValueError("Unknown followup action")

    def cli_stop_role(self, *, quit=False, before_native_close=None, on_stop_aborted=None):
        if self._dialog_wait:
            return {
                "stop_accepted": False,
                "waiting_user_or_in_flight": True,
                "role": "source",
                "last_confirmed_stage": "waiting_user_or_in_flight",
            }
        if self._stop_scheduled:
            raise RuntimeError("Role stop is already in progress")
        if (self._busy or self._task_open or self._snapshot.get("controller")
                or self._snapshot.get("mode") in ("chat", "fixed")):
            raise RuntimeError("Finish the active or retained task before releasing Source")
        if self._input_dialog is not None or self._close_intent is not None or self._worker_retiring or self._worker_failed:
            raise RuntimeError("Source window cannot be released in its current UI state")
        self.assert_release()
        self._stop_scheduled = True
        self._before_native_close = before_native_close
        self._on_stop_aborted = on_stop_aborted
        self._cli_exit_requested = bool(quit)
        self._render()

        def trigger():
            if not self._window_gone:
                self.request_close("exit" if self._cli_exit_requested else "release")
            else:
                self._stop_scheduled = False
                self._cli_exit_requested = False
                self._before_native_close = None
                abort_cb, self._on_stop_aborted = self._on_stop_aborted, None
                if abort_cb is not None:
                    abort_cb()

        QtCore.QTimer.singleShot(0, trigger)
        return {
            "stop_accepted": True,
            "role": "source",
            "quit_requested": bool(quit),
            "last_confirmed_stage": "accepted",
        }

    def cli_ui_action(self, params):
        action = params.get("action")
        task_id = params.get("task_id")
        args = params.get("args")
        args = {} if args is None else args
        if not isinstance(args, dict):
            raise ValueError("args must be a JSON object")
        model_config = params.get("model_config")
        expected_target = params.get("expected_target")
        if model_config is not None and action not in ("start_task", "send"):
            raise ValueError("model_config is only accepted by start_task or send")
        if expected_target is not None and action != "start_task":
            raise ValueError("expected_target is only accepted by start_task")

        has_retained = bool(self._snapshot.get("controller")) or self._snapshot.get("mode") in ("chat", "fixed")
        current_task_id = self._task_id() if (self._task_open or has_retained) and self._presentation_task > 0 else None
        if action == "status":
            if task_id is not None and task_id != self._task_id():
                raise ValueError("task_id does not match the active or recent task")
            details = self.details()
            return to_json_value({
                "task_id": current_task_id,
                "task_open": self._task_open,
                "details": details,
                "status_text": status_text(details),
            })

        if self._stop_scheduled:
            raise ValueError("Source role stop is already in progress")

        enabled = self._mode_controls()
        if action == "new_task":
            if task_id is not None:
                raise ValueError("new_task creates a new task_id and must not receive task_id")
            if not enabled["new_task"] or not self.new_task():
                raise ValueError("End the current task before starting another")
            return {
                "accepted": True,
                "task_id": self._task_id(),
                "action_number": None,
                "task_open": True,
                "status": "Ready",
            }

        if not self._task_open and not has_retained:
            raise ValueError("No active task; call new_task first")
        if not isinstance(task_id, str) or task_id != self._task_id():
            raise ValueError("task_id is required and must match the open task")

        if action == "end_task":
            if not self._can_act(allow_busy=True, closing=True) or self._worker_failed:
                raise ValueError("Task cannot be ended in the current worker state")
            empty_task = (not self._snapshot.get("controller")
                          and self._snapshot.get("mode") not in ("chat", "fixed")
                          and not self._busy)
            self._task_close_terminals.pop(task_id, None)
            prev_action = self._action_number
            self.request_close("task")
            if empty_task:
                terminal = project_close_terminal(
                    task_id=task_id,
                    snapshot=self._snapshot,
                    task_open=self._task_open,
                    journal_metadata=self._journal_cache.get("metadata"),
                )
                self._store_task_close_terminal(task_id, {"action_status": "completed", **terminal})
                return {
                    "accepted": True,
                    "completed": True,
                    **terminal,
                    "action_number": None,
                }
            return {
                "accepted": True,
                "completed": False,
                "task_id": task_id,
                "action_number": self._action_number if self._action_number != prev_action else None,
                "close_phase": self._close_phase.value if self._close_phase else None,
            }

        if action == "pause":
            target_action = self._action_number if self._busy else None
            self.pause()
            return to_json_value({
                "accepted": True,
                "task_id": task_id,
                "action_number": target_action,
                "busy": self._busy,
                "snapshot": self._snapshot,
            })

        if self._snapshot.get("mode") == "fixed":
            raise ValueError(f"{action} is disabled in fixed command mode")

        if action == "compact":
            if not enabled["compact"] or not self.action("compact"):
                raise ValueError("Context compaction is unavailable in the current task state")
            return {"accepted": True, "task_id": task_id, "action_number": self._action_number}

        if action == "save":
            if not enabled["save"] or not self.action("save"):
                raise ValueError("Save is unavailable in the current task state")
            return {"accepted": True, "task_id": task_id, "action_number": self._action_number}

        if action == "compare":
            if not enabled["compare"]:
                raise ValueError("Manual comparison is unavailable in the current task state")
            unknown_keys = set(args) - {"options", "view_request"}
            if unknown_keys:
                raise ValueError("Unknown compare arguments: " + ", ".join(sorted(unknown_keys)))
            options, view_request = prepare_comparison(args.get("options"), args.get("view_request"))
            facts = self._snapshot.get("controller") or {}
            if options is not None and not WORKFLOWS[facts["workflow"]].compares_stp:
                raise ValueError("STL comparison uses paired views")
            if not self.action("compare", {"options": options, "view_request": view_request}):
                raise ValueError("Failed to queue comparison action")
            return {"accepted": True, "task_id": task_id, "action_number": self._action_number}

        if action == "start_task":
            wf = args.get("workflow")
            if wf not in WORKFLOWS:
                raise ValueError("Unknown or missing workflow: " + str(wf))
            if not enabled.get(wf):
                raise ValueError("Finish the current action or task before starting another")
            allowed_keys = {"workflow", "output_dir", "objective", "source_path", "paths",
                            "document", "observation_policy", "linear_tolerance_mm", "angular_tolerance_deg"}
            extra_keys = set(args) - allowed_keys
            if extra_keys:
                raise ValueError("Unknown start_task arguments: " + ", ".join(sorted(extra_keys)))
            output_dir = args.get("output_dir")
            if not isinstance(output_dir, str) or not output_dir.strip():
                raise ValueError("output_dir is required")
            objective = normalize_objective(wf, args.get("objective"))
            task_args = {k: v for k, v in args.items() if k not in ("workflow", "objective")}
            if model_config is not None:
                config, credentials, profile_name = load_model_config(model_config, self._read_settings)
                try:
                    if self._snapshot.get("model_configured"):
                        same_model = (
                            config == self._config
                            and profile_name == self._profile_name
                            and self._key_hash(credentials) == self._active_key_hash
                        )
                        if same_model:
                            credentials.clear()
                        else:
                            if not self.action("configure", {"config": config, "credentials": credentials}):
                                raise ValueError("Failed to queue model configuration update")
                            self._pending_config = config
                            self._pending_profile = profile_name
                            self._pending_followup = {
                                "task_id": task_id,
                                "configure_action": self._action_number,
                                "kind": "start_task",
                                "workflow": wf,
                                "task": task_args,
                                "objective": objective,
                                "expected_target": expected_target,
                            }
                            return {"accepted": True, "task_id": task_id, "action_number": self._action_number}
                    else:
                        self._clear_credentials()
                        self._config = config
                        self._profile_name = profile_name
                        self._draft_credentials = credentials
                except Exception:
                    credentials.clear()
                    raise
            elif not self._snapshot.get("model_configured") and not self._restore_saved_credentials():
                raise ValueError("Model is not configured; select a saved profile or pass model.json")
            if not self.start_task(wf, task_args, objective, expected_target=expected_target):
                raise ValueError("Failed to start task")
            self.panel.message("user", objective)
            return {"accepted": True, "task_id": task_id, "action_number": self._action_number}

        if action == "send":
            if not enabled["send"]:
                raise ValueError("Send is unavailable in the current task state")
            text = args.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Message text must be non-empty")
            if model_config is not None:
                config, credentials, profile_name = load_model_config(model_config, self._read_settings)
                try:
                    if self._snapshot.get("controller") and semantic_inputs(config) != semantic_inputs(self._config):
                        raise ValueError("An active CAD task keeps its original model settings; pause to update its key")
                    if self._snapshot.get("model_configured"):
                        same_model = (
                            config == self._config
                            and profile_name == self._profile_name
                            and self._key_hash(credentials) == self._active_key_hash
                        )
                        if same_model:
                            credentials.clear()
                        elif self._busy:
                            if semantic_inputs(config) != semantic_inputs(self._config):
                                raise ValueError("Cannot switch models while the worker is busy")
                            raise ValueError("Cannot update model credentials while the worker is busy")
                        else:
                            if not self.action("configure", {"config": config, "credentials": credentials}):
                                raise ValueError("Failed to queue model configuration update")
                            self._pending_config = config
                            self._pending_profile = profile_name
                            self._pending_followup = {
                                "task_id": task_id,
                                "configure_action": self._action_number,
                                "kind": "send",
                                "text": text,
                            }
                            return {"accepted": True, "task_id": task_id, "action_number": self._action_number}
                    else:
                        self._clear_credentials()
                        self._config = config
                        self._profile_name = profile_name
                        self._draft_credentials = credentials
                except Exception:
                    credentials.clear()
                    raise
            elif not self._snapshot.get("model_configured") and not self._restore_saved_credentials():
                raise ValueError("Model is not configured; select a saved profile or pass model.json")
            prev_action = self._action_number
            if not self.send(text):
                raise ValueError("Failed to dispatch message")
            if self._action_number == prev_action:
                return to_json_value({
                    "accepted": True,
                    "completed": True,
                    "queued": True,
                    "action": "supplement",
                    "task_id": task_id,
                    "action_number": None,
                    "busy": self._busy,
                    "snapshot": self._snapshot,
                })
            return {"accepted": True, "task_id": task_id, "action_number": self._action_number}

        raise ValueError("Unknown cli_ui_action: " + str(action))

    def cli_ui_wait(self, params):
        task_id = params.get("task_id")
        action_number = params.get("action_number")
        if action_number is not None:
            key = (task_id, action_number)
            entry = self._completions.get(key)
            if entry is not None:
                followup_num = entry.get("followup_action_number")
                if followup_num is not None:
                    followup_entry = self._completions.get((task_id, followup_num))
                    if followup_entry is not None:
                        return to_json_value(followup_entry)
                    if self._worker_failed:
                        return to_json_value({
                            "action_status": "completed",
                            "ok": False,
                            "task_id": task_id,
                            "action_number": followup_num,
                            "error": {"code": "worker_failed", "message": "Source worker exited unexpectedly"},
                            "snapshot": self._snapshot,
                        })
                    return {
                        "action_status": "waiting_user_or_in_flight" if self._dialog_wait else "in_flight",
                        "task_id": task_id,
                        "action_number": followup_num,
                        "waiting_user": bool(self._dialog_wait),
                        "busy": self._busy,
                        "snapshot": to_json_value(self._snapshot),
                    }
                return to_json_value(entry)
            if self._worker_failed:
                return to_json_value({
                    "action_status": "completed",
                    "ok": False,
                    "task_id": task_id,
                    "action_number": action_number,
                    "error": {"code": "worker_failed", "message": "Source worker exited unexpectedly"},
                    "snapshot": self._snapshot,
                })
            matches_current = (
                (task_id is None and self._action == "exec_fixed")
                or (task_id is not None and task_id == self._task_id())
            )
            if not matches_current or action_number != self._action_number:
                raise ValueError("Unknown task_id or action_number for cli_ui_wait")
            if self._dialog_wait:
                return {
                    "action_status": "waiting_user_or_in_flight",
                    "task_id": task_id,
                    "action_number": action_number,
                    "waiting_user": True,
                    "close_phase": self._close_phase.value if self._close_phase else None,
                    "busy": self._busy,
                    "snapshot": to_json_value(self._snapshot),
                }
            if self._busy:
                return {
                    "action_status": "in_flight",
                    "task_id": task_id,
                    "action_number": action_number,
                    "busy": True,
                    "snapshot": to_json_value(self._snapshot),
                }
            raise ValueError("No matching in-flight or completed action")

        if (
            not isinstance(task_id, str)
            or not task_id
            or (
                task_id not in self._task_close_terminals
                and task_id != self._task_id()
                and not any(k[0] == task_id for k in self._completions)
            )
        ):
            raise ValueError("Unknown task_id for cli_ui_wait")
        stored_terminal = self._task_close_terminals.get(task_id)
        if stored_terminal is not None:
            return to_json_value(stored_terminal)
        if task_id != self._task_id():
            for (k_task, _k_num), row in reversed(list(self._completions.items())):
                if k_task == task_id and row.get("action") == "close":
                    return to_json_value({**row, "action_status": "completed"})
            raise ValueError("Close terminal is unavailable for superseded task_id")
        if self._worker_failed:
            return project_close_terminal(
                task_id=task_id,
                snapshot=self._snapshot,
                task_open=self._task_open,
                journal_metadata=self._journal_cache.get("metadata"),
                worker_failed=True,
            )
        if self._dialog_wait:
            return {
                "action_status": "waiting_user_or_in_flight",
                "task_id": task_id,
                "waiting_user": True,
                "close_phase": self._close_phase.value if self._close_phase else None,
                "busy": self._busy,
                "snapshot": to_json_value(self._snapshot),
            }
        if self._close_intent is not None or self._busy:
            return {
                "action_status": "in_flight",
                "task_id": task_id,
                "close_phase": self._close_phase.value if self._close_phase else None,
                "busy": self._busy,
                "snapshot": to_json_value(self._snapshot),
            }
        last_close = None
        for (k_task, _k_num), row in reversed(list(self._completions.items())):
            if k_task == task_id and row.get("results") and row["results"][-1].get("function") == "agent_close_session":
                last_close = row["results"][-1]["value"]
                break
        if last_close is None and self._results and self._results[-1].get("function") == "agent_close_session":
            last_close = self._results[-1]["value"]
        return project_close_terminal(
            task_id=task_id,
            snapshot=self._snapshot,
            task_open=self._task_open,
            last_close=last_close,
            journal_metadata=self._journal_cache.get("metadata"),
            worker_failed=False,
        )

    def cli_exec_command(self, params):
        command = params.get("command")
        if command not in FIXED_TOOLS:
            raise ValueError("Unknown or unsupported fixed command: " + str(command))
        if not self._can_act() or self._close_intent is not None:
            raise ValueError("Source worker is busy, closing or blocked by an open dialog")
        mode = self._snapshot.get("mode")
        if mode in ("chat", "cad") or (mode != "fixed" and bool(self._snapshot.get("controller"))):
            raise ValueError("Finish the active model session before running fixed CAD commands")
        task_id = params.get("task_id")
        workflow = params.get("workflow")
        if workflow is not None and workflow not in WORKFLOWS:
            raise ValueError("Unknown CAD workflow: " + str(workflow))
        expected_target = params.get("expected_target")
        if expected_target is not None and command != "agent_open_session":
            raise ValueError("expected_target is only accepted by agent_open_session")
        args = params.get("args")
        args = {} if args is None else args
        if not isinstance(args, dict):
            raise ValueError("Command args must be a JSON object")

        has_fixed_session = mode == "fixed" and bool(self._snapshot.get("controller"))
        if command == "agent_ping" and not has_fixed_session:
            if task_id is not None and (not self._task_open or task_id != self._task_id()):
                raise ValueError("task_id does not match an open blank task")
            p_task = self._presentation_task if (task_id is not None and self._task_open) else 0
            if self._worker is None:
                self._completions = {k: v for k, v in self._completions.items() if k[0] is not None}
            worker = self._ensure_worker()
            self._action_number = worker.submit(
                "exec_fixed",
                {"command": "agent_ping", "workflow": workflow or "stp2cad", "args": {}},
                presentation_task=p_task,
            )
            self._busy = True
            self._action = "exec_fixed"
            self._presented.clear()
            self._status_notice = None
            self._render()
            return {"accepted": True, "task_id": task_id, "action_number": self._action_number}

        if command == "agent_open_session":
            if has_fixed_session or self._snapshot.get("controller"):
                raise ValueError("Finish the retained task before opening another session")
            if workflow is None:
                raise ValueError("workflow is required for agent_open_session")
            auto_opened = False
            if self._task_open:
                if not isinstance(task_id, str) or task_id != self._task_id():
                    raise ValueError("An open task already exists; pass its matching task_id or end it first")
            else:
                if task_id is not None:
                    raise ValueError("task_id must be omitted when opening a new fixed task")
                if not self.new_task():
                    raise ValueError("Cannot open a new fixed task in the current UI state")
                auto_opened = True
            task_id = self._task_id()
            self._reset_views()
            self._results = ()
            self._progress = None
            self._auto_opened_fixed_task = self._presentation_task if auto_opened else None
            if not self.action("exec_fixed", {
                "command": "agent_open_session",
                "workflow": workflow,
                "args": args,
                "expected_target": expected_target,
            }):
                if auto_opened:
                    self._task_ended()
                    self._auto_opened_fixed_task = None
                raise ValueError("Failed to queue agent_open_session")
            return {"accepted": True, "task_id": task_id, "action_number": self._action_number}

        if not has_fixed_session:
            raise ValueError("Open a fixed session first with agent_open_session")
        if task_id is not None and task_id != self._task_id():
            raise ValueError("task_id does not match the active fixed task")
        if not self.action("exec_fixed", {"command": command, "workflow": workflow, "args": args}):
            raise ValueError("Failed to queue fixed command")
        return {"accepted": True, "task_id": self._task_id(), "action_number": self._action_number}
