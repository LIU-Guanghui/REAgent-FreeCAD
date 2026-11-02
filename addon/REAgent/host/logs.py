"""Bound-instance native log reads; no model access or background collection."""

import codecs
import os
import stat
import uuid
from collections import OrderedDict

import FreeCAD as App

from REAgent.host.process_state import PROCESS


def native_log_source():
    source = {"process_id": os.getpid(), "role": "target" if PROCESS.INSTANCE_ROLE == "rebuild" else "source",
              "path": None, "path_source": "FreeCAD.ConfigGet", "attribution": "instance_file_not_call"}
    try:
        enabled = App.ConfigGet("LoggingFile") == "1"
        path = App.ConfigGet("LoggingFileName")
        source["enabled"] = enabled
        if not enabled:
            source["status"] = "disabled"
        elif not path:
            source["status"] = "path_unavailable"
        elif not os.path.isabs(path):
            # FreeCAD may have changed cwd after opening a relative log at startup.
            source["status"] = "relative_path_unresolved"
        else:
            source.update(status="configured", path=os.path.normpath(path))
    except Exception as exc:
        source.update(status="configuration_unavailable", reason=str(exc)[:500])
    return source


class DiagnosticOperations:
    def _read_freecad_log(self, params):
        source = native_log_source()
        base = {"source": source, "available": False, "next_cursor": None,
                "shared_source": None, "shared_source_status": "peer_not_checked"}
        if source["status"] != "configured":
            return {**base, "status": source["status"]}
        if not hasattr(self, "_log_cursors"):
            self._log_cursors = OrderedDict()
        cursor = params.get("cursor")
        previous = self._log_cursors.get(cursor) if cursor else None
        if cursor and previous is None:
            return {**base, "status": "invalid_cursor", "reason": "Cursor expired, belongs to another instance, or predates restart; omit it for a fresh tail"}
        path = source["path"]
        limit = params.get("max_bytes", 16384)
        try:
            metadata = os.stat(path)
            if not stat.S_ISREG(metadata.st_mode):
                return {**base, "status": "not_regular_file"}
            with open(path, "rb") as stream:
                initial = os.fstat(stream.fileno())
                if not stat.S_ISREG(initial.st_mode):
                    return {**base, "status": "not_regular_file"}
                identity = (initial.st_dev, initial.st_ino)
                reset = None
                scanned = 0
                if previous:
                    if previous["path"] != path or previous["identity"] != identity:
                        reset = "replaced"
                    elif initial.st_size < previous["offset"]:
                        reset = "truncated"
                    else:
                        stream.seek(previous["offset"] - len(previous["anchor"]))
                        anchor = stream.read(len(previous["anchor"]))
                        scanned += len(anchor)
                        if anchor != previous["anchor"]:
                            reset = "changed_at_cursor"
                start = previous["offset"] if previous and not reset else max(0, initial.st_size-limit)
                leading_partial = False
                if start:
                    stream.seek(start-1)
                    leading_partial = stream.read(1) != b"\n"
                    scanned += 1
                stream.seek(start)
                raw = stream.read(min(limit, max(0, initial.st_size-start)))
                scanned += len(raw)
                decoder = codecs.getincrementaldecoder("utf-8")("replace")
                text = decoder.decode(raw, final=False)
                pending = decoder.getstate()[0]
                end = start + len(raw) - len(pending)
                replaced = False
                try:
                    codecs.getincrementaldecoder("utf-8")("strict").decode(raw, final=False)
                except UnicodeDecodeError:
                    replaced = True
                stream.seek(max(0, end-64))
                anchor = stream.read(min(64, end))
                scanned += len(anchor)
                final = os.fstat(stream.fileno())
                current = os.stat(path)
                stable = ((current.st_dev, current.st_ino) == identity and final.st_size >= end
                          and current.st_size >= end)
                consumed = len(raw)-len(pending)
                if consumed and not anchor.endswith(raw[:consumed][-min(64,consumed):]):
                    stable = False
                if final.st_size == initial.st_size and final.st_mtime_ns != initial.st_mtime_ns:
                    stable = False
                if stable:
                    token = "log-" + uuid.uuid4().hex
                    self._log_cursors[token] = {"path": path, "identity": identity, "offset": end, "anchor": anchor}
                    while len(self._log_cursors) > 128:
                        self._log_cursors.popitem(last=False)
                else:
                    token = None
                return {**base, "available": True, "status": "read" if stable else "changed_during_read",
                        "text": text, "encoding": "utf-8", "decode_replacement": replaced,
                        "incomplete_utf8_bytes": len(pending), "offset_start": start, "offset_end": end,
                        "payload_bytes": end-start, "scanned_bytes": scanned, "file_size_observed": initial.st_size,
                        "more_available": final.st_size > end, "tail_truncated": bool(start and (not previous or reset)),
                        "leading_partial_line": leading_partial, "trailing_partial_line": bool(text and not text.endswith("\n")),
                        "cursor_reset": reset, "next_cursor": token}
        except FileNotFoundError:
            return {**base, "status": "missing"}
        except PermissionError:
            return {**base, "status": "permission_denied"}
        except OSError as exc:
            return {**base, "status": "read_failed", "reason": str(exc)[:500]}

    def contribute(self):
        return {'agent_read_freecad_log': self._read_freecad_log}
