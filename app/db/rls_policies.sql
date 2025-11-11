-- Row Level Security (RLS) Policies for Multi-Tenant Automation Platform
-- These policies ensure complete data isolation between organizations
-- Users can only access data from organizations they are members of

-- Enable RLS on all tables
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE playbooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_settings FORCE ROW LEVEL SECURITY;
ALTER TABLE usage_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_activity ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_activity_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_run_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE pinboard_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_events ENABLE ROW LEVEL SECURITY;

-- Drop existing policies so definitions below can re-create them with updated clauses
DROP POLICY IF EXISTS "Users can view their organizations" ON organizations;
DROP POLICY IF EXISTS "Users can create organizations" ON organizations;
DROP POLICY IF EXISTS "Users can update their organizations" ON organizations;
DROP POLICY IF EXISTS "Users can view their chat threads" ON chat_threads;
DROP POLICY IF EXISTS "Users can create chat threads" ON chat_threads;
DROP POLICY IF EXISTS "Users can update their chat threads" ON chat_threads;
DROP POLICY IF EXISTS "Users can delete their chat threads" ON chat_threads;
DROP POLICY IF EXISTS "Users can view their chat messages" ON chat_messages;
DROP POLICY IF EXISTS "Users can send chat messages" ON chat_messages;
DROP POLICY IF EXISTS "Agent sessions service insert" ON agent_sessions;
DROP POLICY IF EXISTS "Agent sessions service update" ON agent_sessions;
DROP POLICY IF EXISTS "Agent sessions user select" ON agent_sessions;
DROP POLICY IF EXISTS "Agent messages service insert" ON agent_messages;
DROP POLICY IF EXISTS "Agent messages service update" ON agent_messages;
DROP POLICY IF EXISTS "Agent messages user select" ON agent_messages;
DROP POLICY IF EXISTS "Users can view pinboard posts" ON pinboard_posts;
DROP POLICY IF EXISTS "Users can create pinboard posts" ON pinboard_posts;
DROP POLICY IF EXISTS "Users can update pinboard posts" ON pinboard_posts;
DROP POLICY IF EXISTS "Users can delete pinboard posts" ON pinboard_posts;
DROP POLICY IF EXISTS "Agent jobs modify" ON agent_jobs;
DROP POLICY IF EXISTS "Agent jobs select" ON agent_jobs;
DROP POLICY IF EXISTS "Service role manages agent jobs" ON agent_jobs;
DROP POLICY IF EXISTS "Service role manages agent catalog agents" ON agent_catalog_agents;
DROP POLICY IF EXISTS "Service role manages agent catalog versions" ON agent_catalog_versions;
DROP POLICY IF EXISTS "Authenticated users can view agent catalog agents" ON agent_catalog_agents;
DROP POLICY IF EXISTS "Authenticated users can view published agent catalog versions" ON agent_catalog_versions;
DROP POLICY IF EXISTS "Users can view organization members" ON organization_members;
DROP POLICY IF EXISTS "Users can join organizations" ON organization_members;
DROP POLICY IF EXISTS "Users can update their memberships" ON organization_members;
DROP POLICY IF EXISTS "Users can view their own profile" ON user_profiles;
DROP POLICY IF EXISTS "Users can insert their own profile" ON user_profiles;
DROP POLICY IF EXISTS "Users can update their own profile" ON user_profiles;
DROP POLICY IF EXISTS "Users can view organization teams" ON teams;
DROP POLICY IF EXISTS "Users can create teams in their organizations" ON teams;
DROP POLICY IF EXISTS "Users can update teams in their organizations" ON teams;
DROP POLICY IF EXISTS "Users can view team members" ON team_members;
DROP POLICY IF EXISTS "Users can join teams" ON team_members;
DROP POLICY IF EXISTS "Users can view organization agents" ON agents;
DROP POLICY IF EXISTS "Users can create agents in their organizations" ON agents;
DROP POLICY IF EXISTS "Users can update agents in their organizations" ON agents;
DROP POLICY IF EXISTS "Users can view organization playbooks" ON playbooks;
DROP POLICY IF EXISTS "Users can create playbooks in their organizations" ON playbooks;
DROP POLICY IF EXISTS "Users can update playbooks in their organizations" ON playbooks;
DROP POLICY IF EXISTS "Users can view organization tasks" ON tasks;
DROP POLICY IF EXISTS "Users can create tasks in their organizations" ON tasks;
DROP POLICY IF EXISTS "Users can update tasks in their organizations" ON tasks;
DROP POLICY IF EXISTS "Users can delete tasks in their organizations" ON tasks;
DROP POLICY IF EXISTS "Users can view organization runs" ON runs;
DROP POLICY IF EXISTS "Users can create runs in their organizations" ON runs;
DROP POLICY IF EXISTS "Users can update runs in their organizations" ON runs;
DROP POLICY IF EXISTS "Users can view their own settings" ON user_settings;
DROP POLICY IF EXISTS "Users can insert their own settings" ON user_settings;
DROP POLICY IF EXISTS "Users can update their own settings" ON user_settings;
DROP POLICY IF EXISTS "Users can delete their own settings" ON user_settings;
DROP POLICY IF EXISTS "Users can view organization usage logs" ON usage_logs;
DROP POLICY IF EXISTS "System can insert usage logs" ON usage_logs;
DROP POLICY IF EXISTS "Users can view their own activity" ON user_activity;
DROP POLICY IF EXISTS "System can insert user activity" ON user_activity;
DROP POLICY IF EXISTS "System can log user activity" ON user_activity_log;
DROP POLICY IF EXISTS "Users can view their activity log" ON user_activity_log;
DROP POLICY IF EXISTS "Users can view their run states" ON run_states;
DROP POLICY IF EXISTS "Users can manage their run states" ON run_states;
DROP POLICY IF EXISTS "Service role manages run states" ON run_states;
DROP POLICY IF EXISTS "Users can manage their run state" ON user_run_state;
DROP POLICY IF EXISTS "Users can view their run state" ON user_run_state;
DROP POLICY IF EXISTS "Service role manages user run state" ON user_run_state;
DROP POLICY IF EXISTS "Users can view billing plans" ON billing_plans;
DROP POLICY IF EXISTS "Users can view organization subscriptions" ON organization_subscriptions;
DROP POLICY IF EXISTS "Service role manages subscription events" ON subscription_events;

