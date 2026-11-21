"""Pure named-profile parsing, semantic comparison and credential decryption."""

import json
import os

from REAgent.agent import settings
from REAgent.application.credentials import open_key

_EMPTY_PROFILES = '{"selected":"","items":[]}'
_CONFIG_FIELDS = ("provider", "base_url", "model", "context_tokens", "output_tokens", "reasoning_effort")
_CONTEXT_TOKENS = 1048576


def default_profiles():
    return {name: {"provider": name, **defaults, "model": "", "context_tokens": _CONTEXT_TOKENS,
                   "reasoning_effort": "default"} for name, defaults in settings.provider_defaults().items()}


def profile_efforts(provider, model):
    return settings.reasoning_efforts(provider, model)


def profile_connection(provider, base_url):
    return settings.connection_details(provider, base_url)


def idle_context(config):
    return {"status": "not_started", **(settings.context_budget(config) if config is not None else {})}


def config_values(row):
    try:
        budget = row["output_tokens"]
        if (isinstance(budget, bool) or isinstance(row["context_tokens"], bool)
                or type(budget) not in (str, int) or type(row["context_tokens"]) not in (str, int)):
            raise ValueError()
        return {**{name: row[name] for name in ("provider", "base_url", "model", "reasoning_effort")},
                "context_tokens": int(row["context_tokens"]),
                "output_tokens": int(budget)}
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ValueError("Enter a provider, model, whole-number context capacity and reasoning effort.") from None

def validate_config(values):
    return settings.validate_config(values)

def semantic_inputs(row):
    values = config_values(row)
    connection = profile_connection(values["provider"], values["base_url"])
    return (connection["credential_binding"], values["model"],
            values["context_tokens"], values["reasoning_effort"], values["output_tokens"])

def read_profiles(value):
    try:
        data = json.loads(value or _EMPTY_PROFILES)
        if not isinstance(data, dict) or set(data) != {"selected", "items"} or not isinstance(data["selected"], str):
            raise ValueError()
        rows = data["items"]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError()
        profiles = {}
        providers = settings.provider_defaults()
        for row in rows:
            if not isinstance(row, dict) or set(row) - {*_CONFIG_FIELDS, "name", "protected_key"}:
                raise ValueError()
            if (any(not isinstance(row[field], str) for field in ("provider", "base_url", "model", "reasoning_effort"))
                    or row["provider"] not in providers or type(row["context_tokens"]) is not int
                    or row["context_tokens"] <= 0 or row["output_tokens"] is not None
                    and (type(row["output_tokens"]) is not int or row["output_tokens"] <= 0)):
                raise ValueError()
            name = row.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 100 or name in profiles:
                raise ValueError()
            if not isinstance(row.get("protected_key", ""), str):
                raise ValueError()
            profiles[name] = {field: row[field] for field in _CONFIG_FIELDS}
            if profiles[name]["output_tokens"] is None:
                profiles[name]["output_tokens"] = providers[row["provider"]]["output_tokens"]
            if row.get("protected_key"):
                profiles[name]["protected_key"] = row["protected_key"]
        if profiles and data["selected"] not in profiles:
            raise ValueError()
        return profiles, data["selected"]
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ValueError("Saved model profiles are invalid; the stored value has not been overwritten.") from None


def saved_settings(read_settings):
    """Restore only the current format, without probing or creating a worker."""
    profiles, name = read_profiles(read_settings())
    if name not in profiles:
        return None
    row = profiles[name]
    config = validate_config(config_values(row))
    credentials = None
    if os.name == "nt" and row.get("protected_key"):
        connection = profile_connection(row["provider"], row["base_url"])
        credentials = {"api_key": open_key(row["protected_key"], connection["credential_binding"])}
    return config, credentials, name


def load_model_config(model_data, read_settings):
    """Validate explicit model.json parameters and decrypt its saved profile API key."""
    if not isinstance(model_data, dict):
        raise ValueError("model.json must be a JSON object")
    required = {"profile", *_CONFIG_FIELDS}
    if set(model_data) != required:
        missing = sorted(required - set(model_data))
        extra = sorted(set(model_data) - required)
        parts = []
        if missing:
            parts.append("missing fields: " + ", ".join(missing))
        if extra:
            parts.append("unknown fields: " + ", ".join(extra))
        raise ValueError("Invalid model configuration (" + "; ".join(parts) + ")")
    profile_name = model_data["profile"]
    if not isinstance(profile_name, str) or not profile_name.strip():
        raise ValueError("Model profile name must be a non-empty string")
    config = validate_config(config_values(model_data))
    connection = profile_connection(config["provider"], config["base_url"])
    stored = read_settings() if callable(read_settings) else read_settings
    profiles, _selected = read_profiles(stored)
    if profile_name not in profiles:
        raise ValueError("Saved model profile was not found: " + profile_name)
    row = profiles[profile_name]
    row_connection = profile_connection(row["provider"], row["base_url"])
    if row_connection["credential_binding"] != connection["credential_binding"]:
        raise ValueError("Saved profile credential binding does not match model provider and base_url")
    protected = row.get("protected_key")
    if not protected:
        raise ValueError("Saved model profile has no saved API key: " + profile_name)
    api_key = open_key(protected, connection["credential_binding"])
    return config, {"api_key": api_key}, profile_name
