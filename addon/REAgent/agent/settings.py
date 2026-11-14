"""Model settings and display data available before a run exists."""

from dataclasses import fields

from REAgent.model.config import DEFAULT_OUTPUT_TOKENS, ModelConfig, credential_binding, endpoint
from REAgent.model.limits import CONTEXT_RESERVE_TOKENS
from REAgent.model.registry import PROVIDERS


def provider_defaults():
    return {name: {"base_url": provider.base_url, "output_tokens": DEFAULT_OUTPUT_TOKENS}
            for name, provider in PROVIDERS.items()}


def reasoning_efforts(provider_name, model):
    provider = PROVIDERS[provider_name]
    return tuple(provider.efforts(provider_name, provider.protocol, model))


def connection_details(provider, base_url):
    return {"endpoint": endpoint(provider, base_url), "credential_binding": credential_binding(provider, base_url)}


def config_fields(config):
    return {item.name: getattr(config, item.name) for item in fields(config) if item.init}


def validate_config(values):
    return config_fields(ModelConfig(**values))


def context_budget(values):
    return {"model": values["model"], "capacity_tokens": values["context_tokens"],
            "output_tokens": values["output_tokens"], "reserve_tokens": CONTEXT_RESERVE_TOKENS,
            "reasoning_effort": values["reasoning_effort"]}