-- Grant blanket full access to the service_role for writable tables
DO $$
DECLARE
    tbl text;
    tables constant text[] := ARRAY[
        'organizations',
        'organization_members',
        'user_profiles',
        'teams',
        'team_members',
        'agents',
        'playbooks',
        'tasks',
        'runs',
        'user_settings',
        'usage_logs',
        'user_activity',
        'chat_threads',
        'chat_messages',
        'pinboard_posts',
        'billing_plans',
        'organization_subscriptions',
        'subscription_events'
    ];
BEGIN
    FOREACH tbl IN ARRAY tables LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE tablename = tbl
              AND policyname = 'Service role full access'
        ) THEN
            EXECUTE format(
                'CREATE POLICY "Service role full access" ON %I FOR ALL TO service_role USING (true) WITH CHECK (true);',
                tbl
            );
        END IF;
    END LOOP;
END $$;

-- Helper function to get user's organizations
CREATE OR REPLACE FUNCTION get_user_organizations(user_id UUID)
RETURNS UUID[] AS $$
BEGIN
    RETURN ARRAY(
        SELECT organization_id 
        FROM organization_members 
        WHERE user_id = $1 AND is_active = true
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Helper function to map auth user IDs to internal profile IDs
CREATE OR REPLACE FUNCTION get_user_profile_id(auth_user UUID)
RETURNS UUID AS $$
DECLARE
    internal_id UUID;
BEGIN
    SELECT id
    INTO internal_id
    FROM user_profiles
    WHERE auth_user_id = auth_user
    LIMIT 1;

    RETURN internal_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Organizations: Users can only see organizations they belong to
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'organizations' AND policyname = 'Users can view their organizations') THEN
        CREATE POLICY "Users can view their organizations" ON organizations
            FOR SELECT TO authenticated USING (
                id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'organizations' AND policyname = 'Users can create organizations') THEN
        CREATE POLICY "Users can create organizations" ON organizations
            FOR INSERT TO authenticated WITH CHECK (true);
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'organizations' AND policyname = 'Users can update their organizations') THEN
        CREATE POLICY "Users can update their organizations" ON organizations
            FOR UPDATE TO authenticated USING (
                id = ANY(get_user_organizations((SELECT auth.uid())))
            ) WITH CHECK (
                id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
END $$;

-- Chat threads access control
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'chat_threads' AND policyname = 'Users can view their chat threads') THEN
        CREATE POLICY "Users can view their chat threads" ON chat_threads
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                OR user_id = (SELECT auth.uid())
            );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'chat_threads' AND policyname = 'Users can create chat threads') THEN
        CREATE POLICY "Users can create chat threads" ON chat_threads
            FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'chat_threads' AND policyname = 'Users can update their chat threads') THEN
        CREATE POLICY "Users can update their chat threads" ON chat_threads
            FOR UPDATE TO authenticated USING (user_id = (SELECT auth.uid()))
            WITH CHECK (user_id = (SELECT auth.uid()));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'chat_threads' AND policyname = 'Users can delete their chat threads') THEN
        CREATE POLICY "Users can delete their chat threads" ON chat_threads
            FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));
    END IF;
