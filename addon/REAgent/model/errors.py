"""Bounded model failures, without client or provider dependencies."""

class ModelError(RuntimeError):
    """Safe diagnostic: never include headers, credentials, URLs or service bodies."""

    def __init__(self, code, message, *, usage=None, native_reason=None, protocol_completed=False,
                 diagnostics=None, secondary_code=None):
        super().__init__(message)
        self.code = code
        self.usage = usage
        self.native_reason = native_reason
        self.protocol_completed = protocol_completed
        self.diagnostics = diagnostics
        self.secondary_code = secondary_code

    def detail(self):
        return {"code": self.code, "message": str(self), "native_reason": self.native_reason,
                "protocol_completed": self.protocol_completed, "usage": self.usage,
                "diagnostics": self.diagnostics, "secondary_code": self.secondary_code}

class CapacityError(ModelError):
    pass
