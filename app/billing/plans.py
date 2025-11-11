"""Data structures describing billing plans, limits, and usage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class PlanStatus(str, Enum):
    """Normalized subscription status codes."""

    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    PAUSED = "paused"
    UNKNOWN = "unknown"


@dataclass
class PlanLimits:
    """Feature and quota limits associated with a plan."""

    max_agents: Optional[int] = None
    monthly_actions: Optional[int] = None
    monthly_tokens: Optional[int] = None
    max_members: Optional[int] = None

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]]) -> "PlanLimits":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            max_agents=_coerce_int(payload.get("max_agents")),
            monthly_actions=_coerce_int(payload.get("monthly_actions")),
            monthly_tokens=_coerce_int(payload.get("monthly_tokens")),
            max_members=_coerce_int(payload.get("max_members")),
        )


@dataclass
class PlanUsage:
    """Usage snapshot for the active billing period."""

    actions_used: int = 0
    actions_quota: Optional[int] = None
    tokens_used: int = 0
    tokens_quota: Optional[int] = None

    @property
    def actions_remaining(self) -> Optional[int]:
        if self.actions_quota is None:
            return None
        return max(self.actions_quota - self.actions_used, 0)

    @property
    def tokens_remaining(self) -> Optional[int]:
        if self.tokens_quota is None:
            return None
        return max(self.tokens_quota - self.tokens_used, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "actions_used": self.actions_used,
            "actions_quota": self.actions_quota,
            "actions_remaining": self.actions_remaining,
            "tokens_used": self.tokens_used,
            "tokens_quota": self.tokens_quota,
            "tokens_remaining": self.tokens_remaining,
        }


@dataclass
class PlanContext:
    """Resolved plan & subscription details for an organization."""

    plan_key: str
    plan_name: str
    category: Optional[str]
    status: PlanStatus
    billing_interval: str
    provider: str = "stripe"
    limits: PlanLimits = field(default_factory=PlanLimits)
    usage: PlanUsage = field(default_factory=PlanUsage)
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    cancel_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    billing_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status in {PlanStatus.ACTIVE, PlanStatus.TRIALING}

    @property
    def agent_limit_reached(self) -> bool:
        limit = self.limits.max_agents
        current = int(self.metadata.get("active_agents", 0))
        return limit is not None and current >= limit

    @property
    def over_action_quota(self) -> bool:
        actions_quota = self.limits.monthly_actions
        if actions_quota is None:
            return False
        return self.usage.actions_used >= actions_quota

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_key": self.plan_key,
            "plan_name": self.plan_name,
            "category": self.category,
            "status": self.status.value,
            "billing_interval": self.billing_interval,
            "provider": self.provider,
            "limits": {
                "max_agents": self.limits.max_agents,
                "monthly_actions": self.limits.monthly_actions,
                "monthly_tokens": self.limits.monthly_tokens,
                "max_members": self.limits.max_members,
            },
            "usage": self.usage.to_dict(),
            "period_start": _to_iso(self.period_start),
            "period_end": _to_iso(self.period_end),
            "trial_end": _to_iso(self.trial_end),
            "cancel_at_period_end": self.cancel_at_period_end,
            "cancel_at": _to_iso(self.cancel_at),
            "canceled_at": _to_iso(self.canceled_at),
            "ended_at": _to_iso(self.ended_at),
            "billing_enabled": self.billing_enabled,
            "metadata": dict(self.metadata),
        }


def _coerce_int(value: Any) -> Optional[int]:
    if value in (None, "", "null", "None"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return str(value)
    except Exception:
        return None
