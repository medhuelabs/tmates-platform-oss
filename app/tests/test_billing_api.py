from datetime import datetime, timezone

from app.api.routes.billing import _plan_response_from_context
from app.api.schemas import CheckoutSessionRequest
from app.billing.plans import PlanContext, PlanLimits, PlanStatus, PlanUsage


def test_plan_response_serialization_includes_usage_remaining() -> None:
    usage = PlanUsage(actions_used=10, actions_quota=100)
    limits = PlanLimits(max_agents=5, monthly_actions=100)
    context = PlanContext(
        plan_key="pro",
        plan_name="Pro",
        category="individual",
        status=PlanStatus.ACTIVE,
        billing_interval="monthly",
        limits=limits,
        usage=usage,
        period_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2025, 2, 1, tzinfo=timezone.utc),
        billing_enabled=True,
    )

    response = _plan_response_from_context(context)

    assert response.plan_key == "pro"
    assert response.limits.max_agents == 5
    assert response.usage.actions_used == 10
    assert response.usage.actions_remaining == 90
    assert response.billing_enabled is True
    assert response.period_start == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert response.period_end == datetime(2025, 2, 1, tzinfo=timezone.utc)
    assert response.provider == "stripe"


def test_checkout_session_request_normalizes_interval() -> None:
    request = CheckoutSessionRequest(plan_key="basic", billing_interval="Annually", quantity=1)
    assert request.billing_interval == "annual"

    monthly = CheckoutSessionRequest(plan_key="basic", billing_interval="monthly")
    assert monthly.billing_interval == "monthly"
