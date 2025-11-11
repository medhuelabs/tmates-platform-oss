import pytest
from fastapi import HTTPException, status

from app.api.schemas import AgentJobCreate
from app.auth.user_context import UserContext


def test_manage_agent_respects_plan_limit(monkeypatch):
    from app.api.routes import agents as agents_module

    class StubAgent:
        name = "Demo Agent"
        description = "Test agent"
        icon = "🤖"

    class StubAgentStore:
        def get_agent(self, key):
            return StubAgent()

    class StubDB:
        def get_user_organization(self, user_id):
            return {"id": "org-123", "name": "Org"}

        def get_organization_agents(self, organization_id):
            return [{"key": "existing"}]

        def get_agent_by_key(self, organization_id, agent_key):
            return None

    class StubBillingManager:
        def __init__(self, db):
            self._db = db

        @property
        def enabled(self):
            return True

        def get_plan_context(self, organization_id, *, active_agents=None):
            return object()

        def agent_limit_error(self, plan_context, *, active_agents):
            return "This plan allows up to 1 agents. Upgrade your subscription to add more teammates."

    stub_db = StubDB()
    monkeypatch.setattr(agents_module, "_agent_store", StubAgentStore())
    monkeypatch.setattr(agents_module, "get_database_client", lambda: stub_db)
    monkeypatch.setattr(agents_module, "BillingManager", StubBillingManager)

    with pytest.raises(HTTPException) as excinfo:
        agents_module.manage_organization_agent({"agent_key": "demo", "action": "add"}, user_id="user-1")

    assert excinfo.value.status_code == status.HTTP_402_PAYMENT_REQUIRED


def test_enqueue_agent_job_blocks_when_quota_exceeded(monkeypatch):
    from app.api.routes import jobs as jobs_module

    class StubDB:
        def list_agent_jobs(self, user_id, limit):
            return []

        def create_agent_job(self, user_id, agent_key, payload, metadata):
            return {"id": "job-1", "status": "queued"}

        def update_agent_job(self, *args, **kwargs):
            return None

    class StubBillingManager:
        def __init__(self, db):
            self._db = db

        @property
        def enabled(self):
            return True

        def get_plan_context(self, organization_id, *, active_agents=None):
            return object()

        def job_quota_error(self, plan_context):
            return "This plan includes 100 automated actions per billing period. Upgrade your subscription or wait for the next cycle to reset usage."

    user_context = UserContext(
        user_id="user-1",
        display_name="Test User",
        email=None,
        enabled_agents=["demo"],
        agent_configs={},
        timezone="UTC",
    )

    def stub_resolve_user_context(user_id):
        return user_context, {"id": "org-123"}, ["demo"]

    monkeypatch.setattr(jobs_module, "resolve_user_context", stub_resolve_user_context)
    monkeypatch.setattr(jobs_module, "BillingManager", StubBillingManager)

    request = AgentJobCreate(agent_key="demo")

    with pytest.raises(HTTPException) as excinfo:
        jobs_module.enqueue_agent_job(request, user_id="user-1", db=StubDB())

    assert excinfo.value.status_code == status.HTTP_402_PAYMENT_REQUIRED