END $$;

-- Chat messages access control
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'chat_messages' AND policyname = 'Users can view their chat messages') THEN
        CREATE POLICY "Users can view their chat messages" ON chat_messages
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                OR user_id = (SELECT auth.uid())
            );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'chat_messages' AND policyname = 'Users can send chat messages') THEN
        CREATE POLICY "Users can send chat messages" ON chat_messages
            FOR INSERT TO authenticated WITH CHECK (
                user_id = (SELECT auth.uid())
                AND EXISTS (
                    SELECT 1
                    FROM chat_threads t
                    WHERE t.id = thread_id
                      AND (
                          t.organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                          OR t.user_id = (SELECT auth.uid())
                      )
                )
            );
    END IF;
END $$;

-- Agent session/message access control
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_sessions' AND policyname = 'Agent sessions service insert') THEN
        CREATE POLICY "Agent sessions service insert" ON agent_sessions
            FOR INSERT TO service_role WITH CHECK (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_sessions' AND policyname = 'Agent sessions service update') THEN
        CREATE POLICY "Agent sessions service update" ON agent_sessions
            FOR UPDATE TO service_role USING (true)
            WITH CHECK (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_sessions' AND policyname = 'Agent sessions user select') THEN
        CREATE POLICY "Agent sessions user select" ON agent_sessions
            FOR SELECT TO authenticated USING (
                EXISTS (
                    SELECT 1
                    FROM chat_messages cm
                    JOIN chat_threads ct ON ct.id = cm.thread_id
                    WHERE cm.session_id::text = agent_sessions.session_id::text
                      AND (
                          ct.user_id = (SELECT auth.uid())
                          OR ct.organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                      )
                )
            );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_messages' AND policyname = 'Agent messages service insert') THEN
        CREATE POLICY "Agent messages service insert" ON agent_messages
            FOR INSERT TO service_role WITH CHECK (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_messages' AND policyname = 'Agent messages service update') THEN
        CREATE POLICY "Agent messages service update" ON agent_messages
            FOR UPDATE TO service_role USING (true)
            WITH CHECK (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_messages' AND policyname = 'Agent messages user select') THEN
        CREATE POLICY "Agent messages user select" ON agent_messages
            FOR SELECT TO authenticated USING (
                EXISTS (
                    SELECT 1
                    FROM chat_messages cm
                    JOIN chat_threads ct ON ct.id = cm.thread_id
                    WHERE cm.session_id::text = agent_messages.session_id::text
                      AND (
                          ct.user_id = (SELECT auth.uid())
                          OR ct.organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                      )
                )
            );
    END IF;
END $$;

-- Pinboard posts access control
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'pinboard_posts' AND policyname = 'Users can view pinboard posts') THEN
        CREATE POLICY "Users can view pinboard posts" ON pinboard_posts
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                OR user_id = (SELECT auth.uid())
            );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'pinboard_posts' AND policyname = 'Users can create pinboard posts') THEN
        CREATE POLICY "Users can create pinboard posts" ON pinboard_posts
            FOR INSERT TO authenticated WITH CHECK (
                user_id = (SELECT auth.uid())
                OR organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'pinboard_posts' AND policyname = 'Users can update pinboard posts') THEN
        CREATE POLICY "Users can update pinboard posts" ON pinboard_posts
            FOR UPDATE TO authenticated USING (
                user_id = (SELECT auth.uid())
                OR organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            ) WITH CHECK (
                user_id = (SELECT auth.uid())
                OR organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'pinboard_posts' AND policyname = 'Users can delete pinboard posts') THEN
        CREATE POLICY "Users can delete pinboard posts" ON pinboard_posts
            FOR DELETE TO authenticated USING (
                user_id = (SELECT auth.uid())
                OR organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
END $$;

-- Agent job queue access control
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_jobs' AND policyname = 'Agent jobs modify') THEN
        CREATE POLICY "Agent jobs modify" ON agent_jobs
            TO authenticated
            USING ((SELECT auth.uid()) = auth_user_id)
            WITH CHECK ((SELECT auth.uid()) = auth_user_id);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_jobs' AND policyname = 'Agent jobs select') THEN
        CREATE POLICY "Agent jobs select" ON agent_jobs
            FOR SELECT TO authenticated USING ((SELECT auth.uid()) = auth_user_id);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_jobs' AND policyname = 'Service role manages agent jobs') THEN
        CREATE POLICY "Service role manages agent jobs" ON agent_jobs
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- Agent catalog access control
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_catalog_agents' AND policyname = 'Service role manages agent catalog agents') THEN
        CREATE POLICY "Service role manages agent catalog agents" ON agent_catalog_agents
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_catalog_versions' AND policyname = 'Service role manages agent catalog versions') THEN
        CREATE POLICY "Service role manages agent catalog versions" ON agent_catalog_versions
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_catalog_agents' AND policyname = 'Authenticated users can view agent catalog agents') THEN
        CREATE POLICY "Authenticated users can view agent catalog agents" ON agent_catalog_agents
            FOR SELECT TO authenticated USING (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agent_catalog_versions' AND policyname = 'Authenticated users can view published agent catalog versions') THEN
        CREATE POLICY "Authenticated users can view published agent catalog versions" ON agent_catalog_versions
            FOR SELECT TO authenticated USING (status = 'published');
    END IF;
END $$;

-- Organization members: Users can see memberships for their organizations
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'organization_members' AND policyname = 'Users can view organization members') THEN
        CREATE POLICY "Users can view organization members" ON organization_members
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                OR user_id = (SELECT auth.uid())
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'organization_members' AND policyname = 'Users can join organizations') THEN
        CREATE POLICY "Users can join organizations" ON organization_members
            FOR INSERT TO authenticated WITH CHECK (
                user_id = (SELECT auth.uid())
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'organization_members' AND policyname = 'Users can update their memberships') THEN
        CREATE POLICY "Users can update their memberships" ON organization_members
            FOR UPDATE TO authenticated USING (
                user_id = (SELECT auth.uid())
                OR organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            ) WITH CHECK (
                user_id = (SELECT auth.uid())
                OR organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
END $$;

-- User profiles: Users can only access their own profile
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_profiles' AND policyname = 'Users can view their own profile') THEN
        CREATE POLICY "Users can view their own profile" ON user_profiles
            FOR SELECT TO authenticated USING (auth_user_id = (SELECT auth.uid()));
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_profiles' AND policyname = 'Users can insert their own profile') THEN
        CREATE POLICY "Users can insert their own profile" ON user_profiles
            FOR INSERT TO authenticated WITH CHECK (auth_user_id = (SELECT auth.uid()));
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_profiles' AND policyname = 'Users can update their own profile') THEN
        CREATE POLICY "Users can update their own profile" ON user_profiles
            FOR UPDATE TO authenticated USING (auth_user_id = (SELECT auth.uid()))
            WITH CHECK (auth_user_id = (SELECT auth.uid()));
    END IF;
END $$;

-- Teams: Organization-scoped access
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'teams' AND policyname = 'Users can view organization teams') THEN
        CREATE POLICY "Users can view organization teams" ON teams
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'teams' AND policyname = 'Users can create teams in their organizations') THEN
        CREATE POLICY "Users can create teams in their organizations" ON teams
            FOR INSERT TO authenticated WITH CHECK (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                AND created_by = (SELECT auth.uid())
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'teams' AND policyname = 'Users can update teams in their organizations') THEN
        CREATE POLICY "Users can update teams in their organizations" ON teams
            FOR UPDATE TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            ) WITH CHECK (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
END $$;

-- Team members: Team and organization scoped
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'team_members' AND policyname = 'Users can view team members') THEN
        CREATE POLICY "Users can view team members" ON team_members
            FOR SELECT TO authenticated USING (
                team_id IN (
                    SELECT id FROM teams 
                    WHERE organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                )
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'team_members' AND policyname = 'Users can join teams') THEN
        CREATE POLICY "Users can join teams" ON team_members
            FOR INSERT TO authenticated WITH CHECK (
                user_id = (SELECT auth.uid())
                AND team_id IN (
                    SELECT id FROM teams 
                    WHERE organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                )
            );
    END IF;
