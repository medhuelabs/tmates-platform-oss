# Services Module

This folder contains shared external service integrations used across multiple agents and common utilities. Agent-specific service adapters remain in their respective agent folders.

## Structure

- `openai/` - OpenAI/Azure OpenAI client and response handling

## Philosophy

**Shared services live here** - External services used by multiple agents or common utilities.

**Agent-specific adapters stay in agents** – When you add new agents, keep
their specialized clients inside `app/agents/<key>/`. The Adam template does
not currently require extra adapters, but the structure accommodates them.

## Usage

```python
# Import shared services
from app.services import openai_client, call_response_with_metrics

# Import specific service modules
from app.services.openai import call_response_with_metrics
```

## Migration from common/

This module contains services previously located in `common/`:

### Changed imports:

- `from common.models import` → `from app.services.openai import`

### Removed files:

- `common/models.py` → `services/openai/client.py`
- `common/mail.py` → removed (Newman has his own email adapter)

## Environment Variables

### OpenAI Service

- `OPENAI_API_KEY` - OpenAI API key (for standard OpenAI)
- `OPENAI_CLIENT` - Client preference: "openai", "azure", or "auto"
- `AZURE_OPENAI_ENDPOINT` - Azure OpenAI endpoint URL
- `AZURE_OPENAI_API_KEY` - Azure OpenAI API key
- `AZURE_OPENAI_API_VERSION` - Azure OpenAI API version
- `AZURE_OPENAI_DEPLOYMENT` - Azure OpenAI deployment name
