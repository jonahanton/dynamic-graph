from __future__ import annotations

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _is_retryable_status(exc: httpx.HTTPStatusError) -> bool:
    return exc.response.status_code in _RETRY_STATUSES


class _RetryableStatus(httpx.HTTPStatusError):
    """Marker subclass so only transient status codes trigger retries."""


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if _is_retryable_status(exc):
            raise _RetryableStatus(str(exc), request=exc.request, response=exc.response) from exc
        raise


def async_retrying() -> AsyncRetrying:
    return AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8.0),
        retry=retry_if_exception_type((httpx.TransportError, _RetryableStatus)),
        reraise=True,
    )
