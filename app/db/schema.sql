-- Supabase Schema for Multi-User Automation Platform
-- This schema creates the organization-centric multi-tenant database structure
-- Compatible with our organization-aware DatabaseClient implementation

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Organizations table - central tenant isolation
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'personal',
    settings JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Organization members - user membership in organizations  
CREATE TABLE IF NOT EXISTS organization_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    is_active BOOLEAN DEFAULT true,
    joined_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(organization_id, user_id)
);

-- User profiles - extended user information
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    auth_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE,
    avatar_url TEXT,
    timezone TEXT DEFAULT 'UTC',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Teams within organizations
CREATE TABLE IF NOT EXISTS teams (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    settings JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_by UUID NOT NULL REFERENCES auth.users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(organization_id, name)
);

-- Team members
CREATE TABLE IF NOT EXISTS team_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    joined_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(team_id, user_id)
);

-- Agents - database-native agent definitions per organization
CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    key TEXT NOT NULL, -- e.g., 'adam'
    name TEXT NOT NULL,
    description TEXT,
    agent_type TEXT DEFAULT 'assistant',
    role TEXT DEFAULT '',
    color TEXT DEFAULT '',
    icon TEXT DEFAULT '',
    avatar TEXT DEFAULT '',
    config JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_by UUID NOT NULL REFERENCES auth.users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(organization_id, key)
);

-- Playbooks - reusable agent task templates
CREATE TABLE IF NOT EXISTS playbooks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    template JSONB NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_by UUID NOT NULL REFERENCES auth.users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(organization_id, name)
);

