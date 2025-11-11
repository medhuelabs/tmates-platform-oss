"""Thin wrapper around the Stripe SDK used for subscription management."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.config import CONFIG

try:  # pragma: no cover - optional dependency
    import stripe
except ImportError:  # pragma: no cover - handled at runtime
    stripe = None

if stripe is not None:
    InvalidRequestError = getattr(getattr(stripe, "error", object), "InvalidRequestError", Exception)
else:  # pragma: no cover - handled at runtime
    InvalidRequestError = Exception


class BillingPortalNotConfiguredError(RuntimeError):
    """Raised when the Stripe billing portal is not configured for the environment."""


class StripeBillingService:
    """Handles Stripe interactions required for hosted billing."""

    def __init__(self, secret_key: str, *, webhook_secret: Optional[str] = None):
        if not secret_key:
            raise ValueError("Stripe secret key is required")
        if stripe is None:
            raise RuntimeError("Stripe SDK is not installed")
        stripe.api_key = secret_key
        self._webhook_secret = webhook_secret

    # ------------------------------------------------------------------
    # Checkout & Portal
    # ------------------------------------------------------------------
    def create_checkout_session(
        self,
        *,
        plan: Dict[str, Any],
        organization: Dict[str, Any],
        subscription: Dict[str, Any],
        interval: str,
        success_url: str,
        cancel_url: str,
        quantity: int = 1,
        customer_email: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """Create a hosted payment page for upgrading or activating a plan."""

        price_id = _resolve_price_id(plan, interval)
        if not price_id:
            raise ValueError("Plan is not configured with a Stripe price for this interval")

        customer_id = subscription.get("stripe_customer_id")
        metadata = {
            "organization_id": organization.get("id"),
            "plan_key": plan.get("key"),
        }

        def _create_customer() -> str:
            customer = stripe.Customer.create(
                name=organization.get("name"),
                email=customer_email,
                metadata=metadata,
            )
            return customer["id"]

        def _create_session(active_customer_id: str):
            return stripe.checkout.Session.create(
                mode="subscription",
                customer=active_customer_id,
                line_items=[
                    {
                        "price": price_id,
                        "quantity": quantity,
                    }
                ],
                success_url=success_url,
                cancel_url=cancel_url,
                subscription_data={
                    "metadata": metadata,
                },
                allow_promotion_codes=True,
                metadata=metadata,
            )

        if not customer_id:
            customer_id = _create_customer()

        try:
            session = _create_session(customer_id)
        except InvalidRequestError as exc:
            error_message = str(exc).lower()
            if (
                customer_id
                and getattr(exc, "code", None) == "resource_missing"
                and "no such customer" in error_message
            ):
                # Customer belongs to a different Stripe environment (live vs test). Re-create.
                customer_id = _create_customer()
                session = _create_session(customer_id)
            else:
                raise

        return session, customer_id

    def create_billing_portal_session(self, *, customer_id: str, return_url: str) -> Dict[str, Any]:
        if not customer_id:
            raise ValueError("Billing portal requires an existing Stripe customer id")
        try:
            return stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
        except InvalidRequestError as exc:  # pragma: no cover - requires live Stripe API
            error_message = (str(exc) or "").lower()
            if "portal" in error_message and "configuration" in error_message:
                raise BillingPortalNotConfiguredError("Stripe billing portal configuration is missing") from exc
            raise

    # ------------------------------------------------------------------
    # Webhooks & subscriptions
    # ------------------------------------------------------------------
    def parse_event(self, payload: bytes, signature: str) -> Any:
        """Validate and parse a Stripe webhook event."""

        if not self._webhook_secret:
            raise RuntimeError("Stripe webhook secret is not configured; cannot verify signatures")
        return stripe.Webhook.construct_event(payload, signature, self._webhook_secret)

    def retrieve_subscription(self, subscription_id: str) -> Any:
        return stripe.Subscription.retrieve(subscription_id, expand=["latest_invoice", "customer"])

    def find_subscription_for_customer(self, customer_id: str) -> Optional[Any]:
        if not customer_id:
            return None
        try:
            result = stripe.Subscription.list(
                customer=customer_id,
                status="all",
                limit=1,
                expand=["data.latest_invoice", "data.customer"],
            )
        except InvalidRequestError:
            return None
        data = getattr(result, "data", None)
        if isinstance(data, list) and data:
            return data[0]
        return None


def _resolve_price_id(plan: Dict[str, Any], interval: str) -> Optional[str]:
    interval_norm = (interval or "monthly").strip().lower()
    override = _lookup_price_override(plan, interval_norm)
    if override:
        return override
    if interval_norm in {"annual", "yearly"}:
        return plan.get("stripe_price_yearly_id")
    return plan.get("stripe_price_monthly_id")


def _lookup_price_override(plan: Dict[str, Any], interval: str) -> Optional[str]:
    overrides = getattr(CONFIG, "stripe_price_overrides", {}) or {}
    if not overrides:
        return None

    plan_key = plan.get("key")
    if plan_key is None:
        return None

    plan_overrides = overrides.get(plan_key)
    if plan_overrides is None:
        plan_overrides = overrides.get(str(plan_key))
    if not isinstance(plan_overrides, dict):
        return None

    price_id = plan_overrides.get(interval)
    if price_id:
        return price_id
    return None
