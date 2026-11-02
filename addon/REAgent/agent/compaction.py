"""Bounded prefix selection, summary validation and prepared context replacement."""

from REAgent.model.messages import AssistantTurn
from REAgent.model.config import input_limit_tokens
from REAgent.model.limits import REQUEST_LIMIT_BYTES
from REAgent.model.errors import CapacityError, ModelError
from REAgent.values import encode_json, decode_json, to_json_value
from .messages import CurrentFacts, HistorySummary, ImageMaterial, ToolOutcome, UserText


SUMMARY_RECORD_LIMIT = 64
SUMMARY_TEXT_LIMIT_CHARACTERS = 1000
SUMMARY_REFERENCE_LIMIT = 16
SUMMARY_LIMIT_BYTES = 16384
REQUEST_PRESSURE_FRACTION = 0.80
REQUEST_PRESSURE_BYTES = REQUEST_LIMIT_BYTES * REQUEST_PRESSURE_FRACTION
COMPACTION_TARGET_FRACTION = 0.55
SUMMARY_FIELD_NAMES = ("observations", "hypotheses", "decisions", "failures",
                       "completed", "unfinished", "open_questions", "next_intent")
SUMMARY_FIELDS = frozenset(SUMMARY_FIELD_NAMES)


def _summary_material(ids, deterministic, narrative):
    """The one checkpoint payload shape; ordered fields keep summaries reproducible."""
    return to_json_value({"unit_ids": ids, "deterministic": deterministic,
                          "narrative": {name: narrative[name] for name in SUMMARY_FIELD_NAMES}})


def replacement_metrics(before, after):
    """Compare local bounds separately from prefix-dependent capacity estimates."""
    fields = ("local_tokens_estimate", "input_tokens_estimate", "method", "request_bytes", "image_count")
    return {"before": {key: before[key] for key in fields},
            "after": {key: after[key] for key in fields},
            "estimate_method_changed": before["method"] != after["method"]}


def validate_summary_content(reply, ids, known, previous, references):
    if reply.calls:
        raise ModelError("summary_failed", "Summary returned a tool call; nothing was executed or adopted")
    try:
        narrative = decode_json(reply.content)
        if (not isinstance(narrative, dict) or set(narrative) != SUMMARY_FIELDS | {"unit_ids"}
                or narrative["unit_ids"] != ids or len(encode_json(narrative).encode("utf-8")) > SUMMARY_LIMIT_BYTES):
            raise ValueError("Invalid bounded coverage")
        inherited = set(previous.get("deterministic", {}).get("references", ()))
        inherited.update(references)
        references = inherited
        for name in SUMMARY_FIELDS:
            rows = narrative[name]
            if not isinstance(rows, list) or len(rows) > SUMMARY_RECORD_LIMIT:
                raise ValueError("Unbounded summary")
            for row in rows:
                if (not isinstance(row, dict) or set(row) != {"text", "references"}
                        or not isinstance(row["text"], str) or not row["text"].strip() or len(row["text"]) > SUMMARY_TEXT_LIMIT_CHARACTERS
                        or not isinstance(row["references"], list) or len(row["references"]) > SUMMARY_REFERENCE_LIMIT
                        or any(not isinstance(ref, str) or ref not in known for ref in row["references"])):
                    raise ValueError("Invalid narrative reference")
                references.update(row["references"])
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ModelError("summary_failed", "Summary fields, coverage, length or references are invalid; original context retained") from None
    return narrative, references


def prepare_summary_candidate(base, candidates, before, client, tools, reason, *, narrative, references, previous, reply):
    ids = [unit.id for unit in candidates]
    prior = previous.get("deterministic", {})
    candidate = base.fork()
    covered = set(ids)
    original = list(prior.get("original_requirements", ()))
    for unit in candidates:
        original.extend({"unit_id": unit.id, "generation": item.generation, "text": item.text}
                        for item in unit.user_requirements)
    fact = next((item for unit in reversed(candidates) for item in reversed(unit.messages)
                 if isinstance(item, CurrentFacts)), None)
    deterministic = {"original_requirements": original, "references": sorted(references),
        "covered_unit_count": prior.get("covered_unit_count", 0) + len(ids),
        "current_facts": ({"fact_sequence": fact.sequence, "facts": fact.value} if fact is not None else
                          prior.get("current_facts")),
        "first_unit": prior.get("first_unit", ids[0]), "last_unit": ids[-1]}
    candidate.summary = HistorySummary(_summary_material(ids, deterministic, narrative), base._history_prefix)
    candidate.units = [unit for unit in candidate.units if unit.id not in covered]
    candidate.retained_groups = frozenset()
    candidate.checkpoint += 1
    candidate.revision += 1
    candidate._prune_images(client, force=True)
    # Framework data explains a UI-triggered change without self-referential
    # request sizes or another copy of the usage/Journal payload.
    deterministic["context_change"] = {
        "source": "framework", "kind": "model_context_summary", "reason": reason,
        "prefix_rewritten": True, "previous_estimate_method": before["method"],
        "capacity_estimate": "recomputed for the new prefix; any prior same-prefix service calibration is invalid",
        "images_released": before["image_count"] - client.estimate(candidate._messages(), tools)["image_count"],
        "disk_files": "unchanged; image release concerns request payload only"}
    candidate.summary = HistorySummary(_summary_material(ids, deterministic, narrative), base._history_prefix)
    try:
        fitting = client.prepare(candidate._messages(), tools)
    except CapacityError as exc:
        raise ModelError(exc.code, "Summary replacement still exceeds capacity; original context retained") from None
    after = fitting.estimate
    byte_pressure = reason == "request_capacity" or before["request_bytes"] >= REQUEST_PRESSURE_BYTES
    if (after["local_tokens_estimate"] >= before["local_tokens_estimate"]
            or after["request_bytes"] > before["request_bytes"]
            or byte_pressure and after["request_bytes"] >= before["request_bytes"]):
        raise ModelError("summary_not_smaller", "Summary did not reduce the comparable local input bound without increasing request bytes; original context retained")
    candidate.last_compaction = {"reason": reason, **replacement_metrics(before, after),
        "images_released": before["image_count"] - fitting.estimate["image_count"],
        "units_compacted": len(ids), "checkpoint": candidate.checkpoint,
        "covered_first": ids[0], "covered_last": ids[-1], "summary_visible_bytes": len(reply.content.encode("utf-8")),
        "summary_usage": reply.usage, "target_reached": fitting.estimate["input_tokens_estimate"] <=
            COMPACTION_TARGET_FRACTION * input_limit_tokens(client.config)}
    candidate._delivered_units.intersection_update(unit.id for unit in candidate.units)
    candidate._delivered_images.intersection_update(item.delivery_id for unit in candidate.units
        for item in unit.messages if isinstance(item, ImageMaterial) and item.images)
    return candidate, fitting