-- Tasks - work items for agents
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY DEFAULT concat('task_', to_char(NOW(), 'YYYYMMDDHH24MISS')),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    title TEXT,
    description TEXT,
    agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    playbook_id UUID REFERENCES playbooks(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER DEFAULT 3,
    is_recurring BOOLEAN DEFAULT false,
    schedule_cron TEXT,
    scheduled_for TIMESTAMP WITH TIME ZONE,
    details JSONB DEFAULT '{}',
    assigned_to UUID REFERENCES auth.users(id),
    created_by UUID NOT NULL REFERENCES auth.users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Runs - execution history for tasks/agents
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY DEFAULT concat('run_', to_char(NOW(), 'YYYYMMDDHH24MISS')),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    input JSONB,
    output JSONB,
    logs TEXT,
    error_message TEXT,
    tokens_used INTEGER DEFAULT 0,
    duration_ms INTEGER,
    cost_usd DECIMAL(10,6),
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    created_by UUID NOT NULL REFERENCES auth.users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- User settings - JSON blob per user and per-agent overrides
CREATE TABLE IF NOT EXISTS user_settings (
    user_id UUID PRIMARY KEY REFERENCES user_profiles(id) ON DELETE CASCADE,
    system_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    agent_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);


-- Usage tracking for billing
CREATE TABLE IF NOT EXISTS usage_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    quantity INTEGER DEFAULT 1,
    cost_usd DECIMAL(10,6),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Billing plan catalog
CREATE TABLE IF NOT EXISTS billing_plans (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL DEFAULT 'individual',
    is_active BOOLEAN NOT NULL DEFAULT true,
    sort_order INTEGER DEFAULT 0,
    stripe_product_id TEXT,
    stripe_price_monthly_id TEXT,
    stripe_price_yearly_id TEXT,
    metadata JSONB DEFAULT '{}',
    limits JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Organization subscription state
CREATE TABLE IF NOT EXISTS organization_subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    plan_key TEXT NOT NULL REFERENCES billing_plans(key),
    status TEXT NOT NULL DEFAULT 'active',
    billing_interval TEXT NOT NULL DEFAULT 'monthly',
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    stripe_price_id TEXT,
    cancel_at_period_end BOOLEAN DEFAULT false,
    current_period_start TIMESTAMP WITH TIME ZONE,
    current_period_end TIMESTAMP WITH TIME ZONE,
    trial_start TIMESTAMP WITH TIME ZONE,
    trial_end TIMESTAMP WITH TIME ZONE,
    quantity INTEGER,
    usage_snapshot JSONB DEFAULT '{}',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (organization_id)
);

-- Stripe webhook event audit (idempotency)
CREATE TABLE IF NOT EXISTS subscription_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    stripe_event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- User activity log for audit trail
CREATE TABLE IF NOT EXISTS user_activity (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    action_data JSONB DEFAULT '{}',
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Detailed per-user activity log (user_profiles scoped)
CREATE TABLE IF NOT EXISTS user_activity_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    action_data JSONB DEFAULT '{}',
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_activity_log_user_id
    ON user_activity_log (user_id);
CREATE INDEX IF NOT EXISTS idx_user_activity_log_created_at
    ON user_activity_log (created_at);

CREATE INDEX IF NOT EXISTS idx_organization_subscriptions_org_id ON organization_subscriptions(organization_id);
CREATE INDEX IF NOT EXISTS idx_organization_subscriptions_subscription_id ON organization_subscriptions(stripe_subscription_id);

-- Seed default billing plans
INSERT INTO billing_plans (
    key,
    name,
    description,
    category,
    is_active,
    sort_order,
    limits,
    metadata,
    stripe_product_id,
    stripe_price_monthly_id,
    stripe_price_yearly_id
)
VALUES
    ('free', 'Free', 'Launch with a single teammate and community support.', 'individual', true, 10,
        '{"max_agents":15,"monthly_actions":1000,"max_members":1}', '{"tier":"individual"}',
        NULL, NULL, NULL),
    ('basic', 'Basic', 'Core workflows for small teams.', 'individual', true, 20,
    '{"max_agents":20,"monthly_actions":5000,"max_members":1}', '{"tier":"individual"}',
        'prod_TMRYh4AXBdIN6S', 'price_1SPifg829drEDKd4X1TK3JY3', 'price_1SPiin829drEDKd49EReVgXx'),
    ('pro', 'Pro', 'Advanced automations and integrations.', 'individual', true, 30,
    '{"max_agents":50,"monthly_actions":20000,"max_members":1}', '{"tier":"individual"}',
        'prod_TMRaeLfamChc9R', 'price_1SPigr829drEDKd4eNLEXfZu', 'price_1SPij9829drEDKd4OqhQYy5r'),
    ('max', 'Max', 'Usage-based scaling with concierge onboarding.', 'individual', true, 40,
    '{"max_agents":null,"monthly_actions":50000,"max_members":1}', '{"tier":"individual","requires_contact":true}',
        'prod_TMRaXclzKIKGxB', 'price_1SPih8829drEDKd4pRWrCOYJ', 'price_1SPijU829drEDKd4j15Lsbuw'),
    ('team', 'Team', 'Cross-functional collaboration with governance.', 'company', true, 50,
        '{"max_agents":100,"monthly_actions":100000,"max_members":50}', '{"tier":"company"}',
        NULL, NULL, NULL),
    ('business', 'Business', 'Enterprise integrations and controls.', 'company', true, 60,
        '{"max_agents":200,"monthly_actions":250000,"max_members":150}', '{"tier":"company"}',
        NULL, NULL, NULL),
    ('enterprise', 'Enterprise', 'Custom deployment and strategic partnership.', 'company', true, 70,
        '{"max_agents":null,"monthly_actions":null,"max_members":null}', '{"tier":"company","requires_contact":true}',
        NULL, NULL, NULL)
ON CONFLICT (key) DO UPDATE
SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    category = EXCLUDED.category,
    is_active = EXCLUDED.is_active,
    sort_order = EXCLUDED.sort_order,
    limits = EXCLUDED.limits,
    metadata = EXCLUDED.metadata,
    stripe_product_id = EXCLUDED.stripe_product_id,
    stripe_price_monthly_id = EXCLUDED.stripe_price_monthly_id,
    stripe_price_yearly_id = EXCLUDED.stripe_price_yearly_id;

-- Run state tracking (legacy compatibility)
CREATE TABLE IF NOT EXISTS run_states (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    process_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'stopped',
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, process_type)
);

-- Per-user agent process state
CREATE TABLE IF NOT EXISTS user_run_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    process_type TEXT NOT NULL,
    process_id TEXT,
    status TEXT DEFAULT 'stopped',
    last_heartbeat TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, process_type)
);

