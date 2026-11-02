"""One synchronous HTTP/SSE transport with three fixed protocol adapters."""

import re
import ssl
import uuid
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, getproxies

from REAgent.model.messages import AssistantTurn
from REAgent.model.limits import (
    IMAGE_LIMIT_BYTES, IMAGE_LIMIT_COUNT, REQUEST_LIMIT_BYTES)
from REAgent.model.errors import CapacityError, ModelError
from REAgent.values import decode_json, encode_json
from REAgent.model.transport import ControlledHTTPHandler, ControlledHTTPSHandler, ReadBudget, sse_events

from .registry import PROVIDERS
from .config import ModelConfig, input_limit_tokens, model_identity
from . import estimation


@dataclass(frozen=True)
class PreparedRequest:
    data: bytes
    estimate: dict
    messages: tuple
    tools_key: str


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ModelClient:
    def __init__(self, config, *, api_key):
        if not isinstance(config, ModelConfig):
            raise ValueError("ModelConfig is required")
        self._config = config
        self._adapter = PROVIDERS[config.provider]
        self.identity = model_identity(config)
        self._active = False
        self._key = None
        self._baseline = None
        self._last_layout = None
        self._cache_key = uuid.uuid4().hex
        self.set_credentials(api_key=api_key)

    @property
    def config(self):
        return self._config

    def set_credentials(self, *, api_key):
        if self._active:
            raise RuntimeError("A model request is still active")
        if (not isinstance(api_key, str) or not api_key or not api_key.isascii()
                or re.search(r"[\x00-\x20\x7f]", api_key)):
            raise ValueError("Enter a non-empty header-safe API key")
        self._key = api_key

    def proxy_status(self):
        proxies = getproxies()
        return {"enabled": any(proxies.get(name) for name in ("http", "https")),
                "source": "urllib_environment_or_system", "bypass": "urllib_no_proxy"}

    def _request_body(self, messages, tools):
        return self._adapter.request(self.config, messages, tools, self.identity, cache_key=self._cache_key)

    def estimate(self, messages, tools):
        body = self._request_body(messages, tools)
        return estimation.estimate(self.config, self._baseline, self._last_layout, messages, tools, len(encode_json(body).encode("utf-8")))


    def material_estimate(self, message):
        return estimation.material_estimate(self.config, message)

    def image_estimates(self, messages):
        return estimation.image_estimates(self.config, messages)

    def observe_usage(self, prepared, reply):
        self._baseline = (prepared, reply)

    def prepare(self, messages, tools):
        body = self._request_body(messages, tools)
        data = encode_json(body).encode("utf-8")
        estimate = estimation.estimate(self.config, self._baseline, self._last_layout, messages, tools, len(data))
        if estimate["image_count"] > IMAGE_LIMIT_COUNT or estimate["image_bytes"] > IMAGE_LIMIT_BYTES:
            raise CapacityError("image_capacity", "The complete image set exceeds configured request capacity")
        if estimate["input_tokens_estimate"] > input_limit_tokens(self.config):
            raise CapacityError("context_capacity", "Estimated input plus output and reserve exceeds configured context")
        if len(data) > REQUEST_LIMIT_BYTES:
            raise CapacityError("request_capacity", "The complete request exceeds configured byte capacity")
        return PreparedRequest(data, estimate, tuple(messages), encode_json(tools))

    def require_visual(self):
        capabilities = self._adapter.capabilities(self.config.provider, self._adapter.protocol, self.config.model)
        if capabilities is not None and (not capabilities.image_input or not capabilities.tools):
            raise ModelError("model_visual_unavailable", "This model does not support the required visual reconstruction inputs and tools")

    def stream(self, prepared, *, cancelled, on_network_start=None, on_first_event=None):
        if self._active:
            raise ModelError("model_busy", "A model request is still active")
        if not isinstance(prepared, PreparedRequest):
            raise TypeError("A capacity-checked request is required")
        self._active = True
        parser = self._adapter.parser(self.identity, binding_controls=self.config.binding_controls)
        budget = ReadBudget(cancelled)
        checkpoint = budget.checkpoint

        try:
            checkpoint()
            proxies = getproxies()
            if any("://" in value and urlsplit(value).scheme not in ("http", "https")
                   for name, value in proxies.items() if name in ("http", "https") and value):
                raise ModelError("proxy_unsupported", "Configured proxy transport is unsupported")
            opener = build_opener(ProxyHandler(proxies), ControlledHTTPHandler(budget),
                                  ControlledHTTPSHandler(budget, ssl.create_default_context()), _NoRedirect())
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                       **self._adapter.headers(self.config, self._key)}
            request = Request(self.identity[2], data=prepared.data, headers=headers, method="POST")
            checkpoint()
            timeout = budget.wait()
            self._last_layout = (prepared.messages, prepared.tools_key)
            if on_network_start is not None:
                on_network_start()
            with opener.open(request, timeout=timeout) as response:
                checkpoint()
                if response.headers.get_content_type() != "text/event-stream":
                    raise ModelError("stream_unsupported", "Service did not return its SSE protocol")
                def events():
                    first = True
                    for event in sse_events(response, checkpoint):
                        if first:
                            first = False
                            if on_first_event is not None:
                                on_first_event()
                        yield event

                for item in parser.parse(events()):
                    checkpoint()
                    yield item
                    if isinstance(item, AssistantTurn):
                        return
                raise ModelError("incomplete_response", "No complete assistant response was retained")
        except ModelError as exc:
            if exc.usage is None:
                exc.usage = parser.usage
            parser.attach_error(exc)
            raise
        except HTTPError as exc:
            status = exc.code
            code = None
            try:
                raw = exc.read(16385)
                if len(raw) <= 16384:
                    detail = decode_json(raw.decode("utf-8"))
                    detail = detail.get("error", {}) if isinstance(detail, dict) else {}
                    code = detail.get("code", detail.get("type")) if isinstance(detail, dict) else None
            except (ValueError, UnicodeError, OSError):
                pass
            finally:
                exc.close()
            context_limit = status == 400 and code in ("context_length_exceeded", "model_context_window_exceeded")
            raise ModelError("model_context_limit" if context_limit else "model_http_error",
                             "Service explicitly rejected the input context capacity" if context_limit else
                             "Model service returned HTTP %s; check endpoint, key, model, effort and output budget" % status,
                             usage=parser.usage, diagnostics={"http_status": status,
                                                             "binding_controls": self.config.binding_controls}) from None
        except (HTTPException, URLError, OSError) as exc:
            try:
                budget.checkpoint()
            except ModelError as expired:
                error = expired
            else:
                timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
                error = ModelError("model_io_timeout" if timed_out else "model_transport_error",
                                   "Model connection or read failed", usage=parser.usage)
            parser.attach_error(error)
            raise error from None
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, RecursionError):
            error = ModelError("invalid_stream", "Model stream contains invalid JSON or protocol fields", usage=parser.usage)
            parser.attach_error(error)
            raise error from None
        finally:
            self._active = False
