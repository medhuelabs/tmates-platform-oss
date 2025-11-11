import pytest
from fastapi import HTTPException, status

from app.api.routes import billing as billing_module
from app.api.schemas import BillingOnboardingRequest


class StubDatabase:
    def __init__(self) -> None:
        self.setup_calls = []
        self.updated = []
        self.subscription = {
            "organization_id": "org-123",
            "plan_key": "free",
            "status": "active",
            "billing_interval": "monthly",
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
        }

    def setup_new_user(self, auth_user_id, email, organization_name):
        self.setup_calls.append((auth_user_id, email, organization_name))
        return {"status": "success"}

    def get_user_profile_by_auth_id(self, user_id):
        return {"auth_user_id": user_id, "avatar_url": None}

    def get_user_organization(self, user_id):
        return {
            "id": "org-123",
            "name": "Acme Org",
            "type": "personal",
            "settings": {},
            "owner_id": user_id,
        }

    def ensure_organization_subscription(self, organization_id, default_plan_key):
        assert organization_id == "org-123"
        return self.subscription

    def get_billing_plan(self, plan_key):
        if plan_key == "basic":
            return {"key": "basic", "is_active": True}
        return None

    def update_organization_subscription(self, organization_id, updates):
        assert organization_id == "org-123"
        self.subscription = {**self.subscription, **updates}
        self.updated.append(updates)
        return self.subscription

    def get_organization_subscription(self, organization_id):
        assert organization_id == "org-123"
        return self.subscription


def test_onboard_billing_updates_subscription():
    db = StubDatabase()
    request = BillingOnboardingRequest(
        email="user@example.com",
        display_name="Test User",
        organization_name="Acme Org",
        plan_key="basic",
        billing_interval="annual",
    )
    auth_user = {"id": "user-1", "email": "user@example.com", "metadata": {}}

    response = billing_module.onboard_billing_customer(request, auth_user=auth_user, db=db)

    assert db.setup_calls == [("user-1", "user@example.com", "Acme Org")]
    assert db.updated[-1]["plan_key"] == "basic"
    assert db.subscription["billing_interval"] == "yearly"
    assert response.subscription is not None
    assert response.subscription.plan_key == "basic"
    assert response.subscription.billing_interval == "yearly"
    assert response.organization is not None
    assert response.organization.id == "org-123"
    assert response.profile.display_name == "Test User"


def test_onboard_billing_rejects_unknown_plan():
    db = StubDatabase()
    request = BillingOnboardingRequest(
        email="user@example.com",
        plan_key="unknown",
    )
    auth_user = {"id": "user-1", "email": "user@example.com", "metadata": {}}

    with pytest.raises(HTTPException) as excinfo:
        billing_module.onboard_billing_customer(request, auth_user=auth_user, db=db)

    assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND


def test_onboard_billing_requires_email():
    db = StubDatabase()
    request = BillingOnboardingRequest()
    auth_user = {"id": "user-1", "email": None, "metadata": {}}

    with pytest.raises(HTTPException) as excinfo:
        billing_module.onboard_billing_customer(request, auth_user=auth_user, db=db)

    assert excinfo.value.status_code == status.HTTP_400_BAD_REQUEST