def compact(conversation, client, tools, facts, *, cancelled, request_summary, commit, attempt_key, reason="manual"):
    if cancelled():
        raise ModelError("request_cancelled", "Summary stopped before selecting a prefix")
    key = (conversation.revision, attempt_key)
    if key in conversation._attempts:
        raise ModelError("compaction_already_attempted", "This unchanged context already had one summary attempt; no repeated request was sent")
    base = conversation.fork()
    base.record_facts(facts)
    candidates = base.compactable_units(client)
    if not candidates:
        raise ModelError("nothing_to_compact", "No older delivered continuous prefix remains outside the recent complete window")
    before = client.estimate(base._messages(), tools)
    known = set(conversation._facts.references if conversation._facts is not None else ())
    if facts is not None:
        known.update(facts.references)
    known.update(unit.id for unit in conversation.units)
    previous = conversation.summary.value if conversation.summary is not None else {}
    known.update(previous.get("deterministic", {}).get("references", ()))

    def coverage():
        return HistorySummary({"instruction": conversation._summary_instruction.text,
            "unit_ids": [unit.id for unit in candidates], "previous_narrative": previous.get("narrative"),
            "unit_map": [{"unit_id": unit.id, "materials": [locator(item) for item in unit.messages]}
                         for unit in candidates],
            "coverage": "summarize only this older continuous prefix; recent exchanges remain verbatim"}, purpose="coverage")

    try:
        if reason == "service_context_limit":
            raise CapacityError("context_capacity", "Do not resend the full context explicitly rejected by the service")
        prepared = client.prepare([*base._messages(), coverage()], tools)
    except CapacityError:
        prepared = None
        while candidates:
            messages = [base.instructions, *(() if base.summary is None else (base.summary,)),
                        *(item for unit in candidates for item in unit.messages)]
            if base.requirements and base.requirements[-1] not in messages:
                messages.append(base.requirements[-1])
            try:
                prepared = client.prepare([*messages, coverage()], tools)
                break
            except CapacityError:
                candidates.pop()
    if prepared is None:
        raise CapacityError("summary_capacity", "No complete prefix and summary instruction fit; original context retained")
    ids = [unit.id for unit in candidates]
    replacement = {}

    def validate(reply):
        if cancelled():
            raise ModelError("request_cancelled", "Requirements changed during summary; original context retained")
        narrative, references = validate_summary_content(reply, ids, known, previous,
            (ref for unit in candidates for item in unit.messages
             if isinstance(item, ToolOutcome) for ref in item.references))
        candidate, fitting = prepare_summary_candidate(base, candidates, before, client, tools, reason,
            narrative=narrative, references=references, previous=previous, reply=reply)
        replacement.update(candidate=candidate, prepared=fitting)

    if cancelled():
        raise ModelError("request_cancelled", "Summary cancelled before its single request")
    conversation._attempts.add(key)
    base._attempts.add(key)
    request_summary(prepared, validate=validate)
    if cancelled():
        raise ModelError("request_cancelled", "Requirements changed before summary commit; original context retained")
    commit(replacement["candidate"], conversation)
    return replacement["prepared"]

def locator(item):
    """Coverage hints are appended data, never labels inserted into old messages."""
    if isinstance(item, AssistantTurn):
        return {"type": "assistant", "response_id": item.id, "call_ids": [call.id for call in item.calls]}
    if isinstance(item, ToolOutcome):
        return {"type": "result", "call_id": item.call_id, "references": item.references}
    if isinstance(item, UserText):
        return {"type": "user", "generation": item.generation, "text_starts_with": item.text[:96]}
    if isinstance(item, CurrentFacts):
        return {"type": "facts", "fact_sequence": item.sequence}
    if isinstance(item, ImageMaterial):
        return {"type": "image_group", "group_id": item.group_id, "delivery_id": item.delivery_id}
    raise TypeError("Unsupported completed material")
