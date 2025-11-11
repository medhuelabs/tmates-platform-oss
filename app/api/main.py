"""FastAPI application exposing the public JSON API."""

from __future__ import annotations

import os
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from .routes import (
    agent_catalog,
    agents,
    agent_results,
    billing,
    chats,
    dynamic_agents,
    gmail,
    files,
    jobs,
    pinboard,
    profile,
    settings,
    teammates,
    websocket,
)


load_dotenv()

app = FastAPI(
    title=os.getenv("API_TITLE", "Tmates Public API"),
    version=os.getenv("API_VERSION", "1.0.0"),
    description=(
        "Public JSON API for tmates-platform mobile and third-party clients. "
        "Authenticate using a Supabase JWT in the Authorization header."
    ),
)


def _configure_cors(api_app: FastAPI) -> None:
    raw_origins = os.getenv("API_CORS_ORIGINS", "").strip()
    if not raw_origins:
        return

    origins: List[str] = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    if not origins:
        return

    api_app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


_configure_cors(app)


@app.get("/health", tags=["health"])  # pragma: no cover - trivial fast check
def healthcheck() -> dict[str, str]:
    """Simple health endpoint for load balancers and smoke tests."""

    return {"status": "ok"}


app.include_router(agent_catalog.router, prefix="/v1", tags=["agent-catalog"])
app.include_router(agents.router, prefix="/v1", tags=["agents"])
app.include_router(dynamic_agents.router, prefix="/v1", tags=["dynamic-agents"])
app.include_router(jobs.router, prefix="/v1", tags=["jobs"])
app.include_router(gmail.router, prefix="/v1", tags=["integrations"])
app.include_router(pinboard.router, prefix="/v1", tags=["pinboard"])
app.include_router(teammates.router, prefix="/v1", tags=["teammates"])
app.include_router(chats.router, prefix="/v1", tags=["chats"])
app.include_router(files.router, prefix="/v1", tags=["files"])
app.include_router(profile.router, prefix="/v1", tags=["profile"])
app.include_router(settings.router, prefix="/v1", tags=["settings"])
app.include_router(websocket.router, prefix="/v1", tags=["websocket"])
app.include_router(agent_results.router, prefix="/v1", tags=["agent-results"])
app.include_router(billing.router, prefix="/v1", tags=["billing"])
