"""Request-local standard-library HTTP reads and bounded incremental SSE.

Only the original worker owns these sockets. DNS is checked after the system
resolver returns; it cannot be interrupted here. Poll timeouts never escape
the raw receive layer into a BufferedReader or a socket.makefile buffer.
"""

import io
import socket
import time
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from urllib.request import HTTPHandler, HTTPSHandler

from REAgent.model.errors import ModelError
from REAgent.model.limits import (
    IO_POLL_INTERVAL_SECONDS, IO_TIMEOUT_SECONDS, READ_CHUNK_BYTES, RESPONSE_LIMIT_BYTES,
    SSE_EVENT_LIMIT_BYTES, SSE_LINE_LIMIT_BYTES, TOTAL_TIMEOUT_SECONDS)


class ReadBudget:
    def __init__(self, cancelled):
        self.cancelled = cancelled
        self.deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
        self.last_received = time.monotonic()

    def checkpoint(self):
        if self.cancelled():
            raise ModelError("request_cancelled", "Request stopped; no partial decision is executable")
        if time.monotonic() >= self.deadline:
            raise ModelError("request_budget", "Model request exceeded its total time budget")

    def wait(self, *, receiving=False):
        self.checkpoint()
        remaining = self.deadline - time.monotonic()
        if receiving:
            idle = IO_TIMEOUT_SECONDS - (time.monotonic() - self.last_received)
            if idle <= 0:
                raise ModelError("model_io_timeout", "Model connection received no data within the I/O budget")
            return max(0.001, min(remaining, idle, IO_POLL_INTERVAL_SECONDS))
        return max(0.001, min(remaining, IO_TIMEOUT_SECONDS))

    def connect(self, address, timeout=None, source_address=None):
        # Resolve once, then give each connection attempt only its remaining budget.
        self.checkpoint()
        addresses = socket.getaddrinfo(address[0], address[1], 0, socket.SOCK_STREAM)
        self.checkpoint()
        failure = None
        for family, kind, protocol, _name, target in addresses:
            sock = socket.socket(family, kind, protocol)
            try:
                sock.settimeout(self.wait())
                if source_address:
                    sock.bind(source_address)
                sock.connect(target)
                # TLS inherits this timeout after the TCP connection returns.
                sock.settimeout(self.wait())
                return sock
            except BaseException as exc:
                sock.close()
                if not isinstance(exc, OSError):
                    raise
                failure = exc
        if failure is not None:
            raise failure
        raise OSError("No address was resolved")


class _Receive(io.RawIOBase):
    def __init__(self, sock, budget):
        super().__init__()
        self.sock, self.budget = sock, budget
        self._lease = None
        # Keep a file reference so urllib's socket.close does not close the FD
        # before HTTPResponse is consumed. Never read through this SocketIO.
        self._lease = sock.makefile("rb", buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.closed:
            raise ValueError("Read from a closed response")
        if not len(buffer):
            return 0
        while True:
            self.sock.settimeout(self.budget.wait(receiving=True))
            try:
                count = self.sock.recv_into(buffer)
            except socket.timeout:
                continue
            self.budget.checkpoint()
            if count:
                self.budget.last_received = time.monotonic()
            return count

    def close(self):
        if not self.closed:
            try:
                if self._lease is not None:
                    self._lease.close()
            finally:
                super().close()


class _ResponseSocket:
    def __init__(self, sock, budget):
        self.sock, self.budget = sock, budget

    def makefile(self, mode):
        if mode != "rb":
            raise ValueError("Only binary response reads are supported")
        self.budget.last_received = time.monotonic()
        return io.BufferedReader(_Receive(self.sock, self.budget))


class _ControlledConnection:
    def __init__(self, host, *, budget, **kwargs):
        super().__init__(host, **kwargs)
        self._budget = budget
        self._create_connection = budget.connect
        self.response_class = lambda sock, debuglevel=0, **options: HTTPResponse(
            _ResponseSocket(sock, budget), debuglevel=debuglevel, **options)

    def connect(self):
        self.timeout = self._budget.wait()
        super().connect()
        self._budget.checkpoint()

    def _tunnel(self):
        super()._tunnel()
        self.sock.settimeout(self._budget.wait())

    def send(self, data):
        if self.sock is None:
            self.connect()
        self.sock.settimeout(self._budget.wait())
        super().send(data)
        self._budget.checkpoint()


class _HTTPConnection(_ControlledConnection, HTTPConnection):
    pass


class _HTTPSConnection(_ControlledConnection, HTTPSConnection):
    pass


class ControlledHTTPHandler(HTTPHandler):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def http_open(self, request):
        return self.do_open(lambda host, **kwargs: _HTTPConnection(host, budget=self.budget, **kwargs), request)


class ControlledHTTPSHandler(HTTPSHandler):
    def __init__(self, budget, context):
        super().__init__(context=context)
        self.budget = budget

    def https_open(self, request):
        return self.do_open(lambda host, **kwargs: _HTTPSConnection(host, budget=self.budget, **kwargs),
                            request, context=self._context)


def sse_events(response, checkpoint):
    pending, data = bytearray(), []
    event, total, event_bytes, first, after_cr = None, 0, 0, True, False

    def lines(chunk):
        nonlocal after_cr
        start = 0
        for index, byte in enumerate(chunk):
            if after_cr:
                after_cr = False
                if byte == 10:
                    start = index + 1
                    continue
            if byte in (10, 13):
                pending.extend(chunk[start:index])
                if len(pending) > SSE_LINE_LIMIT_BYTES:
                    raise ModelError("sse_line_capacity", "Model SSE line exceeds its byte budget")
                # Charge the maximum CRLF delimiter even for a lone CR.
                yield bytes(pending), 2 if byte == 13 else 1
                pending.clear()
                start = index + 1
                after_cr = byte == 13
        pending.extend(chunk[start:])
        if len(pending) > SSE_LINE_LIMIT_BYTES:
            raise ModelError("sse_line_capacity", "Model SSE line exceeds its byte budget")
        if event_bytes + len(pending) > SSE_EVENT_LIMIT_BYTES:
            raise ModelError("sse_event_capacity", "Model SSE event exceeds its byte budget")

    while True:
        checkpoint()
        chunk = response.read1(min(READ_CHUNK_BYTES, RESPONSE_LIMIT_BYTES - total + 1))
        checkpoint()
        total += len(chunk)
        if total > RESPONSE_LIMIT_BYTES:
            raise ModelError("response_capacity", "Model stream exceeds its total byte budget")
        if not chunk:
            raise ModelError("incomplete_stream", "Stream ended without a protocol completion")
        for line, delimiter_bytes in lines(chunk):
            event_bytes += len(line) + delimiter_bytes
            if event_bytes > SSE_EVENT_LIMIT_BYTES:
                raise ModelError("sse_event_capacity", "Model SSE event exceeds its byte budget")
            text = line.decode("utf-8-sig" if first else "utf-8")
            first = False
            if not text:
                if data:
                    yield event, "\n".join(data)
                data, event, event_bytes = [], None, 0
            elif text.startswith("data:"):
                part = text[5:]
                data.append(part[1:] if part.startswith(" ") else part)
            elif text.startswith("event:"):
                event = text[6:].strip()
