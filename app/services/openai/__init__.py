"""
OpenAI service client and response handling.

Provides centralized OpenAI/Azure OpenAI integration with automatic
client configuration and response metrics tracking.
"""

from .client import (
    openai_client,
    call_response_with_metrics,
    MODEL_PRICING,
)

try:  # Legacy compatibility for code importing Response from this module.
    from requests.models import Response as _RequestsResponse
except Exception:  # pragma: no cover - requests may not be available during bootstrap
    class _RequestsResponse:  # type: ignore[too-many-ancestors]
        pass

Response = _RequestsResponse

__all__ = [
    "openai_client",
    "call_response_with_metrics",
    "MODEL_PRICING",
    "Response",
]