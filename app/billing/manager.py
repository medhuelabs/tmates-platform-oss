"""High-level billing helpers for resolving plans, enforcing limits, and logging usage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.config import CONFIG
from app.db import DatabaseClient

from .plans import PlanContext, PlanLimits, PlanStatus, PlanUsage

DEFAULT_PLAN_KEY = "free"


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0, "0", "false", "False", "no", "off"):
        return False
    if value in (1, "1", "true", "True", "yes", "on"):
        return True
    return bool(value)


class BillingManager:
    """Facade that knows how to resolve plans, enforce quotas, and record usage."""

    def __init__(self, db: DatabaseClient):
        self._db = db

    # ------------------------------------------------------------------
    # Feature flags
    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """Billing is only enforced when the feature flag and Stripe secrets are present."""

        return bool(getattr(CONFIG, "stripe_billing_enabled", False))

    # ------------------------------------------------------------------
    # Plan resolution
    # ------------------------------------------------------------------
    def get_plan_context(
        self,
        organization_id: str,
        *,
        active_agents: Optional[int] = None,
    ) -> PlanContext:
        """Resolve the organization's plan, subscription status, and usage snapshot."""

        subscription = self._ensure_subscription_record(organization_id)
        plan_key = str(subscription.get("plan_key") or DEFAULT_PLAN_KEY)
        plan_record = self._db.get_billing_plan(plan_key) or self._db.get_billing_plan(DEFAULT_PLAN_KEY) or {}

        limits = PlanLimits.from_dict(_safe_json(plan_record.get("limits")))
        usage = self._build_usage_snapshot(
            organization_id=organization_id,
            limits=limits,
            subscription=subscription,
        )

        metadata: Dict[str, Any] = {}
        plan_metadata = _safe_json(plan_record.get("metadata"))
        if isinstance(plan_metadata, dict):
            metadata.update(plan_metadata)
        subscription_metadata = _safe_json(subscription.get("metadata"))
        if isinstance(subscription_metadata, dict):
            metadata.update({f"subscription_{key}": value for key, value in subscription_metadata.items()})
        if active_agents is not None:
            metadata["active_agents"] = active_agents

        provider = str(
            (subscription_metadata or {}).get("provider")
            or subscription.get("provider")
            or "stripe"
        )
        metadata["subscription_provider"] = provider

        cancel_at = _parse_timestamp(
            (subscription_metadata or {}).get("cancel_at") or subscription.get("cancel_at")
        )
        canceled_at = _parse_timestamp(
            (subscription_metadata or {}).get("canceled_at") or subscription.get("canceled_at")
        )
        ended_at = _parse_timestamp(
            (subscription_metadata or {}).get("ended_at") or subscription.get("ended_at")
        )
        cancel_at_period_end = _coerce_bool(subscription.get("cancel_at_period_end")) or (cancel_at is not None)

        period_start = _parse_timestamp(subscription.get("current_period_start"))
        period_end = _parse_timestamp(subscription.get("current_period_end"))
        if period_start is None and isinstance(subscription_metadata, dict):
            period_start = _parse_timestamp(subscription_metadata.get("current_period_start"))
        if period_end is None and isinstance(subscription_metadata, dict):
            period_end = _parse_timestamp(subscription_metadata.get("current_period_end"))

        context = PlanContext(
            plan_key=plan_key,
            plan_name=str(plan_record.get("name") or plan_key.title()),
            category=plan_record.get("category"),
            status=_normalize_status(subscription.get("status"), plan_key),
            billing_interval=str(subscription.get("billing_interval") or "monthly"),
            provider=provider,
            limits=limits,
            usage=usage,
            period_start=period_start,
            period_end=period_end,
            trial_end=_parse_timestamp(subscription.get("trial_end")),
            cancel_at_period_end=cancel_at_period_end,
            cancel_at=cancel_at,
            canceled_at=canceled_at,
            ended_at=ended_at,
            billing_enabled=self.enabled,
            metadata=metadata,
        )

        # Fallback period bounds when Stripe has not supplied them yet.
        if context.period_end is None and cancel_at is not None:
            context.period_end = cancel_at
        if context.period_start is None or context.period_end is None:
            fallback_start, fallback_end = _default_period_bounds(context.billing_interval)
            if context.period_start is None:
                context.period_start = fallback_start
            if context.period_end is None:
                context.period_end = fallback_end

        return context

    # ------------------------------------------------------------------
    # Enforcement helpers
    # ------------------------------------------------------------------
    def agent_limit_error(self, plan: PlanContext, *, active_agents: int) -> Optional[str]:
        """Return an error message if the agent cap has been reached."""

        if not self.enabled:
            return None
        limit = plan.limits.max_agents
        if limit is None or active_agents <= limit:
            return None
        return (
            f"This plan allows up to {limit} agents. "
            "Upgrade your subscription to add more teammates."
        )

    def job_quota_error(self, plan: PlanContext) -> Optional[str]:
        """Return an error message if the action quota has been exhausted."""

        if not self.enabled:
            return None
        if not plan.over_action_quota:
            return None
        quota = plan.limits.monthly_actions
        return (
            f"This plan includes {quota} automated actions per billing period. "
            "Upgrade your subscription or wait for the next cycle to reset usage."
        )

    # ------------------------------------------------------------------
    # Usage logging
    # ------------------------------------------------------------------
    def record_usage(
        self,
        *,
        organization_id: str,
        user_id: Optional[str],
        event_type: str,
        quantity: int = 1,
        cost_usd: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist a usage event for later quota checks (safe no-op when unsupported)."""

        self._db.record_usage_event(
            organization_id=organization_id,
            user_id=user_id,
            event_type=event_type,
            quantity=quantity,
            cost_usd=cost_usd,
            metadata=metadata or {},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_subscription_record(self, organization_id: str) -> Dict[str, Any]:
        record = self._db.get_organization_subscription(organization_id)
        if record:
            return record
        return self._db.ensure_organization_subscription(organization_id, DEFAULT_PLAN_KEY)

    def _build_usage_snapshot(
        self,
        organization_id: str,
        *,
        limits: PlanLimits,
        subscription: Dict[str, Any],
    ) -> PlanUsage:
        period_start = _parse_timestamp(subscription.get("current_period_start"))
        period_end = _parse_timestamp(subscription.get("current_period_end"))
        subscription_metadata = _safe_json(subscription.get("metadata"))
        if period_start is None and isinstance(subscription_metadata, dict):
            period_start = _parse_timestamp(subscription_metadata.get("current_period_start"))
        if period_end is None and isinstance(subscription_metadata, dict):
            period_end = _parse_timestamp(subscription_metadata.get("current_period_end"))
        if period_start is None or period_end is None:
            period_start, period_end = _default_period_bounds(str(subscription.get("billing_interval") or "monthly"))

        usage_totals = self._db.get_usage_totals(
            organization_id=organization_id,
            start=period_start,
            end=period_end,
        )

        return PlanUsage(
            actions_used=int(usage_totals.get("actions", 0)),
            actions_quota=limits.monthly_actions,
            tokens_used=int(usage_totals.get("tokens", 0)),
            tokens_quota=limits.monthly_tokens,
        )


# ----------------------------------------------------------------------
# Utility functions
# ----------------------------------------------------------------------

def _safe_json(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            import json

            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            return None
    return None


def _normalize_status(value: Any, plan_key: Optional[str] = None) -> PlanStatus:
    if not value:
        # For free plans, default to active status instead of unknown
        if plan_key == "free":
            return PlanStatus.ACTIVE
        return PlanStatus.UNKNOWN
    normalised = str(value).strip().lower()
    try:
        return PlanStatus(normalised)
    except ValueError:
        mapping = {
        "trial": PlanStatus.TRIALING,
        "trialing": PlanStatus.TRIALING,
        "active": PlanStatus.ACTIVE,
        "past_due": PlanStatus.PAST_DUE,
        "past-due": PlanStatus.PAST_DUE,
        "incomplete": PlanStatus.INCOMPLETE,
        "incomplete_expired": PlanStatus.INCOMPLETE_EXPIRED,
        "canceled": PlanStatus.CANCELED,
        "cancelled": PlanStatus.CANCELED,
        "unpaid": PlanStatus.UNPAID,
        "paused": PlanStatus.PAUSED,
        }
        return mapping.get(normalised, PlanStatus.UNKNOWN)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _default_period_bounds(interval: str) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    interval_norm = (interval or "monthly").strip().lower()

    if interval_norm in {"annual", "yearly"}:
        year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        next_year = year_start.replace(year=year_start.year + 1)
        return year_start, next_year

    # Default to monthly window
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period_start.month == 12:
        period_end = period_start.replace(year=period_start.year + 1, month=1)
    else:
        period_end = period_start.replace(month=period_start.month + 1)
    return period_start, period_end
