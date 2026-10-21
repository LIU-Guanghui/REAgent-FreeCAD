"""A user-readable reconstruction journal, independent of FreeCAD diagnostic logs."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import claim_artifacts, claim_fixed_artifacts
from .contract import ARTIFACT_NUMBER_LIMIT, artifact_filename
from .errors import PublicError


def _file_error(exc, stage, path):
    detail = {"stage": stage, "path": str(path), "exception_type": type(exc).__name__,
              "message": str(exc)[:500]}
    for key in ("errno", "winerror"):
        if getattr(exc, key, None) is not None:
            detail[key] = getattr(exc, key)
    return detail


class JournalRecorder:
    capacity_bytes = 8 * 1024 * 1024

    def __init__(self, task, artifacts):
        self.task = task
        self.artifacts = artifacts
        self._path_claimed = False
        self.events = []
        self.io_error = self.recording_error = None
        self.sequence = 0
        self.persisted_sequence = self.persisted_event_count = 0
        self.dropped_events = 0
        self.capacity_reached = False
        self.start_failed = False
        self.residual_files = []
        self.cleanup_errors = []

    @property
    def path(self):
        return Path(self.artifacts["journal_path"])

    @property
    def complete(self):
        return (self._path_claimed and self.sequence > 0 and not self.dropped_events
                and self.recording_error is None and self.io_error is None
                and not self.residual_files and self.persisted_sequence == self.sequence)

    def _payload(self):
        return json.dumps({"task": self.task, "artifacts": self.artifacts,
                           "recording": {"latest_sequence": self.sequence, "dropped_events": self.dropped_events,
                                         "complete": not self.dropped_events and self.recording_error is None},
                           "events": self.events}, ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    def _append(self, event, *, function, summary, level="info", role="target", duration_ms=None):
        self.sequence += 1
        if self.capacity_reached:
            self.dropped_events += 1
            raise ValueError("User journal capacity exceeded")
        record = None
        try:
            record = {"sequence": self.sequence, "time": datetime.now(timezone.utc).isoformat(),
                      "task": self.task, "level": level, "role": role, "event": event,
                      "summary": {"function": function, **summary}}
            if duration_ms is not None:
                record["duration_ms"] = duration_ms
            json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.events.append(record)
            if len(self._payload().encode("utf-8")) > self.capacity_bytes:
                self.capacity_reached = True
                raise ValueError("User journal capacity exceeded")
        except Exception as exc:
            if self.events and self.events[-1] is record:
                self.events.pop()
            self.dropped_events += 1
            self.recording_error = _file_error(exc, "journal_record", self.path)
            raise
        return record

    def emit(self, event, *, persist=True, **values):
        try:
            record = self._append(event, **values)
        except Exception:
            return None
        if persist and self._path_claimed and self.io_error is None and not self.start_failed and not self.residual_files:
            try:
                self._write()
            except Exception:
                pass  # CAD results remain separate; metadata blocks the next write.
        return record

    def _note_io_failure(self, exc, stage, path, *, function):
        self.io_error = _file_error(exc, stage, path)
        self.emit("journal.write_failed", function=function, role="agent",
                  summary={"error": dict(self.io_error)}, level="error", persist=False)

    def _public_failure(self):
        detail = self.io_error or self.recording_error or {
            "stage": "journal_preflight", "path": str(self.path), "message": "Journal is incomplete"}
        failure = PublicError("journal_write_failed", detail["stage"], detail["message"])
        failure.error_class = "persistence"
        failure.file_error = dict(detail)
        return failure

    def record_start(self, *, function, summary, resume_path=None):
        """Strict startup gate; deliberately bypass WorkflowContext.emit."""
        stage = "journal_record"
        try:
            first = self._append("task.starting", function=function, summary=summary, role="agent")
            stage = "artifact_allocation"
            selected, reservation = claim_artifacts(self.artifacts, resume_path)
            self.artifacts.update(selected)
            self._path_claimed = True
            stage = "journal_claim_close"
            reservation.close()
            first["summary"]["artifacts"] = dict(self.artifacts)
            stage = "journal_write"
            self._write()
            if not self.complete:
                raise self._public_failure()
        except Exception as exc:
            self.start_failed = True
            if stage != "journal_record" and self.io_error is None:
                self._note_io_failure(exc, stage, getattr(exc, "filename", None) or self.path,
                                      function="JournalRecorder.record_start")
            if self._path_claimed and self.persisted_sequence == 0:
                try:
                    self.path.unlink()
                    self._path_claimed = False
                except OSError as cleanup_error:
                    self.cleanup_errors.append(_file_error(cleanup_error, "journal_claim_cleanup", self.path))
                    if str(self.path) not in self.residual_files:
                        self.residual_files.append(str(self.path))
            raise self._public_failure() from exc

    def _write(self):
        if self.residual_files:
            raise self._public_failure()
        stage, io_path = "journal_encode", self.path
        temporary = None
        failure = None
        try:
            payload = self._payload()
            stage = "journal_temp_create"
            for number in range(ARTIFACT_NUMBER_LIMIT + 1):
                io_path = self.path.parent / artifact_filename(self.artifacts["basename"], "journal_tmp", number)
                try:
                    stream = io_path.open("x", encoding="utf-8")
                except FileExistsError:
                    continue
                temporary = io_path
                break
            else:
                raise FileExistsError("Journal temporary names through _999 are occupied")
            with stream:
                stage = "journal_temp_write"
                stream.write(payload)
                stage = "journal_temp_close"
            stage, io_path = "journal_replace", self.path
            os.replace(temporary, self.path)
            self.persisted_sequence = self.sequence
            self.persisted_event_count = len(self.events)
            self.io_error = None
        except Exception as exc:
            failure = (exc, stage, io_path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as exc:
                    self.residual_files.append(str(temporary))
                    self.cleanup_errors.append(_file_error(exc, "journal_temp_cleanup", temporary))
                    if failure is None:
                        failure = (exc, "journal_temp_cleanup", temporary)
        if failure is not None:
            self._note_io_failure(*failure, function="JournalRecorder._write")
            raise self._public_failure() from failure[0]

    def flush(self):
        if self.residual_files:
            raise self._public_failure()
        if not self._path_claimed:
            stage = "journal_claim"
            try:
                reservation = claim_fixed_artifacts(self.artifacts)
                self._path_claimed = True
                stage = "journal_claim_close"
                reservation.close()
            except Exception as exc:
                self._note_io_failure(exc, stage, getattr(exc, "filename", None) or self.path,
                                      function="JournalRecorder.flush")
                raise self._public_failure() from exc
        self._write()
        self.start_failed = False
        return self.metadata()

    def read(self, after_sequence=0, limit=100):
        rows = [row for row in self.events if row["sequence"] > after_sequence]
        selected = rows[:limit]
        return {"task": self.task, "events": selected,
                "after_sequence": after_sequence,
                "next_sequence": selected[-1]["sequence"] if selected else after_sequence,
                "remaining_events": max(0, len(rows) - len(selected)), "journal": self.metadata()}

    def metadata(self):
        return {"path": str(self.path), "complete": self.complete,
                "recording_complete": not self.dropped_events and self.recording_error is None,
                "error": self.io_error or self.recording_error, "recording_error": self.recording_error,
                "latest_sequence": self.sequence, "persisted_sequence": self.persisted_sequence,
                "persisted_event_count": self.persisted_event_count, "path_claimed": self._path_claimed,
                "residual_files": list(self.residual_files),
                "cleanup_errors": list(self.cleanup_errors),
                "event_count": len(self.events), "dropped_events": self.dropped_events,
                "purpose": "user_reconstruction_process"}

    def note_failure(self, exc):
        self.sequence += 1
        self.dropped_events += 1
        self.recording_error = _file_error(exc, "journal_record", self.path)


def validation_reference(result, operation_id):
    provenance = result.get("validation_provenance") or {}
    native = result["native"]
    facts = {"native": {"state": native["state"],
                        "result_geometry_available": None if native["state"] == "unready"
                        else native["result_geometry"] is not None}}
    facts.update({name: {key: row[key] for key in ("status", "available", "reason", "applicability", "applicability_reason") if key in row}
                  for name, row in result.get("checks", {}).items()})
    audit = result.get("parameter_audit", {})
    if audit.get("status") != "not_requested":
        facts["parameter_audit"] = {"status": audit.get("status"),
                                    "restored": audit.get("restoration", {}).get("restored")}
    for name, row in facts.items():
        row.setdefault("applicability", "current" if provenance.get(name) else "unknown")
    states = {row["applicability"] for row in facts.values()}
    applicability = "stale" if "stale" in states else "current" if states == {"current"} else "unknown"
    return {"operation_id": operation_id, "scope": "target", "status": "recorded",
            "requested": result.get("requested", {}), "facts": facts, "applicability": applicability}


def invalidate_validation(reference, reason):
    if reference.get("status") != "recorded":
        return reference
    return {**reference, "applicability": "stale", "applicability_reason": reason,
            "facts": {name: {**row, "applicability": "stale", "applicability_reason": reason}
                      for name, row in reference.get("facts", {}).items()}}


def validation_applicability(reference, recorded_provenance, current_provenance):
    if reference.get("status") != "recorded" or reference.get("applicability") == "stale":
        return reference
    recorded_provenance = recorded_provenance or {}
    current_provenance = current_provenance or {}
    facts = {}
    for name, row in reference.get("facts", {}).items():
        recorded = recorded_provenance.get(name)
        if row.get("applicability") == "stale":
            facts[name] = row
            continue
        if not recorded or any(current_provenance.get(key) is None for key in recorded):
            facts[name] = {**row, "applicability": "unknown", "applicability_reason": "dependency_revision_unavailable"}
            continue
        same = all(current_provenance.get(key) == value for key, value in recorded.items())
        facts[name] = {**row, "applicability": "current" if same else "stale",
                      "applicability_reason": "same_dependency_revisions" if same else "dependency_revision_changed"}
    states = {row["applicability"] for row in facts.values()}
    applicability = "stale" if "stale" in states else "unknown" if "unknown" in states or not states else "current"
    return {**reference, "facts": facts, "applicability": applicability}
