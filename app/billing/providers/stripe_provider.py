"""Stripe implementation of the billing provider interface."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.config import CONFIG

from ..stripe_service import BillingPortalNotConfiguredError, StripeBillingService
from .base import BillingProvider, ProviderNotConfiguredError


class StripeBillingProvider(BillingProvider):
    key = "stripe"

    def __init__(self) -> None:
        self._service: Optional[StripeBillingService] = None

    def is_configured(self) -> bool:
        return bool(getattr(CONFIG, "stripe_secret_key", None))

    def _ensure_service(self) -> StripeBillingService:
        if not self.is_configured():
            raise ProviderNotConfiguredError("Stripe billing is not configured")
        if self._service is None:
            secret_key = getattr(CONFIG, "stripe_secret_key", None)
            webhook_secret = getattr(CONFIG, "stripe_webhook_secret", None)
            self._service = StripeBillingService(secret_key, webhook_secret=webhook_secret)
        return self._service

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
        service = self._ensure_service()
        return service.create_checkout_session(
            plan=plan,
            organization=organization,
            subscription=subscription,
            interval=interval,
            success_url=success_url,
            cancel_url=cancel_url,
            quantity=quantity,
            customer_email=customer_email,
        )

    def create_billing_portal_session(
        self,
        *,
        customer_id: str,
        return_url: str,
    ) -> Dict[str, Any]:
        service = self._ensure_service()
        return service.create_billing_portal_session(customer_id=customer_id, return_url=return_url)

    def retrieve_subscription(self, subscription_id: str) -> Dict[str, Any]:
        service = self._ensure_service()
        return service.retrieve_subscription(subscription_id)

    def find_subscription_for_customer(self, customer_id: Optional[str]) -> Optional[Dict[str, Any]]:
        service = self._ensure_service()
        return service.find_subscription_for_customer(customer_id)

    def parse_event(self, payload: bytes, signature: str) -> Dict[str, Any]:
        service = self._ensure_service()
        return service.parse_event(payload, signature)


__all__ = [
    "StripeBillingProvider",
    "BillingPortalNotConfiguredError",
]
