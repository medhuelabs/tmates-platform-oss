# Database Module

This folder contains all database-related functionality for the multi-user automation system.

## Structure

- `client.py` - Main DatabaseClient class and Supabase operations
- `settings.py` - JSON-based settings management and persistence
- `models/` - Database models and schema definitions
- SQL deploy scripts live alongside the module (`schema.sql`, `functions.sql`, `rls_policies.sql`)

## Usage Examples

```python
from app.db import get_database_client, DatabaseClient

# Single function import
from app.db.settings import (
    load_user_system_settings,
    load_user_agent_settings
)

# Model imports
from app.db.models import UserProfile, AgentSettings, RunRecord
```

## Migration Notes

During the consolidation, import paths changed:

- `from common.database import` → `from app.db import`
- `from common.json_settings import` → `from app.db.settings import`

## Environment Variables

Required environment variables for database operations:

- `SUPABASE_URL` - Your Supabase project URL
- `SUPABASE_ANON_KEY` - Supabase anonymous key
- `SUPABASE_SERVICE_ROLE_KEY` - Supabase service role key (optional, for privileged operations)
- `SUPABASE_JWT_SECRET` - JWT secret for token validation (optional)