END $$;

-- Agents: Organization-scoped access
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agents' AND policyname = 'Users can view organization agents') THEN
        CREATE POLICY "Users can view organization agents" ON agents
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agents' AND policyname = 'Users can create agents in their organizations') THEN
        CREATE POLICY "Users can create agents in their organizations" ON agents
            FOR INSERT TO authenticated WITH CHECK (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                AND created_by = (SELECT auth.uid())
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agents' AND policyname = 'Users can update agents in their organizations') THEN
        CREATE POLICY "Users can update agents in their organizations" ON agents
            FOR UPDATE TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            ) WITH CHECK (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
END $$;

-- Playbooks: Organization-scoped access
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'playbooks' AND policyname = 'Users can view organization playbooks') THEN
        CREATE POLICY "Users can view organization playbooks" ON playbooks
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'playbooks' AND policyname = 'Users can create playbooks in their organizations') THEN
        CREATE POLICY "Users can create playbooks in their organizations" ON playbooks
            FOR INSERT TO authenticated WITH CHECK (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                AND created_by = (SELECT auth.uid())
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'playbooks' AND policyname = 'Users can update playbooks in their organizations') THEN
        CREATE POLICY "Users can update playbooks in their organizations" ON playbooks
            FOR UPDATE TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            ) WITH CHECK (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
END $$;

-- Tasks: Organization-scoped access
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'tasks' AND policyname = 'Users can view organization tasks') THEN
        CREATE POLICY "Users can view organization tasks" ON tasks
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'tasks' AND policyname = 'Users can create tasks in their organizations') THEN
        CREATE POLICY "Users can create tasks in their organizations" ON tasks
            FOR INSERT TO authenticated WITH CHECK (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                AND created_by = (SELECT auth.uid())
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'tasks' AND policyname = 'Users can update tasks in their organizations') THEN
        CREATE POLICY "Users can update tasks in their organizations" ON tasks
            FOR UPDATE TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            ) WITH CHECK (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'tasks' AND policyname = 'Users can delete tasks in their organizations') THEN
        CREATE POLICY "Users can delete tasks in their organizations" ON tasks
            FOR DELETE TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                AND created_by = (SELECT auth.uid())
            );
    END IF;
