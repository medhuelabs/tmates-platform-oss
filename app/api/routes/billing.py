"""Billing-related API endpoints (Stripe checkout, portal, plan info)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_authenticated_user, get_current_user_id, get_database
from app.api.schemas import (
    BillingOnboardingRequest,
    BillingOnboardingResponse,
    BillingLimits,
    BillingPlanResponse,
    BillingPortalRequest,
    BillingPortalResponse,
    BillingUsage,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    OrganizationSummary,
    PublicPricingPlan,
    PublicPricingResponse,
    SubscriptionSummary,
    UserProfile,
)
from app.billing import (
    BillingManager,
    BillingPortalNotConfiguredError,
    ProviderNotConfiguredError,
    get_billing_provider,
)
from app.billing.plans import PlanContext
from app.config import CONFIG
from app.db import DatabaseClient
from app.registry.agents.store import AgentStore


TEAM_CHAT_TITLE = "Team Chat"
TEAM_CHAT_SLUG = "group:all"
TEAM_CHAT_KIND = "group"
ONBOARDING_AGENT_KEY = "adam"
_AGENT_STORE = AgentStore()
_ADAM_DM_GREETING = (
    "Hi, I'm Adam. I'm here to help you get comfortable inside Tmates. "
    "Ask me about drafting an email, planning your day, or inviting another teammate when you're ready."
)
_ADAM_TEAM_GREETING = (
    "Adam here. I set up this team chat so everyone can coordinate. "
    "Ping me whenever you need a quick recap or want to loop in a specialist."
)


def _normalize_agent_keys(raw_value: Any) -> List[str]:
    if isinstance(raw_value, list):
        return [str(entry) for entry in raw_value if entry]
    if isinstance(raw_value, str):
        import json

        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(entry) for entry in parsed if entry]
    return []


def _lookup_agent_name(agent_key: str) -> str:
    try:
        definition = _AGENT_STORE.get_agent(agent_key)
    except Exception:
        definition = None
    if definition and getattr(definition, "name", None):
        return str(definition.name)
    return agent_key.title()


def _has_onboarding_message(messages: Sequence[Dict[str, Any]]) -> bool:
    for message in messages:
        payload = message.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("onboarding") and payload.get("agent_key") == ONBOARDING_AGENT_KEY:
            return True
    return False


def _ensure_dm_thread_with_welcome(
    db: DatabaseClient,
    *,
    user_id: str,
    organization_id: str,
) -> None:
    agent_name = _lookup_agent_name(ONBOARDING_AGENT_KEY)
    try:
        threads = db.list_chat_threads(
            user_id,
            organization_id=organization_id,
            limit=200,
        )
    except Exception as exc:
        print(f"Failed to list chat threads during onboarding DM seed: {exc}")
        threads = []

    target_thread: Optional[Dict[str, Any]] = None
    for thread in threads or []:
        agent_keys = _normalize_agent_keys(thread.get("agent_keys"))
        metadata = thread.get("metadata") or {}
        metadata_keys = _normalize_agent_keys(metadata.get("agent_keys"))
        if ONBOARDING_AGENT_KEY in agent_keys or ONBOARDING_AGENT_KEY in metadata_keys:
            if len(agent_keys) <= 1 and len(metadata_keys) <= 1:
                target_thread = thread
                break

    created_thread = False
    if not target_thread:
        try:
            thread = db.create_chat_thread(
                auth_user_id=user_id,
                organization_id=organization_id,
                title=agent_name,
                kind="agent",
                agent_keys=[ONBOARDING_AGENT_KEY],
                metadata={
                    "agent_key": ONBOARDING_AGENT_KEY,
                    "agent_name": agent_name,
                    "created_via": "onboarding_seed",
                },
            )
            if thread:
                target_thread = thread
                created_thread = True
        except Exception as exc:
            print(f"Failed to create onboarding DM thread: {exc}")
            return

    if not target_thread:
        return

    try:
        history = db.list_chat_messages(target_thread.get("id"), limit=10, ascending=True)
    except Exception as exc:
        print(f"Failed to list onboarding DM messages: {exc}")
        history = []

    if history and _has_onboarding_message(history):
        return
    if history and not created_thread:
        return

    try:
        db.insert_chat_message(
            thread_id=target_thread.get("id"),
            role="assistant",
            content=_ADAM_DM_GREETING,
            author=agent_name,
            payload={"agent_key": ONBOARDING_AGENT_KEY, "onboarding": True},
            organization_id=organization_id,
            user_id=user_id,
        )
        db.touch_chat_thread(target_thread.get("id"))
    except Exception as exc:
        print(f"Failed to insert onboarding DM welcome message: {exc}")


def _ensure_team_chat_with_welcome(
    db: DatabaseClient,
    *,
    user_id: str,
    organization_id: str,
) -> None:
    agent_name = _lookup_agent_name(ONBOARDING_AGENT_KEY)
    try:
        threads = db.list_chat_threads(
            user_id,
            organization_id=organization_id,
            limit=200,
        )
    except Exception as exc:
        print(f"Failed to list threads during team chat seed: {exc}")
        threads = []

    team_thread: Optional[Dict[str, Any]] = None
    for thread in threads or []:
        metadata = thread.get("metadata") or {}
        slug = metadata.get("slug")
        title = str(thread.get("title") or "").strip().lower()
        if slug == TEAM_CHAT_SLUG or title == TEAM_CHAT_TITLE.lower():
            team_thread = thread
            break

    if not team_thread:
        try:
            thread = db.create_chat_thread(
                auth_user_id=user_id,
                organization_id=organization_id,
                title=TEAM_CHAT_TITLE,
                kind=TEAM_CHAT_KIND,
                agent_keys=[ONBOARDING_AGENT_KEY],
                metadata={
                    "slug": TEAM_CHAT_SLUG,
                    "created_via": "onboarding_seed",
                    "agent_keys": [ONBOARDING_AGENT_KEY],
                },
            )
            if thread:
                team_thread = thread
        except Exception as exc:
            print(f"Failed to create team chat thread during onboarding: {exc}")
            return

    if not team_thread:
        return

    try:
        history = db.list_chat_messages(team_thread.get("id"), limit=10, ascending=True)
    except Exception as exc:
        print(f"Failed to list team chat messages during onboarding seed: {exc}")
        history = []

    if history and _has_onboarding_message(history):
        return
    if history:
        return

    try:
        db.insert_chat_message(
            thread_id=team_thread.get("id"),
            role="assistant",
            content=_ADAM_TEAM_GREETING,
            author=agent_name,
            payload={
                "agent_key": ONBOARDING_AGENT_KEY,
                "onboarding": True,
                "channel": "team_chat",
            },
            organization_id=organization_id,
            user_id=user_id,
        )
        db.touch_chat_thread(team_thread.get("id"))
    except Exception as exc:
        print(f"Failed to insert team chat welcome message: {exc}")



router = APIRouter()


def _plan_response_from_context(context: PlanContext) -> BillingPlanResponse:
    usage = BillingUsage(**context.usage.to_dict())
    limits = BillingLimits(
        max_agents=context.limits.max_agents,
        monthly_actions=context.limits.monthly_actions,
        monthly_tokens=context.limits.monthly_tokens,
        max_members=context.limits.max_members,
    )
    return BillingPlanResponse(
        plan_key=context.plan_key,
        plan_name=context.plan_name,
        category=context.category,
        status=context.status.value,
        billing_interval=context.billing_interval,
        provider=context.provider,
        limits=limits,
        usage=usage,
        period_start=context.period_start,
        period_end=context.period_end,
        trial_end=context.trial_end,
        cancel_at_period_end=context.cancel_at_period_end,
        cancel_at=context.cancel_at,
        canceled_at=context.canceled_at,
        ended_at=context.ended_at,
        billing_enabled=context.billing_enabled,
    )


def _require_billing_provider(*, allow_disabled: bool = False):
    try:
        provider = get_billing_provider()
    except KeyError as exc:
        if allow_disabled:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing provider is not available") from exc
        raise RuntimeError("Billing provider is not available") from exc
    if not provider.is_configured():
        if allow_disabled:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing provider is not configured")
        raise RuntimeError("Billing provider is not configured")
    return provider


def _is_owner_role(role_value: Optional[str]) -> bool:
    if not role_value:
        return False
    return role_value.strip().lower() in {"owner", "admin"}


@router.post("/billing/onboard", response_model=BillingOnboardingResponse, status_code=status.HTTP_200_OK)
def onboard_billing_customer(
    request: BillingOnboardingRequest,
    auth_user: Dict[str, Any] = Depends(get_authenticated_user),
    db: DatabaseClient = Depends(get_database),
) -> BillingOnboardingResponse:
    """Ensure the authenticated user has a profile, organization, and subscription placeholder."""

    user_id = auth_user.get("id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user information is missing",
        )

    metadata = auth_user.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    email = (request.email or auth_user.get("email") or metadata.get("email"))
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required to complete onboarding",
        )

    display_name = request.display_name or metadata.get("display_name")
    if not display_name:
        first_name = str(metadata.get("first_name") or "").strip()
        last_name = str(metadata.get("last_name") or "").strip()
        combined = " ".join(part for part in (first_name, last_name) if part)
        display_name = combined or email.split("@")[0]

    organization_name = (
        request.organization_name
        or metadata.get("organization_name")
        or metadata.get("company")
        or metadata.get("company_name")
        or display_name
    )

    try:
        setup_result = db.setup_new_user(
            user_id,
            email,
            organization_name,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize user workspace: {exc}",
        ) from exc

    if not setup_result:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initialize user workspace",
        )

    profile_record = db.get_user_profile_by_auth_id(user_id)
    if not profile_record:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User profile is unavailable",
        )

    organization_record = db.get_user_organization(user_id)
    if not organization_record:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Organization setup is incomplete",
        )

    org_id = organization_record.get("id") or organization_record.get("organization_id")
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Organization identifier is missing",
        )

    subscription_record = db.ensure_organization_subscription(
        org_id,
        "free",
    )

    str_org_id = str(org_id)

    try:
        _ensure_dm_thread_with_welcome(
            db,
            user_id=user_id,
            organization_id=str_org_id,
        )
        _ensure_team_chat_with_welcome(
            db,
            user_id=user_id,
            organization_id=str_org_id,
        )
    except Exception as onboarding_exc:  # pragma: no cover - defensive onboarding seed
        print(f"Failed to seed onboarding chat content: {onboarding_exc}")

    updates: Dict[str, Any] = {}
    plan_key = request.plan_key
    if plan_key:
        plan = db.get_billing_plan(plan_key)
        if not plan or not plan.get("is_active"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Requested plan is unavailable",
            )
        updates["plan_key"] = plan_key
        updates["status"] = "pending_checkout"

    normalized_interval = "monthly"
    if request.billing_interval == "annual":
        normalized_interval = "yearly"

    if updates:
        updates["billing_interval"] = normalized_interval
    db.update_organization_subscription(org_id, updates)
    subscription_record = db.get_organization_subscription(org_id)

    def _coerce_dict(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                import json

                parsed = json.loads(value)
            except (TypeError, ValueError):
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}

    profile_model = UserProfile(
        id=user_id,
        email=email,
        display_name=display_name,
        avatar_url=profile_record.get("avatar_url"),
    )

    organization_model = OrganizationSummary(
        id=str_org_id,
        name=organization_record.get("name"),
        type=organization_record.get("type"),
        settings=_coerce_dict(organization_record.get("settings")),
    )

    subscription_model: Optional[SubscriptionSummary] = None
    if subscription_record:
        subscription_metadata = _coerce_dict(subscription_record.get("metadata"))
        provider_value = subscription_metadata.get("provider") or subscription_record.get("provider")
        subscription_model = SubscriptionSummary(
            organization_id=str(
                subscription_record.get("organization_id") or org_id
            ),
            plan_key=subscription_record.get("plan_key"),
            status=subscription_record.get("status"),
            billing_interval=subscription_record.get("billing_interval"),
            stripe_customer_id=subscription_record.get("stripe_customer_id"),
            stripe_subscription_id=subscription_record.get("stripe_subscription_id"),
            provider=provider_value,
        )

    return BillingOnboardingResponse(
        profile=profile_model,
        organization=organization_model,
        subscription=subscription_model,
    )


@router.get("/billing/plan", response_model=BillingPlanResponse, status_code=status.HTTP_200_OK)
def get_billing_plan(
    user_id: str = Depends(get_current_user_id),
    db: DatabaseClient = Depends(get_database),
) -> BillingPlanResponse:
    organization = db.get_user_organization(user_id)
    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    manager = BillingManager(db)
    active_agents = db.get_organization_agents(organization["id"]) or []
    try:
        plan_context = manager.get_plan_context(organization["id"], active_agents=len(active_agents))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to resolve billing plan") from exc

    return _plan_response_from_context(plan_context)


@router.get("/billing/pricing", response_model=PublicPricingResponse, status_code=status.HTTP_200_OK)
def get_public_pricing(db: DatabaseClient = Depends(get_database)) -> PublicPricingResponse:
    """Get public pricing information without authentication."""
    import json
    
    plans = db.list_billing_plans(include_inactive=False)
    
    public_plans = []
    for plan in plans:
        # Parse JSON strings from database
        limits = plan.get("limits", {})
        if isinstance(limits, str):
            try:
                limits = json.loads(limits)
            except (json.JSONDecodeError, TypeError):
                limits = {}
        
        metadata = plan.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        
        public_plan = PublicPricingPlan(
            key=plan["key"],
            name=plan["name"],
            description=plan.get("description"),
            category=plan.get("category", "individual"),
            stripe_product_id=plan.get("stripe_product_id"),
            stripe_price_monthly_id=plan.get("stripe_price_monthly_id"),
            stripe_price_yearly_id=plan.get("stripe_price_yearly_id"),
            limits=limits,
            metadata=metadata,
            sort_order=plan.get("sort_order", 0)
        )
        public_plans.append(public_plan)
    
    # Sort by sort_order for consistent presentation
    public_plans.sort(key=lambda x: x.sort_order)
    
    return PublicPricingResponse(plans=public_plans)


@router.post("/billing/checkout", response_model=CheckoutSessionResponse, status_code=status.HTTP_200_OK)
def create_checkout_session(
    request: CheckoutSessionRequest,
    user_id: str = Depends(get_current_user_id),
    db: DatabaseClient = Depends(get_database),
) -> CheckoutSessionResponse:
    manager = BillingManager(db)
    if not manager.enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing is not enabled")

    organization = db.get_user_organization(user_id)
    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    membership = db.get_organization_membership(user_id, organization["id"])
    if not _is_owner_role((membership or {}).get("role")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only organization owners can manage billing")

    plan = db.get_billing_plan(request.plan_key)
    if not plan or not plan.get("is_active"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requested plan is unavailable")

    interval = (request.billing_interval or "monthly").strip().lower()
    if interval not in {"monthly", "annual", "yearly"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="billing_interval must be monthly or annual")

    success_url = request.success_url or getattr(CONFIG, "stripe_checkout_success_url", None)
    cancel_url = request.cancel_url or getattr(CONFIG, "stripe_checkout_cancel_url", None)
    if not success_url or not cancel_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Checkout success and cancel URLs are required")

    subscription = db.ensure_organization_subscription(organization["id"], plan["key"])
    primary_user = db.get_user_profile_by_auth_id(user_id) if hasattr(db, "get_user_profile_by_auth_id") else None
    customer_email = (primary_user or {}).get("email")

    provider = _require_billing_provider(allow_disabled=False)
    try:
        session, customer_id = provider.create_checkout_session(
            plan=plan,
            organization=organization,
            subscription=subscription or {},
            interval=interval,
            success_url=success_url,
            cancel_url=cancel_url,
            quantity=request.quantity or 1,
            customer_email=customer_email,
        )
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    update_payload: Dict[str, Any] = {
        "plan_key": plan["key"],
        "billing_interval": "yearly" if interval in {"annual", "yearly"} else "monthly",
        "status": "pending_checkout",
    }
    if customer_id and customer_id != subscription.get("stripe_customer_id"):
        update_payload["stripe_customer_id"] = customer_id
    db.update_organization_subscription(organization["id"], update_payload)

    return CheckoutSessionResponse(checkout_url=session["url"], session_id=session["id"])


@router.post("/billing/portal", response_model=BillingPortalResponse, status_code=status.HTTP_200_OK)
def create_billing_portal_session(
    request: BillingPortalRequest,
    user_id: str = Depends(get_current_user_id),
    db: DatabaseClient = Depends(get_database),
) -> BillingPortalResponse:
    manager = BillingManager(db)
    if not manager.enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing is not enabled")

    organization = db.get_user_organization(user_id)
    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    membership = db.get_organization_membership(user_id, organization["id"])
    if not _is_owner_role((membership or {}).get("role")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only organization owners can access the billing portal")

    subscription = db.get_organization_subscription(organization["id"]) or {}
    customer_id = subscription.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Billing portal requires an active Stripe customer")

    return_url = request.return_url or getattr(CONFIG, "stripe_portal_return_url", None) or getattr(CONFIG, "stripe_checkout_success_url", None)
    if not return_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Portal return URL is required")

    provider = _require_billing_provider(allow_disabled=False)
    try:
        portal_session = provider.create_billing_portal_session(customer_id=customer_id, return_url=return_url)
    except ProviderNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except BillingPortalNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return BillingPortalResponse(url=portal_session["url"])


@router.post("/billing/sync", response_model=BillingPlanResponse, status_code=status.HTTP_200_OK)
def sync_billing_subscription(
    user_id: str = Depends(get_current_user_id),
    db: DatabaseClient = Depends(get_database),
) -> BillingPlanResponse:
    organization = db.get_user_organization(user_id)
    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    membership = db.get_organization_membership(user_id, organization["id"])
    if not _is_owner_role((membership or {}).get("role")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only organization owners can sync billing")

    manager = BillingManager(db)
    if not manager.enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing is not enabled")

    subscription_record = db.get_organization_subscription(organization["id"])
    if not subscription_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription record not found")

    provider = _require_billing_provider(allow_disabled=False)
    subscription_obj: Optional[Dict[str, Any]] = None

    subscription_id = subscription_record.get("stripe_subscription_id")
    if subscription_id:
        try:
            subscription_obj = provider.retrieve_subscription(subscription_id)
        except Exception:
            subscription_obj = None

    if subscription_obj is None:
        customer_id = subscription_record.get("stripe_customer_id")
        subscription_obj = provider.find_subscription_for_customer(customer_id) if customer_id else None

    if not subscription_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stripe subscription not found")

    subscription_obj["provider"] = provider.key
    _apply_subscription_update(db, organization["id"], subscription_obj)

    active_agents = db.get_organization_agents(organization["id"]) or []
    plan_context = manager.get_plan_context(organization["id"], active_agents=len(active_agents))
    return _plan_response_from_context(plan_context)


@router.post("/billing/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request, db: DatabaseClient = Depends(get_database)) -> Dict[str, Any]:
    provider = _require_billing_provider(allow_disabled=False)
    payload = await request.body()
    signature = request.headers.get("Stripe-Signature", "")

    try:
        event = provider.parse_event(payload, signature)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid webhook payload: {exc}")

    event_id = event.get("id")
    if event_id and hasattr(db, "has_subscription_event") and db.has_subscription_event(event_id):
        return {"processed": False, "status": "duplicate"}

    handled = False
    org_id: Optional[str] = None
    event_type = event.get("type") or "unknown"

    try:
        if event_type == "checkout.session.completed":
            session_obj = event["data"]["object"]
            subscription_id = session_obj.get("subscription")
            if subscription_id:
                subscription_obj = provider.retrieve_subscription(subscription_id)
                subscription_obj["provider"] = provider.key
                org_id = _resolve_subscription_org(db, subscription_obj)
                if org_id:
                    _apply_subscription_update(db, org_id, subscription_obj)
                    handled = True
        elif event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
            subscription_obj = event["data"]["object"]
            subscription_id = subscription_obj.get("id")
            if subscription_id:
                try:
                    subscription_obj = provider.retrieve_subscription(subscription_id)
                except Exception:
                    # Fallback to original event payload when retrieval fails.
                    pass
            subscription_obj["provider"] = provider.key
            org_id = _resolve_subscription_org(db, subscription_obj)
            if org_id:
                _apply_subscription_update(db, org_id, subscription_obj)
                handled = True
        elif event_type == "invoice.payment_failed":
            invoice_obj = event["data"]["object"]
            customer_id = invoice_obj.get("customer")
            record = db.get_subscription_by_customer_id(customer_id) if customer_id else None
            if record:
                org_id = record.get("organization_id")
                db.update_organization_subscription(org_id, {"status": "past_due"})
                handled = True
    except Exception as exc:  # pragma: no cover - defensive guard for webhook processing
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Webhook handling failed: {exc}")

    if handled and event_id and hasattr(db, "record_subscription_event"):
        db.record_subscription_event(
            organization_id=org_id,
            stripe_event_id=event_id,
            event_type=event_type,
            payload=event.get("data", {}).get("object", {}),
        )

    return {"processed": handled}


def _resolve_subscription_org(db: DatabaseClient, subscription: Dict[str, Any]) -> Optional[str]:
    metadata = subscription.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            import json

            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            metadata = {}
    org_id = metadata.get("organization_id")
    if org_id:
        return org_id
    customer_id = subscription.get("customer")
    if customer_id:
        record = db.get_subscription_by_customer_id(customer_id)
        if record:
            return record.get("organization_id")
    subscription_id = subscription.get("id")
    if subscription_id:
        record = db.get_subscription_by_subscription_id(subscription_id)
        if record:
            return record.get("organization_id")
    return None


def _apply_subscription_update(db: DatabaseClient, organization_id: str, subscription: Dict[str, Any]) -> None:
    billing_interval = "monthly"
    quantity: Optional[int] = None
    stripe_price_id: Optional[str] = None
    items = subscription.get("items", {})
    if isinstance(items, dict):
        data = items.get("data") or []
        if data:
            first_item = data[0]
            plan_obj = first_item.get("plan") or {}
            price_obj = first_item.get("price") or plan_obj
            interval_value = (price_obj or {}).get("interval")
            if interval_value:
                billing_interval = str(interval_value)
            quantity_value = first_item.get("quantity")
            if quantity_value is not None:
                try:
                    quantity = int(quantity_value)
                except (TypeError, ValueError):
                    quantity = None
            stripe_price_id = (price_obj or {}).get("id")

    metadata = subscription.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            import json

            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            metadata = {}
    metadata_payload: Dict[str, Any] = {}
    if isinstance(metadata, dict):
        metadata_payload.update(metadata)

    plan_key = metadata.get("plan_key") or metadata.get("plan")
    status_value = subscription.get("status") or "active"

    current_period_start, current_period_end = _resolve_period_bounds(subscription, metadata_payload)
    trial_start = _timestamp_to_iso(subscription.get("trial_start"))
    trial_end = _timestamp_to_iso(subscription.get("trial_end"))
    cancel_at = _timestamp_to_iso(subscription.get("cancel_at"))
    canceled_at = _timestamp_to_iso(subscription.get("canceled_at"))
    ended_at = _timestamp_to_iso(subscription.get("ended_at"))

    if current_period_end is None and cancel_at is not None:
        current_period_end = cancel_at

    cancel_flag = subscription.get("cancel_at_period_end")
    if cancel_at is not None:
        cancel_flag = True

    customer_value = subscription.get("customer")
    if isinstance(customer_value, dict):
        customer_id_value = customer_value.get("id")
    else:
        customer_id_value = customer_value

    updates: Dict[str, Any] = {
        "status": status_value,
        "billing_interval": billing_interval,
        "stripe_customer_id": customer_id_value,
        "stripe_subscription_id": subscription.get("id"),
        "stripe_price_id": stripe_price_id,
        "plan_key": plan_key or "free",
        "current_period_start": current_period_start,
        "current_period_end": current_period_end,
        "trial_start": trial_start,
        "trial_end": trial_end,
        "cancel_at_period_end": bool(cancel_flag),
    }
    if quantity is not None:
        updates["quantity"] = quantity
    provider_value = metadata_payload.get("provider") or subscription.get("provider") or "stripe"
    metadata_payload["provider"] = provider_value

    if cancel_at is not None:
        metadata_payload["cancel_at"] = cancel_at
    else:
        metadata_payload.pop("cancel_at", None)
    if canceled_at is not None:
        metadata_payload["canceled_at"] = canceled_at
    else:
        metadata_payload.pop("canceled_at", None)
    if ended_at is not None:
        metadata_payload["ended_at"] = ended_at
    else:
        metadata_payload.pop("ended_at", None)
    if current_period_start is not None:
        metadata_payload["current_period_start"] = current_period_start
    else:
        metadata_payload.pop("current_period_start", None)
    if current_period_end is not None:
        metadata_payload["current_period_end"] = current_period_end
    else:
        metadata_payload.pop("current_period_end", None)
    updates["metadata"] = metadata_payload

    db.update_organization_subscription(organization_id, updates)


def _timestamp_to_iso(value: Any) -> Optional[str]:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isdigit():
            return datetime.fromtimestamp(int(raw), tz=timezone.utc).isoformat()
        normalised = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(normalised)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    return None


def _resolve_period_bounds(subscription: Dict[str, Any], existing_metadata: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    start_iso = _timestamp_to_iso(subscription.get("current_period_start"))
    end_iso = _timestamp_to_iso(subscription.get("current_period_end"))

    if start_iso is None or end_iso is None:
        latest_invoice = subscription.get("latest_invoice")
        if isinstance(latest_invoice, dict):
            invoice_start = _timestamp_to_iso(latest_invoice.get("period_start"))
            invoice_end = _timestamp_to_iso(latest_invoice.get("period_end"))
            if start_iso is None and invoice_start is not None:
                start_iso = invoice_start
            if end_iso is None and invoice_end is not None:
                end_iso = invoice_end

            lines = latest_invoice.get("lines")
            if isinstance(lines, dict):
                for line in lines.get("data") or []:
                    if not isinstance(line, dict):
                        continue
                    period = line.get("period")
                    if not isinstance(period, dict):
                        continue
                    line_start = _timestamp_to_iso(period.get("start"))
                    line_end = _timestamp_to_iso(period.get("end"))
                    if start_iso is None and line_start is not None:
                        start_iso = line_start
                    if end_iso is None and line_end is not None:
                        end_iso = line_end
                    if start_iso is not None and end_iso is not None:
                        break

    if start_iso is None:
        existing_start = existing_metadata.get("current_period_start")
        if existing_start is not None:
            start_iso = _timestamp_to_iso(existing_start)
            if start_iso is None and isinstance(existing_start, str):
                start_iso = existing_start

    if end_iso is None:
        existing_end = existing_metadata.get("current_period_end")
        if existing_end is not None:
            end_iso = _timestamp_to_iso(existing_end)
            if end_iso is None and isinstance(existing_end, str):
                end_iso = existing_end

    return start_iso, end_iso


@router.get("/auth/user/status", status_code=status.HTTP_200_OK)
def get_user_status(
    user_id: str = Depends(get_current_user_id),
    db: DatabaseClient = Depends(get_database),
) -> Dict[str, Any]:
    """Check if the authenticated user already has an organization."""
    try:
        organization = db.get_user_organization(user_id)
        has_organization = bool(organization and organization.get("id"))
        organization_id = organization.get("id") if organization else None

        return {
            "hasOrganization": has_organization,
            "organizationId": organization_id,
            "userId": user_id,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check user status: {exc}",
        ) from exc
