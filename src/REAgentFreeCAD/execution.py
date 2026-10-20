"""Serialized background execution with process-local outcome lookup."""

import asyncio
import json
import time
import uuid

from mcp.types import CallToolResult, TextContent

from .contract import validate_input_schema
from .errors import PublicError, error_payload
from .tools_schema import EXECUTION_POLICY, TOOLS


RESPONSE_WAIT_SECONDS = EXECUTION_POLICY["response_wait_seconds"]
RETAINED_OPERATIONS = EXECUTION_POLICY["retained_operations"]


def _response(value, *, error=False, images=()):
    return CallToolResult(isError=error, content=[
        TextContent(type="text", text=json.dumps(value, ensure_ascii=False, allow_nan=False)),
        *images,
    ])


class Execution:
    """The worker owns workflow/CAD state; the event loop owns this registry."""

    def __init__(self, execute):
        self.execute = execute
        self.operations = {}
        self.active = None

    @property
    def busy(self):
        return self.active is not None

    def _snapshot(self, entry):
        return {
            "operation_id": entry["operation_id"],
            "function": entry["function"], "status": entry["status"],
            "elapsed_ms": ((entry.get("finished") or time.perf_counter()) - entry["started"]) * 1000,
            "progress": dict(entry["progress"]),
            "side_effect": entry["side_effect"] if entry["status"] == "completed" else "unknown",
            "scope": "current_mcp_process",
        }

    def _publish_progress(self, operation_id, progress):
        entry = self.operations.get(operation_id)
        if entry is not None and entry["status"] == "running":
            entry["progress"] = progress

    def _lookup(self, args):
        operation_id = args.get("operation_id")
        if operation_id is None:
            return _response({
                "operations": [self._snapshot(entry) for entry in self.operations.values()],
                "retained_limit": RETAINED_OPERATIONS,
                "scope": "current_mcp_process",
            })
        entry = self.operations.get(operation_id)
        if entry is None:
            raise PublicError("unknown_operation", "public_preflight",
                              "Operation is absent or evicted in this MCP process; this does not mean it did not execute. Do not repeat a lost write.")
        value = {"operation": self._snapshot(entry)}
        result = entry.get("result")
        if result is None:
            return _response(value)
        # Preserve the original text result and image order together. This does
        # not repeat the operation or attest that an earlier response arrived.
        value["result"] = json.loads(result.content[0].text)
        return _response(value, error=bool(result.isError), images=result.content[1:])

    def _finished(self, operation_id, task):
        entry = self.operations[operation_id]
        context = entry.pop("context")  # The worker has finished; no concurrent readers.
        entry["progress"] = dict(context.get("progress", {}))
        entry["side_effect"] = context["side_effect"]
        try:
            entry["result"] = task.result()
            value = json.loads(entry["result"].content[0].text)
            if isinstance(value, dict) and "side_effect" in value:
                entry["side_effect"] = value["side_effect"]
        except BaseException as exc:
            entry["side_effect"] = "unknown"
            entry["result"] = _response({
                "operation_id": operation_id, "code": "execution_interrupted",
                "message": str(exc)[:500], "side_effect": "unknown",
                "retry_action": "inspect_state",
            }, error=True)
        entry["status"] = "completed"
        entry["finished"] = time.perf_counter()
        entry.pop("task", None)
        self.active = None

    async def run(self, name, arguments):
        args = {} if arguments is None else arguments
        if name == "agent_get_state" and isinstance(args, dict) and "session_id" not in args:
            try:
                validate_input_schema(args, TOOLS[name]["input_schema"], path=name)
                return self._lookup(args)
            except Exception as exc:
                return _response(error_payload(exc), error=True)
        if self.busy:
            return _response({
                "code": "execution_busy", "stage": "public_preflight",
                "message": "A worker is still executing. Query agent_get_state with its operation_id; do not repeat the write.",
                "side_effect": "none", "active_operation": self._snapshot(self.operations[self.active]),
            }, error=True)
        while len(self.operations) >= RETAINED_OPERATIONS:
            del self.operations[next(iter(self.operations))]
        operation_id = "op-" + uuid.uuid4().hex[:20]
        context = {"operation_id": operation_id, "started": time.perf_counter(), "side_effect": "none"}
        loop = asyncio.get_running_loop()
        context["publish_progress"] = lambda progress: loop.call_soon_threadsafe(
            self._publish_progress, operation_id, dict(progress))
        entry = {"operation_id": operation_id, "started": context["started"],
                 "function": name, "status": "running", "context": context,
                 "progress": {}, "side_effect": "none"}
        self.operations[operation_id] = entry
        self.active = operation_id
        task = asyncio.create_task(asyncio.to_thread(self.execute, name, args, context))
        entry["task"] = task
        task.add_done_callback(lambda done: self._finished(operation_id, done))
        # Expiry/cancellation ends only this wait. The worker keeps ownership
        # until it finishes, including response construction and evidence publication.
        try:
            return await asyncio.wait_for(asyncio.shield(task), RESPONSE_WAIT_SECONDS)
        except asyncio.TimeoutError:
            return _response({
                **self._snapshot(entry),
                "message": "Execution continues. Query agent_get_state with operation_id; do not repeat this operation.",
            })