END $$;

-- Runs: Organization-scoped access
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'runs' AND policyname = 'Users can view organization runs') THEN
        CREATE POLICY "Users can view organization runs" ON runs
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'runs' AND policyname = 'Users can create runs in their organizations') THEN
        CREATE POLICY "Users can create runs in their organizations" ON runs
            FOR INSERT TO authenticated WITH CHECK (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
                AND created_by = (SELECT auth.uid())
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'runs' AND policyname = 'Users can update runs in their organizations') THEN
        CREATE POLICY "Users can update runs in their organizations" ON runs
            FOR UPDATE TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            ) WITH CHECK (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
END $$;

-- User settings: User-scoped access
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_settings' AND policyname = 'Users can view their own settings') THEN
        CREATE POLICY "Users can view their own settings" ON user_settings
            FOR SELECT TO authenticated USING (
                user_id = get_user_profile_id((SELECT auth.uid()))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_settings' AND policyname = 'Users can insert their own settings') THEN
        CREATE POLICY "Users can insert their own settings" ON user_settings
            FOR INSERT TO authenticated WITH CHECK (
                user_id = get_user_profile_id((SELECT auth.uid()))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_settings' AND policyname = 'Users can update their own settings') THEN
        CREATE POLICY "Users can update their own settings" ON user_settings
            FOR UPDATE TO authenticated USING (
                user_id = get_user_profile_id((SELECT auth.uid()))
            )
            WITH CHECK (
                user_id = get_user_profile_id((SELECT auth.uid()))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_settings' AND policyname = 'Users can delete their own settings') THEN
        CREATE POLICY "Users can delete their own settings" ON user_settings
            FOR DELETE TO authenticated USING (
                user_id = get_user_profile_id((SELECT auth.uid()))
            );
    END IF;
END $$;

-- Usage logs: Organization-scoped access (read-only for users)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'usage_logs' AND policyname = 'Users can view organization usage logs') THEN
        CREATE POLICY "Users can view organization usage logs" ON usage_logs
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'usage_logs' AND policyname = 'System can insert usage logs') THEN
        CREATE POLICY "System can insert usage logs" ON usage_logs
            FOR INSERT TO service_role WITH CHECK (true);
    END IF;
END $$;

-- Billing plans: public catalog for authenticated users
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'billing_plans' AND policyname = 'Users can view billing plans') THEN
        CREATE POLICY "Users can view billing plans" ON billing_plans
            FOR SELECT TO authenticated USING (is_active = true);
    END IF;
END $$;

-- Organization subscriptions: members can inspect their current plan
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'organization_subscriptions' AND policyname = 'Users can view organization subscriptions') THEN
        CREATE POLICY "Users can view organization subscriptions" ON organization_subscriptions
            FOR SELECT TO authenticated USING (
                organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
END $$;

-- User activity: User and organization scoped
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_activity' AND policyname = 'Users can view their own activity') THEN
        CREATE POLICY "Users can view their own activity" ON user_activity
            FOR SELECT TO authenticated USING (
                user_id = (SELECT auth.uid())
                OR organization_id = ANY(get_user_organizations((SELECT auth.uid())))
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_activity' AND policyname = 'System can insert user activity') THEN
        CREATE POLICY "System can insert user activity" ON user_activity
            FOR INSERT TO service_role WITH CHECK (true);
    END IF;
END $$;

-- User activity log access control
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_activity_log' AND policyname = 'System can log user activity') THEN
        CREATE POLICY "System can log user activity" ON user_activity_log
            FOR INSERT TO service_role WITH CHECK (true);
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_activity_log' AND policyname = 'Users can view their activity log') THEN
        CREATE POLICY "Users can view their activity log" ON user_activity_log
            FOR SELECT TO authenticated USING (
                user_id IN (
                    SELECT up.id
                    FROM user_profiles up
                    WHERE up.auth_user_id = (SELECT auth.uid())
                )
            );
    END IF;
END $$;

-- Legacy tables: User-scoped access
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'run_states' AND policyname = 'Users can view their run states') THEN
        CREATE POLICY "Users can view their run states" ON run_states
            FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'run_states' AND policyname = 'Users can manage their run states') THEN
        CREATE POLICY "Users can manage their run states" ON run_states
            FOR ALL TO authenticated USING (user_id = (SELECT auth.uid()))
            WITH CHECK (user_id = (SELECT auth.uid()));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'run_states' AND policyname = 'Service role manages run states') THEN
        CREATE POLICY "Service role manages run states" ON run_states
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- User run state access control
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_run_state' AND policyname = 'Users can manage their run state') THEN
        CREATE POLICY "Users can manage their run state" ON user_run_state
            FOR ALL TO authenticated USING (
                user_id IN (
                    SELECT up.id
                    FROM user_profiles up
                    WHERE up.auth_user_id = (SELECT auth.uid())
                )
            )
            WITH CHECK (
                user_id IN (
                    SELECT up.id
                    FROM user_profiles up
                    WHERE up.auth_user_id = (SELECT auth.uid())
                )
            );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_run_state' AND policyname = 'Users can view their run state') THEN
        CREATE POLICY "Users can view their run state" ON user_run_state
            FOR SELECT TO authenticated USING (
                user_id IN (
                    SELECT up.id
                    FROM user_profiles up
                    WHERE up.auth_user_id = (SELECT auth.uid())
                )
            );
    END IF;
END $$;

-- Service role bypass for user_run_state
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_run_state' AND policyname = 'Service role manages user run state') THEN
        CREATE POLICY "Service role manages user run state" ON user_run_state
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- Grant necessary permissions to authenticated users
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO authenticated;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO authenticated;
