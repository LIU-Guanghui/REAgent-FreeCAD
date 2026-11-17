"""Immutable evidence associations from completed model requests."""

from dataclasses import dataclass

from REAgent.json_values import decode_json, encode_json


@dataclass(frozen=True)
class AdoptedObservations:
    observations: str = "{}"

    @property
    def seen(self):
        return decode_json(self.observations)


def prepare_receipt(policy, observations, *, purpose="action"):
    if purpose != "action":
        return policy
    seen = policy.seen
    target_ids = set(seen.get("target_evidence_ids", ()))
    for key, payload in observations:
        if key == "target_evidence_ids":
            target_ids.add(payload["evidence_id"])
        else:
            seen[key] = {**payload, "scope": "model_request_completed", "inspection": "not_attested"}
    if target_ids:
        seen["target_evidence_ids"] = sorted(target_ids)
    return AdoptedObservations(encode_json(seen))
