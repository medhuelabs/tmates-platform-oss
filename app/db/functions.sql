-- Database Functions for Multi-User Automation Platform
-- These functions provide server-side logic for common operations

-- Trigger helper to keep agent job timestamps in sync
CREATE OR REPLACE FUNCTION agent_jobs_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = public;

-- Function to create a personal organization for a new user
CREATE OR REPLACE FUNCTION create_personal_organization(
    p_user_id UUID,
    p_user_email TEXT,
    p_display_name TEXT
)
RETURNS UUID AS $$
DECLARE
    org_id UUID;
BEGIN
    -- Create personal organization
    INSERT INTO organizations (name, type, settings)
    VALUES (p_display_name, 'personal', '{}')
    RETURNING id INTO org_id;
    
    -- Add user as owner of the organization
    INSERT INTO organization_members (organization_id, user_id, role)
    VALUES (org_id, p_user_id, 'owner');
    
    RETURN org_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Function to initialize default agents for an organization
CREATE OR REPLACE FUNCTION initialize_default_agents(
    p_organization_id UUID,
    p_created_by UUID
)
RETURNS VOID AS $$
BEGIN
    -- Create default agents for the organization (Adam template only)
    INSERT INTO agents (organization_id, key, name, description, agent_type, role, color, icon, avatar, created_by) VALUES
    (p_organization_id, 'adam', 'Adam', 'Conversational teammate built on the Adam template', 'assistant', 'Core Teammate', '', '', '', p_created_by);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Function to setup new user with personal organization and default agents
CREATE OR REPLACE FUNCTION setup_new_user(
    p_user_id UUID,
    p_email TEXT,
    p_display_name TEXT
)
RETURNS JSONB AS $$
DECLARE
    org_id UUID;
    result JSONB;
BEGIN
    -- Create personal organization
    SELECT create_personal_organization(p_user_id, p_email, p_display_name) INTO org_id;
    
    -- Initialize default agents
    PERFORM initialize_default_agents(org_id, p_user_id);

    -- Ensure subscription placeholder exists
    INSERT INTO organization_subscriptions (organization_id, plan_key, status, billing_interval)
    VALUES (org_id, 'free', 'active', 'monthly')
    ON CONFLICT (organization_id) DO NOTHING;
    
    -- Return result
    result := jsonb_build_object(
        'organization_id', org_id,
        'agents_created', 1,
        'status', 'success'
    );
    
    RETURN result;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Function to get user's primary organization
CREATE OR REPLACE FUNCTION get_user_primary_organization(p_user_id UUID)
RETURNS TABLE(
    id UUID,
    name TEXT,
    type TEXT,
    settings JSONB,
    created_at TIMESTAMP WITH TIME ZONE
) AS $$
BEGIN
    RETURN QUERY
    SELECT o.id, o.name, o.type, o.settings, o.created_at
    FROM organizations o
    JOIN organization_members om ON o.id = om.organization_id
    WHERE om.user_id = p_user_id 
      AND om.is_active = true
      AND o.is_active = true
    ORDER BY 
        CASE WHEN o.type = 'personal' THEN 1 ELSE 2 END,
        om.joined_at ASC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Function to get organization agents with stats
