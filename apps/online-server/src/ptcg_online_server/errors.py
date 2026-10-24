"""Stable public errors never interpolate database, credential or request contents."""

from ptcg_protocol.validation import validate


class ServiceError(Exception):
    def __init__(self, code, status, *, retryable=False):
        self.code, self.status, self.retryable = code, status, retryable
        super().__init__(code)

    def payload(self):
        return validate(
            "ProtocolError",
            {
                "protocolVersion": "2.0",
                "code": self.code,
                "retryable": self.retryable,
            },
        )