CREATE INDEX IF NOT EXISTS idx_user_run_state_user_id
    ON user_run_state (user_id);
CREATE INDEX IF NOT EXISTS idx_user_run_state_status
    ON user_run_state (status);

-- Chat threads for persistent conversations
CREATE TABLE IF NOT EXISTS chat_threads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'agent',
    agent_keys TEXT[] DEFAULT '{}',
    active_session_id UUID,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_threads_org_user
    ON chat_threads (organization_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_threads_active_session
    ON chat_threads (active_session_id)
    WHERE active_session_id IS NOT NULL;

-- Chat messages belonging to threads
CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    thread_id UUID NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    session_id UUID,
    role TEXT NOT NULL,
    author TEXT,
    content TEXT NOT NULL,
    payload JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_id
    ON chat_messages (thread_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_messages_org_user
    ON chat_messages (organization_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id
    ON chat_messages (session_id, created_at);

-- Agent session metadata used for streaming responses
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Agent message transcript storage per session
CREATE TABLE IF NOT EXISTS agent_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
    message_data TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_session_time
    ON agent_messages (session_id, created_at);

-- Background job queue for long-running agent work
CREATE TABLE IF NOT EXISTS agent_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    auth_user_id UUID NOT NULL,
    agent_key TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB DEFAULT '{}',
    result JSONB,
    error JSONB,
    progress NUMERIC,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_agent_jobs_user_created
    ON agent_jobs (auth_user_id, created_at DESC);

-- Pinboard posts for agent-authored updates
CREATE TABLE IF NOT EXISTS pinboard_posts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    author_agent_key TEXT NOT NULL,
    title TEXT NOT NULL,
    slug TEXT NOT NULL,
    excerpt TEXT,
    content_md TEXT NOT NULL,
    cover_url TEXT,
    priority TEXT NOT NULL DEFAULT 'normal',
    attachments JSONB DEFAULT '[]',
    sources JSONB DEFAULT '[]',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (organization_id, slug)
);

-- Agent catalog tables for remote-managed agent publishing
CREATE TABLE IF NOT EXISTS agent_catalog_agents (
    key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description TEXT,
    icon_url TEXT,
    category TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc', NOW()),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc', NOW())
);

CREATE TABLE IF NOT EXISTS agent_catalog_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_key TEXT NOT NULL REFERENCES agent_catalog_agents(key) ON DELETE CASCADE,
    version TEXT NOT NULL,
    bundle_url TEXT NOT NULL,
    bundle_checksum TEXT,
    bundle_signature TEXT,
    signature_algorithm TEXT,
    manifest_snapshot JSONB,
    status TEXT NOT NULL DEFAULT 'draft',
    published_by TEXT,
    published_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc', NOW()),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc', NOW()),
    CONSTRAINT agent_catalog_versions_status_check CHECK (status IN ('draft', 'staged', 'published', 'retired')),
    CONSTRAINT agent_catalog_versions_unique UNIQUE (agent_key, version)
);

CREATE INDEX IF NOT EXISTS agent_catalog_versions_status_idx
    ON agent_catalog_versions (agent_key, status);

