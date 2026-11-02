"""Pure Python composition; no settings handles, host objects or live resources."""

from REAgent.model.client import ModelClient
from REAgent.rebuild.controller import Controller
from REAgent.rebuild.toolset import RebuildTools


def create_model(config, *, api_key):
    return ModelClient(config, api_key=api_key)


def create_rebuild(*, on_journal):
    controller = Controller()
    controller.observe_journal(on_journal)
    return RebuildTools(controller)
