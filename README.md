# Tmates Platform API

<!-- Badges -->

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)]()
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg)]()
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)]()
[![CI](https://img.shields.io/badge/CI-GitHub_Actions-181717.svg)]()
[![License](https://img.shields.io/badge/License-MIT-informational.svg)]()

## 1. Overview

Tmates is a platform that lets you create and collaborate with your own team of AI teammates—each with a role, personality, and memory. Talk to them like coworkers, assign goals in plain language, and they will coordinate, work in the background, and report results. The backend is a FastAPI-based service designed for async workloads, Supabase-backed auth, and scalable orchestration of AI-powered agents.

## 2. Key Features

- 🔌 Integrations for CLI, mobile, and web clients.
- 🧑‍🤝‍🧑 Curated catalog of teammates (agents) with hire/dismiss flows.
- 🛠️ Self-service teammate creation and configuration.
- 💬 Real-time chat with individual or team threads.
- 🪄 Background task execution powered by Celery workers.
- 📌 Shared pinboard for persistent notes and summaries.
- 📁 File ingestion and secure storage abstractions.
- ⚙️ User and organization settings synced via Supabase.

## 3. Architecture Summary

- FastAPI application with modular routers under `app/api`.
- Agent execution via a shared `TmatesAgentsSDK` runtime that manages prompts, memory, and model access.
- Supabase (Postgres) for authentication, organization data, chat history, and agent catalogs.
- Redis-backed Celery workers for long-running or asynchronous agent jobs.
- Pluggable file storage (local disk, Supabase Storage, or S3-compatible buckets).
- Environment-driven runtime (`app/config.py`) for feature flags, billing, and storage selection.
- Reverse-proxy friendly—designed to sit behind Nginx/Traefik with HTTPS termination.

```
┌───────────────────────────────┐
│  Clients (Mobile / Web / CLI) │
└───────────────┬───────────────┘
                │ HTTP / WebSocket
┌───────────────▼───────────────┐
│        FastAPI app            │
│            app/api            │
└───────────────┬───────────────┘
                │ quick responses
         ┌──────▼───────┐
         │ Redis Queue  │
         └──────┬───────┘
                │ enqueue jobs
   ┌────────────▼────────────┐   ┌───────────────────────────────┐
   │      Agent Runner       │───│      Celery Workers           │
   │   app/core/agent_runner │   │          app/worker           │
   └────────────┬────────────┘   └───────────────────────────────┘
                │ discovers
   ┌────────────▼────────────┐
   │      Agent Registry     │
   │        app/registry     │
   └────────────┬────────────┘
                │ bootstraps
   ┌────────────▼────────────┐
   │     TmatesAgentsSDK     │
   │        app/sdk/...      │
   └────────────┬────────────┘
                │ LLM calls & tools
   ┌────────────▼────────────────────────────┐
   │ OpenAI / Azure, Pinboard, integrations  │
   └────────────┬────────────────────────────┘
                │ persist / stream
   ┌────────────▼────────────────────────────┐
   │ Supabase (auth, orgs, chats, catalog)   │
   │ Postgres memory, object storage, Stripe │
   │ Redis (results)                         │
   └─────────────────────────────────────────┘

```

## 4. Tech Stack

- **Language:** Python 3.11+
- **Framework:** FastAPI, Pydantic
- **Server:** Uvicorn (ASGI)
- **Agents Runtime:** `openai-agents` + custom `TmatesAgentsSDK`
- **Databases:** Supabase (Postgres), SQLAlchemy session memory
- **Messaging / Background Jobs:** Celery with Redis broker/result backend
- **Authentication:** Supabase JWT integration
- **Storage:** Local filesystem, Supabase Storage, or S3-compatible object stores
- **Payments:** Stripe billing provider
- **Observability:** Docker logs, Logfire tracing, optional CloudWatch integration
- **Containerization:** Docker & Docker Compose
- **CI/CD:** GitHub Actions (template ready)
- **Tooling:** Poetry-free `requirements.txt`, scripts under `scripts/`

## 5. Project Structure

```bash
tmates-platform/
  app/
    agents/            # Agent implementations, prompts, manifests (see AGENTS.md)
    api/               # FastAPI app, routers, dependencies, schemas
    auth/              # Supabase auth wiring and user context helpers
    core/              # Agent runner, dynamic agent service, thread manager
    registry/          # Agent discovery, catalog, bundle manager
    sdk/               # TmatesAgentsSDK runtime and configuration helpers
    services/          # Shared service integrations (OpenAI, storage, pinboard)
    worker/            # Celery app and task definitions
    db/                # Supabase client, SQL scripts, settings helpers
  scripts/             # Operational scripts (agent sync, SSL, maintenance)
  docker-compose.dev.yml
  docker-compose.staging.yml
  docker-compose.prod.yml
  requirements.txt
  run.py               # CLI entry point for agent execution
  AGENTS.md            # Agent implementation guidelines
```

### Highlights

- `app/api`: FastAPI routers grouped by domain (`agents`, `chats`, `files`, `billing`, `websocket`, etc.).
- `app/core`: Runtime coordination (user context, agent runner, mobile chat service).
- `app/registry`: Manages agent discovery, including Supabase-hosted bundles.
- `app/sdk`: Shared SDK that wires OpenAI/Azure clients, SQLAlchemy memory, and tools.
- `app/services`: External integrations reused across agents.
- `app/worker`: Celery worker configuration and tasks.
- `app/db`: Supabase database client, SQL definitions, and settings utilities.

## 6. Getting Started

### 6.1 Prerequisites

- Python 3.11+
- Docker & Docker Compose (recommended path)
- Redis (bundled via Docker Compose)
- Supabase project (URL + service role key)
- Configured file storage backend (Supabase Storage, S3-compatible bucket, or local stub)
- OpenAI or Azure OpenAI credentials

### 6.2 Environment Variables

Start from the template:

```bash
cp .env.example .env.dev
```

Essential variables:

| Variable                                                                                | Description                                                         |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `ENV`                                                                                   | Runtime environment flag (`dev`, `prod`)                            |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` | Supabase project/auth credentials                                   |
| `DATABASE_URL`                                                                          | Postgres connection string used for agent memory (async compatible) |
| `ENCRYPTION_KEY`, `SESSION_SECRET`                                                      | Secrets for token encryption and session signing                    |
| `OPENAI_API_KEY` / `AZURE_OPENAI_*`                                                     | LLM provider credentials                                            |
| `FILE_STORAGE_BACKEND`                                                                  | `local`, `supabase`, or `s3`                                        |
| `AGENT_CATALOG_ENABLED`                                                                 | Toggle Supabase catalog bundles vs. local agents                    |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`                                            | Override Redis connection if needed                                 |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`                                            | Enable billing and enforce plan limits                              |
| `ENABLE_LOGFIRE`, `LOGFIRE_TOKEN`                                                       | Optional tracing exports                                            |

Refer to `.env.example` for the full list and agent-specific notes.

> **Important:** Provision the Supabase project (database + storage) and object storage backend before starting the containers. The API will fail at startup if `SUPABASE_*` or storage credentials are missing.

Configure `FILE_STORAGE_BACKEND` to match your environment:

- `local` – stores files under `files/users/<id>` (best for quick prototypes).
- `supabase` – uses Supabase Storage buckets; ensure the bucket exists and credentials are scoped appropriately.
- `s3` – works with AWS S3 or any compatible service (set `S3_BUCKET_NAME`, `AWS_*`, optional `S3_ENDPOINT_URL`).

## 7. Run Locally

### Option A – Docker Compose (recommended)

```bash
docker compose -f docker-compose.dev.yml up --build
```

- API available at `http://localhost:8000`
- Interactive docs at `http://localhost:8000/docs` and `http://localhost:8000/redoc`
- Celery worker and Redis launch automatically; containers share the same `.env.dev`

### Option B – Local Development (manual)

```bash
pip install -r requirements.txt
export ENV=dev  # or use direnv/virtualenv
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
celery -A app.worker.celery_app.celery_app worker --loglevel=info
```

Use `python run.py agent <key> --message "Hello"` for quick CLI-based agent checks.

## 8. Database & Migrations

- Supabase/Postgres holds canonical data (organizations, profiles, chats, billing, agent catalog).
- SQL definition files live in `app/db/schema.sql`, `functions.sql`, and `rls_policies.sql`.
- Apply schema changes through the Supabase SQL editor or automation pipeline; Alembic is not used.
- The agent runtime requires `DATABASE_URL` pointing to an async-capable Postgres instance for session memory.

## 9. API Documentation

- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- REST routes are namespaced under `/v1/*`
- WebSocket endpoint exposed at `/v1/websocket`
- For SDK consumers or public API references, export the OpenAPI schema via `GET /openapi.json`

## 10. Authentication

- Authentication is handled through Supabase JWTs; the API expects an `Authorization: Bearer <token>` header.
- `app/api/dependencies.py` validates tokens and builds a `UserContext` from Supabase.
- The backend intentionally avoids bespoke auth logic so adopters can mirror the Supabase setup or swap in their own provider with minimal changes.

## 11. Testing & Code Quality

- Test suite is being refactored; existing coverage lives under `app/tests/` and agent-specific directories.
- Current commands:
  ```bash
  pytest
  flake8
  ```
- Expect changes as the test harness is modernized; update this section once the new tooling (e.g., `ruff`, `mypy`, `pre-commit`) is finalized.

## 12. Logging, Monitoring & Tracing

- Container logs are accessible via `docker logs <service>` during development.
- Production logging routes through your chosen sink (e.g., CloudWatch); configure Docker logging drivers or sidecars accordingly.
- Agent-level tracing integrates with both Logfire (`ENABLE_LOGFIRE=1`, `LOGFIRE_TOKEN`) and OpenAI tracing exports.
- Adjust verbosity with `.env` flags such as `LOG_LEVEL`, `VERBOSE`, and `SUPPRESS_SYSTEM_LOGS`.
- Celery workers propagate correlation IDs through task metadata for easier tracing.

## 13. Deployment

- Docker images are build-ready; use the provided compose files for staging (`docker-compose.staging.yml`) and production (`docker-compose.prod.yml`), which include Nginx for TLS termination.
- Typical stack:
  - Nginx reverse proxy (static config under `nginx.conf`)
  - Uvicorn workers for the FastAPI app
  - Celery worker processes
  - Redis (broker/result backend)
  - Supabase/Postgres (managed service)
  - Optional S3-compatible storage for large files
- See `scripts/setup-ssl.sh` and `scripts/renew-ssl.sh` for TLS automation examples.

## 14. Security

- Enforce HTTPS in production; terminate TLS at Nginx or your gateway.
- CORS origins configurable via `API_CORS_ORIGINS`.
- Secrets (OpenAI keys, Supabase service role, Stripe tokens) are environment-only—never commit them.
- Encrypt stored tokens using the Fernet `ENCRYPTION_KEY`.
- Review dependencies regularly (`pip-audit`, Dependabot, or GitHub Advanced Security recommended).
- Supabase RLS policies in `app/db/rls_policies.sql` provide fine-grained data access controls.

## 15. Contributing

We welcome contributions as the platform evolves.

1. Fork the repository and create a feature branch.
2. Reproduce the environment using Docker or local setup.
3. Implement changes with clear commits; update documentation where relevant.
4. Run the available tests (`pytest`, `flake8`) or note gaps if work is in progress.
5. Open a pull request with context, screenshots/logs if applicable, and checklists for manual verification.

No `CONTRIBUTING.md` yet—open an issue if you need guidance or want to help define the process.

## 16. License

MIT License  
Copyright (c) 2025 MedHue Labs

## 17. Contact & Support

- Website: https://tmates.app
- Email: hello@tmates.app
- Discord: https://discord.gg/tmates
- Issues: GitHub Issues in this repository

For enterprise inquiries or integration support, reach out via email.