-- Enable RLS on agent catalog tables
ALTER TABLE agent_catalog_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_catalog_versions ENABLE ROW LEVEL SECURITY;

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_organization_members_org_id ON organization_members(organization_id);
CREATE INDEX IF NOT EXISTS idx_organization_members_user_id ON organization_members(user_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_auth_user_id ON user_profiles(auth_user_id);
CREATE INDEX IF NOT EXISTS idx_agents_organization_id ON agents(organization_id);
CREATE INDEX IF NOT EXISTS idx_agents_key ON agents(key);
CREATE INDEX IF NOT EXISTS idx_tasks_organization_id ON tasks(organization_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_agent_id ON tasks(agent_id);
CREATE INDEX IF NOT EXISTS idx_runs_organization_id ON runs(organization_id);
CREATE INDEX IF NOT EXISTS idx_runs_task_id ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_runs_agent_id ON runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at);
CREATE INDEX IF NOT EXISTS idx_user_settings_user_id ON user_settings(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_logs_organization_id ON usage_logs(organization_id);
CREATE INDEX IF NOT EXISTS idx_usage_logs_created_at ON usage_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_user_activity_user_id ON user_activity(user_id);
CREATE INDEX IF NOT EXISTS idx_user_activity_created_at ON user_activity(created_at);
CREATE INDEX IF NOT EXISTS idx_pinboard_posts_org_created ON pinboard_posts(organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pinboard_posts_user_created ON pinboard_posts(user_id, created_at DESC);

-- Update triggers for updated_at columns
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql' SET search_path = public;

-- Create triggers with proper error handling
DO $$
BEGIN
    -- Organizations trigger
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_organizations_updated_at') THEN
        CREATE TRIGGER update_organizations_updated_at 
        BEFORE UPDATE ON organizations 
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    
    -- User profiles trigger  
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_user_profiles_updated_at') THEN
        CREATE TRIGGER update_user_profiles_updated_at 
        BEFORE UPDATE ON user_profiles 
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    
    -- Teams trigger
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_teams_updated_at') THEN
        CREATE TRIGGER update_teams_updated_at 
        BEFORE UPDATE ON teams 
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    
    -- Agents trigger
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_agents_updated_at') THEN
        CREATE TRIGGER update_agents_updated_at 
        BEFORE UPDATE ON agents 
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    
    -- Playbooks trigger
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_playbooks_updated_at') THEN
        CREATE TRIGGER update_playbooks_updated_at 
        BEFORE UPDATE ON playbooks 
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    
    -- Tasks trigger
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tasks_updated_at') THEN
        CREATE TRIGGER update_tasks_updated_at 
        BEFORE UPDATE ON tasks 
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    
    -- User settings trigger
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_user_settings_updated_at') THEN
        CREATE TRIGGER update_user_settings_updated_at 
        BEFORE UPDATE ON user_settings 
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;

    -- Agent jobs trigger
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'agent_jobs_set_updated_at') THEN
        CREATE TRIGGER agent_jobs_set_updated_at
        BEFORE UPDATE ON agent_jobs
        FOR EACH ROW EXECUTE FUNCTION agent_jobs_set_updated_at();
    END IF;

    -- User run state trigger
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_user_run_state_updated_at') THEN
        CREATE TRIGGER update_user_run_state_updated_at
        BEFORE UPDATE ON user_run_state
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;

    -- Chat threads trigger
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_chat_threads_updated_at') THEN
        CREATE TRIGGER update_chat_threads_updated_at
        BEFORE UPDATE ON chat_threads
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;

    -- Pinboard posts trigger
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_pinboard_posts_updated_at') THEN
        CREATE TRIGGER update_pinboard_posts_updated_at
        BEFORE UPDATE ON pinboard_posts
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;

    -- Agent catalog triggers
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_agent_catalog_agents_updated_at') THEN
        CREATE TRIGGER update_agent_catalog_agents_updated_at
        BEFORE UPDATE ON agent_catalog_agents
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_agent_catalog_versions_updated_at') THEN
        CREATE TRIGGER update_agent_catalog_versions_updated_at
        BEFORE UPDATE ON agent_catalog_versions
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
