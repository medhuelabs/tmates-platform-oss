# Agent Implementation Guidelines

This repository currently ships a single production-ready agent (`adam`) that
follows the modern October 2025 template. Use this document as a quick reference
when auditing or building additional agents.

## Repository Structure (Current)

- `app/agents/adam/` – canonical example agent with `agent.py`, `brain.py`,
  `interface/`, `prompts/`, and `manifest.yaml`.
- `app/registry/` – agent discovery and hiring logic (manifest driven).
- `app/core/agent_runner.py` – shared dispatcher that resolves user context and
  invokes `AgentBase.run()`, which now wraps `run_api()`.
- `run.py` – entry point for synchronous one-off agent execution.
- `app/api/` – FastAPI application exposing REST + WebSocket endpoints.
- `app/worker/` – Celery integration for background execution.

## Agent Template Expectations

1. **Single brain function**: `brain.py` exposes `run_prompt()` (sync or async).
2. **Interface adapters**: `interface/api.py` wraps the brain and handles
   event-loop logic.
3. **External prompts**: All instructions live in `prompts/*.txt`.
4. **Manifest**: `manifest.yaml` documents capabilities, required environment
   variables, and tooling metadata.
5. **Tests**: Place agent-specific tests under `app/agents/<key>/tests/`.

## Execution Paths

- `python run.py agent adam --message "Hello"` – synchronous smoke test that
  now flows through `run_api()`.
- API and Celery workers call `run_api()` automatically, ensuring structured
  responses for mobile/web clients.

## Adding New Agents

1. Copy `app/agents/adam/` as a starting point.
2. Update prompts, manifest, and tests to reflect the new capability.
3. Register any new configuration requirements in `docs/configuration.md` and
   `.env.example`.
4. Ensure `AgentStore` discovers the new manifest by keeping the directory under
   `app/agents/<key>/`.
5. Document behaviour in `docs/agent-architecture-standards.md`.

## TmatesAgentsSDK (Internal)

Use `app.sdk.agents.tmates_agents_sdk` as the lightweight runtime when building
or modernising agents:

- `TmatesAgentsSDK` instantiates the shared `Agent` object, wires Logfire/OpenAI
  clients, and exposes an async `run_prompt()` that automatically persists
  SQL-backed session memory.
- `run_agent_api_request()` centralises the synchronous API adapter with session
  management, event-loop handling, and optional generated-attachment hydration.
- `AgentRuntimeConfig` replaces the bespoke `AdamConfig`, so individual agents
  only need unique constants (e.g., `DEFAULT_MODEL`) rather than duplicating the
  environment parsing logic.

Agents can opt into the SDK incrementally—simply import `TmatesAgentsSDK`,
instantiate it inside `brain.py`, and delegate the FastAPI handler in
`interface/api.py` to `run_agent_api_request()`. Custom behaviour such as
attachments or enriched run-contexts plugs in via the helper's optional hooks.

Legacy agents such as Dwight, Newman, Leonardo, Nolan, or Omie have been fully
removed. Any references to them should be treated as historical context only
and live under `docs/archive/legacy/`.
