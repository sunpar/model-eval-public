from __future__ import annotations

from enum import Enum


class ErrorKind(str, Enum):
    RETRYABLE = "retryable"
    BLOCKED_BY_CONFIG = "blocked_by_config"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_AUTH = "provider_auth"
    UNKNOWN = "unknown"


class ProviderError(Exception):
    kind = ErrorKind.UNKNOWN


class ProviderBlockedError(ProviderError):
    kind = ErrorKind.BLOCKED_BY_CONFIG


class ProviderInvalidRequestError(ProviderError):
    kind = ErrorKind.INVALID_REQUEST


class ProviderAuthError(ProviderError):
    kind = ErrorKind.PROVIDER_AUTH


class ProviderRetryableError(ProviderError):
    kind = ErrorKind.RETRYABLE


def classify_provider_error(error: BaseException) -> ErrorKind:
    if isinstance(error, ProviderError):
        return error.kind
    if isinstance(error, (TimeoutError, ConnectionError)):
        return ErrorKind.RETRYABLE
    if isinstance(error, PermissionError):
        return ErrorKind.PROVIDER_AUTH

    message = str(error).lower()
    if any(part in message for part in ("timeout", "rate limit", "temporar", "429", "5xx")):
        return ErrorKind.RETRYABLE
    if any(part in message for part in ("api key", "unauthorized", "forbidden", "auth")):
        return ErrorKind.PROVIDER_AUTH
    if any(part in message for part in ("invalid request", "bad request", "400", "schema")):
        return ErrorKind.INVALID_REQUEST
    return ErrorKind.UNKNOWN
