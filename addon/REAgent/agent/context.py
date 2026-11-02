"""Append-only units, explicit image retention and transactional checkpoints."""

from .compaction import (REQUEST_PRESSURE_BYTES, REQUEST_PRESSURE_FRACTION, compact,
                         replacement_metrics)
from copy import copy
from dataclasses import dataclass, replace

from REAgent.model.messages import AssistantTurn
from REAgent.model.config import input_limit_tokens
from REAgent.model.limits import (
    IMAGE_SOFT_TARGET_COUNT, IMAGE_SOFT_TARGET_TOKENS, RECENT_WINDOW_FRACTION,
    RECENT_WINDOW_MAX_TOKENS)
from REAgent.agent.messages import ImageMaterial, ToolOutcome, UserText
from REAgent.model.errors import CapacityError, ModelError


@dataclass(frozen=True)
class ActiveContext:
    conversation: object
    policy: object
    binding: str | None


@dataclass(frozen=True)
class ConversationUnit:
    id: str
    messages: tuple
    observations: tuple = ()
    user_requirements: tuple = ()


class Conversation:
    def __init__(self, instructions, objective, *, summary_instruction, history_prefix):
        self.instructions = instructions
        self._summary_instruction = summary_instruction
        self._history_prefix = history_prefix
        self.requirements, self.units = [], []
        self.summary = self._facts = self.last_compaction = None
        self.revision = self.checkpoint = self._sequence = self._fact_sequence = 0
        self._delivered_units, self._delivered_images, self._attempts = set(), set(), set()
        self.retained_groups = frozenset()
        self.add_requirement(0, objective)

    def fork(self):
        candidate = copy(self)
        candidate.units, candidate.requirements = list(self.units), list(self.requirements)
        candidate._delivered_units, candidate._delivered_images = set(self._delivered_units), set(self._delivered_images)
        candidate._attempts = set(self._attempts)
        return candidate

    def _append(self, messages, *, observations=(), user_requirements=()):
        self._sequence += 1
        self.units.append(ConversationUnit("unit-%s" % self._sequence, tuple(messages), tuple(observations),
                                           tuple(user_requirements)))
        self.revision += 1

    def add_requirement(self, generation, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Task requirements must be non-empty text")
        material = UserText(text, generation)
        self.requirements.append(material)
        self._append((material,), user_requirements=(material,))

    def record_facts(self, facts):
        if facts is None or self._facts is not None and facts.value == self._facts.value:
            return
        self._fact_sequence += 1
        self._facts = replace(facts, sequence=self._fact_sequence)
        self._append((self._facts,))

    def record_reply(self, reply, materials=(), *, observations=()):
        materials = tuple(materials)
        outcomes = tuple(item.call_id for item in materials if isinstance(item, ToolOutcome) and item.source == "model")
        if outcomes != tuple(call.id for call in reply.calls):
            raise ValueError("Every model call requires its original result in call order")
        self._append((reply, *materials), observations=observations)

    def record_human(self, material):
        self._append((material,))

    def receipt(self, unit_ids, delivery_ids):
        """Only a successful adopted MAIN request calls this, never a summary."""
        before = (len(self._delivered_units), len(self._delivered_images))
        self._delivered_units.update(unit_ids)
        self._delivered_images.update(delivery_ids)
        if before != (len(self._delivered_units), len(self._delivered_images)):
            self.revision += 1

    def discard_provider_state(self):
        self.units = [replace(unit, messages=tuple(replace(message, provider_state=None)
            if isinstance(message, AssistantTurn) else message for message in unit.messages)) for unit in self.units]
        self.revision += 1

    def loaded_group(self, group_id):
        return any(isinstance(item, ImageMaterial) and item.group_id == group_id and item.images
                   for unit in self.units for item in unit.messages)

    def retain(self, group_id, policy):
        if not self.loaded_group(group_id):
            raise ValueError("Read the complete image group into context before changing its retention")
        groups = set(self.retained_groups)
        if policy == "until_checkpoint":
            groups.add(group_id)
        elif policy == "release":
            groups.discard(group_id)
        else:
            raise ValueError("Unknown image retention policy")
        if groups != self.retained_groups:
            self.retained_groups = frozenset(groups)
            self.revision += 1

    def _messages(self):
        return [self.instructions, *(() if self.summary is None else (self.summary,)),
                *(message for unit in self.units for message in unit.messages)]

    def estimate(self, client, tools, facts):
        candidate = self.fork()
        candidate.record_facts(facts)
        return client.estimate(candidate._messages(), tools)

    def compactable_units(self, client):
        if len(self.units) < 2:
            return []
        budget = min(RECENT_WINDOW_MAX_TOKENS, input_limit_tokens(client.config) * RECENT_WINDOW_FRACTION)
        total, cut = 0, len(self.units)
        while cut and total < budget:
            cut -= 1
            total += sum(client.material_estimate(item)[1] + 64 for item in self.units[cut].messages)
        replies = [index for index, unit in enumerate(self.units)
                   if any(isinstance(item, AssistantTurn) for item in unit.messages)]
        if replies:
            cut = min(cut, replies[-1])
        for index, unit in enumerate(self.units[:cut]):
            if unit.id not in self._delivered_units:
                cut = index
                break
        return self.units[:cut]

    def _prune_images(self, client, *, force=False):
        images = [item for unit in self.units for item in unit.messages if isinstance(item, ImageMaterial) and item.images]
        count = sum(len(item.images) for item in images)
        tokens = sum(row["tokens_estimate"] for item in images for row in client.image_estimates((item,)))
        if not force and count <= IMAGE_SOFT_TARGET_COUNT and tokens <= IMAGE_SOFT_TARGET_TOKENS:
            return 0
        # Each group's newest delivery, ordered from most to least recent.
        latest = {}
        for item in reversed(images):
            latest.setdefault(item.group_id, item)
        keep = {item.delivery_id for item in images if item.delivery_id not in self._delivered_images}
        keep.update(item.delivery_id for group, item in latest.items() if group in self.retained_groups)
        kept_count = sum(len(item.images) for item in images if item.delivery_id in keep)
        kept_tokens = sum(row["tokens_estimate"] for item in images if item.delivery_id in keep
                          for row in client.image_estimates((item,)))
        for item in list(latest.values())[:2]:
            if item.delivery_id in keep:
                continue
            cost = sum(row["tokens_estimate"] for row in client.image_estimates((item,)))
            if not keep or kept_count + len(item.images) <= IMAGE_SOFT_TARGET_COUNT and kept_tokens + cost <= IMAGE_SOFT_TARGET_TOKENS:
                keep.add(item.delivery_id)
                kept_count += len(item.images)
                kept_tokens += cost
        removed = sum(len(item.images) for item in images if item.delivery_id not in keep)
        if removed:
            self.units = [replace(unit, messages=tuple(
                replace(item, images=(), value={**item.value, "context_payload": "released; read registered evidence to reload"})
                if isinstance(item, ImageMaterial) and item.images and item.delivery_id not in keep else item
                for item in unit.messages)) for unit in self.units]
            self.revision += 1
        return removed

    def prepare(self, client, tools, facts, *, cancelled, request_summary, commit, attempt_key):
        if cancelled():
            raise ModelError("request_cancelled", "Context preparation stopped before staging")
        candidate = self.fork()
        candidate.record_facts(facts)
        before = client.estimate(candidate._messages(), tools)
        removed = candidate._prune_images(client, force=before["request_bytes"] >= REQUEST_PRESSURE_BYTES)
        capacity = None
        try:
            prepared = client.prepare(candidate._messages(), tools)
        except CapacityError as exc:
            prepared, capacity = None, exc
        limit = input_limit_tokens(client.config)
        if capacity is not None and capacity.code == "context_capacity":
            fixed = client.estimate([candidate.instructions, *candidate.requirements], tools)["input_tokens_estimate"]
            largest = max((sum(client.material_estimate(item)[1] + 64 for item in unit.messages)
                           for unit in candidate.units), default=0)
            if fixed >= limit or largest >= limit:
                raise CapacityError("indivisible_context_capacity",
                    "Fixed rules/tools plus original requirements, or one complete result/image unit, exceed input room; no summary can safely split them")
        pressure = (prepared is None and capacity.code != "image_capacity" or
                    prepared is not None and (prepared.estimate["input_tokens_estimate"] >= REQUEST_PRESSURE_FRACTION * limit
                                              or prepared.estimate["request_bytes"] >= REQUEST_PRESSURE_BYTES))
        if pressure and candidate.compactable_units(client):
            return self.compact(client, tools, facts, cancelled=cancelled, request_summary=request_summary,
                                commit=commit, attempt_key=attempt_key, reason=capacity.code if capacity else "high_water")
        if capacity is not None:
            raise CapacityError(capacity.code, str(capacity) + "; fixed prefix, original requirements or protected complete image groups cannot be reduced safely")
        if removed:
            candidate.last_compaction = {"reason": "image_pressure", **replacement_metrics(before, prepared.estimate), "images_released": removed,
                "units_compacted": 0, "checkpoint": candidate.checkpoint}
        if cancelled():
            raise ModelError("request_cancelled", "Context preparation cancelled; original context retained")
        commit(candidate, self)
        return prepared


    def compact(self, client, tools, facts, **callbacks):
        return compact(self, client, tools, facts, **callbacks)
