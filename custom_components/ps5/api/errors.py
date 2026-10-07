"""Errors raised by the PlayStation API client."""

from __future__ import annotations

from typing import Any


class PsnError(Exception):
    """Base class for all errors of this package."""


class NpssoInvalid(PsnError):
    """The NPSSO is invalid or expired (the authorize call returned no code)."""


class AuthRejected(PsnError):
    """The token endpoint rejected a request (HTTP 400/401)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Token endpoint rejected the request (HTTP {status})")
        self.status = status


class AuthExpired(PsnError):
    """A GraphQL request was rejected with HTTP 401."""


class RateLimited(PsnError):
    """Sony answered HTTP 429, or the client is paused after one."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Rate limited, retry after {retry_after:.0f} s")
        self.retry_after = retry_after


class TransientError(PsnError):
    """Timeout, connection error or HTTP 5xx."""


class PersistedQueryNotFound(PsnError):
    """Sony no longer knows the persisted query hash of an operation."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"Persisted query not found for operation {operation}")
        self.operation = operation


class ApiError(PsnError):
    """Any other unexpected API answer."""

    def __init__(
        self, message: str, operation: str | None = None, errors: list[Any] | None = None
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.errors = errors or []


class WriteRejected(PsnError):
    """A write operation returned a non-null errorCode."""

    def __init__(self, operation: str, error_code: Any, reason_code: Any) -> None:
        super().__init__(
            f"{operation} rejected: errorCode={error_code!r}, reasonCode={reason_code!r}"
        )
        self.operation = operation
        self.error_code = error_code
        self.reason_code = reason_code


class ConsoleNotFound(PsnError):
    """The selected console is no longer among the account's consoles."""

    def __init__(self, console_name: str) -> None:
        super().__init__(f"Console {console_name!r} not found")
        self.console_name = console_name
