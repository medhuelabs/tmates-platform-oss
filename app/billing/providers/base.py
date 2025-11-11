"""Provider abstraction for handling billing operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple


class ProviderNotConfiguredError(RuntimeError):
    """Raised when a billing provider is missing required configuration."""


class BillingProvider(ABC):
    """Interface for payment providers (Stripe, Apple, Google, etc.)."""

    key: str

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True when the provider has the secrets it needs."""

    @abstractmethod
    def create_checkout_session(
        self,
        *,
        plan: Dict[str, Any],
        organization: Dict[str, Any],
        subscription: Dict[str, Any],
        interval: str,
        success_url: str,
        cancel_url: str,
        quantity: int,
        customer_email: Optional[str],
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """Create a purchase session (Stripe Checkout, Apple App Store, etc.)."""

    @abstractmethod
    def create_billing_portal_session(
        self,
        *,
        customer_id: str,
        return_url: str,
    ) -> Dict[str, Any]:
        """Return a portal URL for managing the subscription."""

    @abstractmethod
    def retrieve_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Fetch the latest subscription object from the provider."""

    @abstractmethod
    def find_subscription_for_customer(self, customer_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Lookup an active subscription for the given customer, if any."""

    @abstractmethod
    def parse_event(self, payload: bytes, signature: str) -> Dict[str, Any]:
        """Validate and decode webhook payloads for the provider."""
