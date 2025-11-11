"""Billing provider registry and helpers."""

from __future__ import annotations

from functools import lru_cache
from typing import Callable, Dict, Optional

from app.config import CONFIG

from .base import BillingProvider, ProviderNotConfiguredError
from .stripe_provider import StripeBillingProvider, BillingPortalNotConfiguredError

_REGISTRY_FACTORIES: Dict[str, Callable[[], BillingProvider]] = {
    "stripe": StripeBillingProvider,
}


@lru_cache()
def _get_provider_instance(provider_key: str) -> BillingProvider:
    factory = _REGISTRY_FACTORIES.get(provider_key)
    if factory is None:
        raise KeyError(f"Unknown billing provider: {provider_key}")
    return factory()


def get_billing_provider(provider_key: Optional[str] = None) -> BillingProvider:
    key = (provider_key or getattr(CONFIG, "billing_default_provider", "stripe")).strip().lower()
    return _get_provider_instance(key)


__all__ = [
    "BillingProvider",
    "BillingPortalNotConfiguredError",
    "ProviderNotConfiguredError",
    "get_billing_provider",
]