CREATE OR REPLACE FUNCTION get_organization_agents_with_stats(p_organization_id UUID)
RETURNS TABLE(
    id UUID,
    key TEXT,
    name TEXT,
    description TEXT,
    agent_type TEXT,
    config JSONB,
    is_active BOOLEAN,
    created_at TIMESTAMP WITH TIME ZONE,
    task_count BIGINT,
    run_count BIGINT,
    last_run TIMESTAMP WITH TIME ZONE
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        a.id,
        a.key,
        a.name,
        a.description,
        a.agent_type,
        a.config,
        a.is_active,
        a.created_at,
        COALESCE(t.task_count, 0) as task_count,
        COALESCE(r.run_count, 0) as run_count,
        r.last_run
    FROM agents a
    LEFT JOIN (
        SELECT agent_id, COUNT(*) as task_count
        FROM tasks
        WHERE organization_id = p_organization_id
        GROUP BY agent_id
    ) t ON a.id = t.agent_id
    LEFT JOIN (
        SELECT agent_id, COUNT(*) as run_count, MAX(created_at) as last_run
        FROM runs
        WHERE organization_id = p_organization_id
        GROUP BY agent_id
    ) r ON a.id = r.agent_id
    WHERE a.organization_id = p_organization_id
      AND a.is_active = true
    ORDER BY a.name;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Function to create task with auto-generated ID
CREATE OR REPLACE FUNCTION create_organization_task(
    p_organization_id UUID,
    p_name TEXT,
    p_title TEXT DEFAULT NULL,
    p_description TEXT DEFAULT NULL,
    p_agent_id UUID DEFAULT NULL,
    p_details JSONB DEFAULT '{}',
    p_created_by UUID DEFAULT NULL
)
RETURNS TABLE(
    id TEXT,
    organization_id UUID,
    name TEXT,
    title TEXT,
    description TEXT,
    agent_id UUID,
    status TEXT,
    details JSONB,
    created_by UUID,
    created_at TIMESTAMP WITH TIME ZONE
) AS $$
DECLARE
    task_id TEXT;
    created_by_user UUID;
BEGIN
    -- Generate task ID
    task_id := 'task_' || to_char(NOW(), 'YYYYMMDDHH24MISS');
    
    -- Use provided created_by or current user
    created_by_user := COALESCE(p_created_by, auth.uid());
    
    -- Insert task
    INSERT INTO tasks (
        id, organization_id, name, title, description, 
        agent_id, details, created_by
    ) VALUES (
        task_id, p_organization_id, p_name, p_title, p_description,
        p_agent_id, p_details, created_by_user
    );
    
    -- Return the created task
    RETURN QUERY
    SELECT t.id, t.organization_id, t.name, t.title, t.description,
           t.agent_id, t.status, t.details, t.created_by, t.created_at
    FROM tasks t
    WHERE t.id = task_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Function to log run start with auto-generated ID
CREATE OR REPLACE FUNCTION log_organization_run_start(
    p_organization_id UUID,
    p_task_id TEXT DEFAULT NULL,
    p_agent_id UUID DEFAULT NULL,
    p_input JSONB DEFAULT NULL,
    p_created_by UUID DEFAULT NULL
)
RETURNS TEXT AS $$
DECLARE
    run_id TEXT;
    created_by_user UUID;
BEGIN
    -- Generate run ID
    run_id := 'run_' || to_char(NOW(), 'YYYYMMDDHH24MISS');
    
    -- Use provided created_by or current user
    created_by_user := COALESCE(p_created_by, auth.uid());
    
    -- Insert run record
    INSERT INTO runs (
        id, organization_id, task_id, agent_id, 
        status, input, created_by
    ) VALUES (
        run_id, p_organization_id, p_task_id, p_agent_id,
        'running', p_input, created_by_user
    );
    
    RETURN run_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Function to complete a run
CREATE OR REPLACE FUNCTION complete_organization_run(
    p_run_id TEXT,
    p_status TEXT DEFAULT 'completed',
    p_output JSONB DEFAULT NULL,
    p_error_message TEXT DEFAULT NULL,
    p_tokens_used INTEGER DEFAULT 0,
    p_duration_ms INTEGER DEFAULT NULL,
    p_cost_usd DECIMAL(10,6) DEFAULT NULL
)
RETURNS BOOLEAN AS $$
BEGIN
    UPDATE runs
    SET 
        status = p_status,
        output = p_output,
        error_message = p_error_message,
        tokens_used = p_tokens_used,
        duration_ms = p_duration_ms,
        cost_usd = p_cost_usd,
        completed_at = NOW()
    WHERE id = p_run_id;
    
    RETURN FOUND;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Function to get organization usage stats
CREATE OR REPLACE FUNCTION get_organization_usage_stats(
    p_organization_id UUID,
    p_start_date TIMESTAMP WITH TIME ZONE DEFAULT (NOW() - INTERVAL '30 days'),
    p_end_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()
)
RETURNS TABLE(
    total_tasks BIGINT,
    total_runs BIGINT,
    successful_runs BIGINT,
    failed_runs BIGINT,
    total_tokens BIGINT,
    total_cost DECIMAL(10,6),
    avg_duration_ms DECIMAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        (SELECT COUNT(*) FROM tasks WHERE organization_id = p_organization_id 
         AND created_at BETWEEN p_start_date AND p_end_date) as total_tasks,
        (SELECT COUNT(*) FROM runs WHERE organization_id = p_organization_id 
         AND created_at BETWEEN p_start_date AND p_end_date) as total_runs,
        (SELECT COUNT(*) FROM runs WHERE organization_id = p_organization_id 
         AND status = 'completed' AND created_at BETWEEN p_start_date AND p_end_date) as successful_runs,
        (SELECT COUNT(*) FROM runs WHERE organization_id = p_organization_id 
         AND status = 'failed' AND created_at BETWEEN p_start_date AND p_end_date) as failed_runs,
        (SELECT COALESCE(SUM(tokens_used), 0) FROM runs WHERE organization_id = p_organization_id 
         AND created_at BETWEEN p_start_date AND p_end_date) as total_tokens,
        (SELECT COALESCE(SUM(cost_usd), 0) FROM runs WHERE organization_id = p_organization_id 
         AND created_at BETWEEN p_start_date AND p_end_date) as total_cost,
        (SELECT COALESCE(AVG(duration_ms), 0) FROM runs WHERE organization_id = p_organization_id 
         AND duration_ms IS NOT NULL AND created_at BETWEEN p_start_date AND p_end_date) as avg_duration_ms;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;
