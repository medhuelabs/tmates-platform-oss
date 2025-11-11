"""Environment-driven runtime settings for the automation suite."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Optional, Sequence, Tuple


def _env_str(
    name: str,
    default: Optional[str] = None,
    *,
    alias: Optional[str] = None,
    empty_to_none: bool = True,
) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None and alias:
        raw = os.getenv(alias)
    if raw is None:
        return default
    value = raw.strip()
    if not value and empty_to_none:
        return None if default is None else default
    return value if value else default


def _env_bool(name: str, default: bool, *, alias: Optional[str] = None) -> bool:
    raw = os.getenv(name)
    if raw is None and alias:
        raw = os.getenv(alias)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float, *, alias: Optional[str] = None) -> float:
    raw = os.getenv(name)
    if raw is None and alias:
        raw = os.getenv(alias)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_tuple(name: str, default: Sequence[str]) -> Tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return tuple(default)
    items = tuple(part.strip() for part in raw.split(",") if part.strip())
    return items or tuple(default)


def _discover_available_agents() -> Tuple[str, ...]:
    """Discover available agents dynamically from the filesystem."""
    try:
        from app.registry.agents.store import AgentStore
        agent_store = AgentStore()
        agents = agent_store.get_available_agents()
        return tuple(agent.key for agent in agents)
    except Exception:
        # Fallback to empty tuple if discovery fails
        return tuple()


class Settings(SimpleNamespace):
    """Simple attribute container used throughout the codebase."""

CONFIG = Settings()


def _compute_values() -> tuple[dict[str, object], dict[str, object]]:
    def _first_env_str(*names: str, default: Optional[str] = None, empty_to_none: bool = True) -> Optional[str]:
        for env_name in names:
            value = _env_str(env_name, None, empty_to_none=empty_to_none)
            if value is not None:
                return value
        return default

    def _first_env_bool(*names: str, default: bool) -> bool:
        for env_name in names:
            if os.getenv(env_name) is not None:
                return _env_bool(env_name, default)
        return default

    def _first_env_int(*names: str, default: int) -> int:
        for env_name in names:
            if os.getenv(env_name) is not None:
                return _env_int(env_name, default)
        return default

    # -----------------------------------------------------------------------
    # RUNTIME ENVIRONMENT
    # -----------------------------------------------------------------------
    environment = _env_str("ENV", "prod", empty_to_none=False).lower()
    if environment not in {"dev", "prod"}:
        environment = "prod"
    is_development = environment == "dev"

    catalog_enabled = _env_bool("AGENT_CATALOG_ENABLED", False)
    catalog_environment = _env_str("AGENT_CATALOG_ENV", environment, empty_to_none=False).lower()
    if catalog_environment not in {"dev", "staging", "prod"}:
        catalog_environment = "prod" if environment != "dev" else "dev"

    # -----------------------------------------------------------------------
    # SUPABASE STORAGE CONFIGURATION
    # -----------------------------------------------------------------------
    supabase_url = _env_str("SUPABASE_URL", None)
    supabase_anon_key = _env_str("SUPABASE_ANON_KEY", None)
    supabase_service_role_key = _env_str("SUPABASE_SERVICE_ROLE_KEY", None)
    supabase_storage_bucket = _env_str(
        "SUPABASE_STORAGE_BUCKET",
        "user-files",
        empty_to_none=False,
    )
    supabase_storage_prefix = _env_str(
        "SUPABASE_STORAGE_PREFIX",
        "users",
        empty_to_none=False,
    )
    supabase_signed_url_ttl = _env_int("SUPABASE_SIGNED_URL_TTL", 3600)
    supabase_configured = bool(supabase_url) and bool(supabase_service_role_key)

    # -----------------------------------------------------------------------
    # FILE STORAGE BACKEND SELECTION
    # -----------------------------------------------------------------------
    requested_backend = _env_str("FILE_STORAGE_BACKEND", None, empty_to_none=False)
    s3_bucket_name = _env_str("S3_BUCKET_NAME", None)
    s3_storage_prefix = _env_str("S3_STORAGE_PREFIX", "users", empty_to_none=False)
    s3_signed_url_ttl = _env_int("S3_SIGNED_URL_TTL", 3600)
    aws_region = _env_str("AWS_REGION", None)
    aws_profile = _env_str("AWS_PROFILE", None)
    aws_access_key_id = _env_str("AWS_ACCESS_KEY_ID", None)
    aws_secret_access_key = _env_str("AWS_SECRET_ACCESS_KEY", None)
    aws_session_token = _env_str("AWS_SESSION_TOKEN", None)
    s3_endpoint_url = _env_str("S3_ENDPOINT_URL", None)
    s3_force_path_style = _env_bool("S3_FORCE_PATH_STYLE", False)

    # -----------------------------------------------------------------------
    # BILLING / STRIPE
    # -----------------------------------------------------------------------
    raw_stripe_billing_flag = _env_bool("STRIPE_BILLING_ENABLED", False)
    stripe_secret_key = _env_str("STRIPE_SECRET_KEY", None)
    stripe_publishable_key = _env_str("STRIPE_PUBLISHABLE_KEY", None)
    stripe_webhook_secret = _env_str("STRIPE_WEBHOOK_SECRET", None)
    stripe_checkout_success_url = _env_str("STRIPE_CHECKOUT_SUCCESS_URL", None)
    stripe_checkout_cancel_url = _env_str("STRIPE_CHECKOUT_CANCEL_URL", None)
    stripe_portal_return_url = _env_str("STRIPE_PORTAL_RETURN_URL", None)
    raw_price_overrides = _env_str("STRIPE_PRICE_OVERRIDES", None)
    billing_default_provider = _env_str("BILLING_PROVIDER_DEFAULT", "stripe", empty_to_none=False).lower()

    stripe_price_overrides: dict[str, dict[str, str]] = {}
    if raw_price_overrides:
        try:
            parsed = json.loads(raw_price_overrides)
            if isinstance(parsed, dict):
                # Normalize keys to lower-case intervals for consistent lookups.
                for plan_key, interval_map in parsed.items():
                    if not isinstance(interval_map, dict):
                        continue
                    normalized: dict[str, str] = {}
                    for interval_name, price_id in interval_map.items():
                        if isinstance(interval_name, str) and isinstance(price_id, str) and price_id.strip():
                            normalized[interval_name.strip().lower()] = price_id.strip()
                    if normalized:
                        stripe_price_overrides[str(plan_key)] = normalized
        except json.JSONDecodeError:
            stripe_price_overrides = {}

    stripe_configured = bool(stripe_secret_key)
    stripe_billing_enabled = raw_stripe_billing_flag and stripe_configured

    allowed_backends = {"local", "supabase", "s3"}
    if requested_backend:
        candidate_backend = requested_backend.lower()
        if candidate_backend not in allowed_backends:
            candidate_backend = (
                "supabase"
                if supabase_configured and not is_development
                else "s3"
                if s3_bucket_name
                else "local"
            )
    else:
        if is_development:
            candidate_backend = "local"
        elif supabase_configured:
            candidate_backend = "supabase"
        elif s3_bucket_name:
            candidate_backend = "s3"
        else:
            candidate_backend = "local"

    file_storage_backend = candidate_backend
    use_supabase_storage = file_storage_backend == "supabase"

    # -----------------------------------------------------------------------
    # APPLICATION SECRETS
    # -----------------------------------------------------------------------
    session_secret = _first_env_str("SESSION_SECRET", default=None)
    file_view_token_secret = _first_env_str("FILE_VIEW_TOKEN_SECRET", default=None)

    # -----------------------------------------------------------------------
    # WHO IS RUNNING THESE AGENTS?
    # -----------------------------------------------------------------------
    user_display_name = _env_str("USER_DISPLAY_NAME", "Default User", empty_to_none=False)
    user_email = _env_str("USER_EMAIL", None)

    # -----------------------------------------------------------------------
    # LOGGING & OBSERVABILITY
    # -----------------------------------------------------------------------
    suppress_system_logs = _env_bool("SUPPRESS_SYSTEM_LOGS", True)
    system_log_prefixes = _env_tuple(
        "SYSTEM_LOG_PREFIXES",
        ("[openai]", "[runs]", "[dispatcher]", "[interpreter]"),
    )

    # -----------------------------------------------------------------------
    # DETAILS INTERPRETER (LLM-ASSISTED TASK PARSING)
    # -----------------------------------------------------------------------
    details_interpreter_enabled = _env_bool("DETAILS_INTERPRETER_ENABLED", False)
    details_interpreter_model = _env_str(
        "DETAILS_INTERPRETER_MODEL", "gpt-5-mini", empty_to_none=False
    )
    details_interpreter_system_prompt = _env_str(
        "DETAILS_INTERPRETER_SYSTEM_PROMPT", None
    )
    details_interpreter_temperature = _env_float(
        "DETAILS_INTERPRETER_TEMPERATURE", 0.0
    )

    # -----------------------------------------------------------------------
    # INSTALLED AGENTS
    # -----------------------------------------------------------------------
    installed_agents: Tuple[str, ...] = _env_tuple(
        "INSTALLED_AGENTS",
        _discover_available_agents(),  # Discover agents dynamically
    )

    globals_map = {
        "ENVIRONMENT": environment,
        "IS_DEVELOPMENT": is_development,
        "AGENT_CATALOG_ENABLED": catalog_enabled,
        "AGENT_CATALOG_ENVIRONMENT": catalog_environment,
        "USER_DISPLAY_NAME": user_display_name,
        "USER_EMAIL": user_email,
        "SUPPRESS_SYSTEM_LOGS": suppress_system_logs,
        "SYSTEM_LOG_PREFIXES": system_log_prefixes,
        "DETAILS_INTERPRETER_ENABLED": details_interpreter_enabled,
        "DETAILS_INTERPRETER_MODEL": details_interpreter_model,
        "DETAILS_INTERPRETER_SYSTEM_PROMPT": details_interpreter_system_prompt,
        "DETAILS_INTERPRETER_TEMPERATURE": details_interpreter_temperature,
        "INSTALLED_AGENTS": installed_agents,
        "SUPABASE_URL": supabase_url,
        "SUPABASE_ANON_KEY": supabase_anon_key,
        "SUPABASE_SERVICE_ROLE_KEY": supabase_service_role_key,
        "SUPABASE_STORAGE_BUCKET": supabase_storage_bucket,
        "SUPABASE_STORAGE_PREFIX": supabase_storage_prefix,
        "SUPABASE_SIGNED_URL_TTL": supabase_signed_url_ttl,
        "USE_SUPABASE_STORAGE": use_supabase_storage,
        "FILE_STORAGE_BACKEND": file_storage_backend,
        "S3_BUCKET_NAME": s3_bucket_name,
        "S3_STORAGE_PREFIX": s3_storage_prefix,
        "S3_SIGNED_URL_TTL": s3_signed_url_ttl,
        "AWS_REGION": aws_region,
        "AWS_PROFILE": aws_profile,
        "AWS_ACCESS_KEY_ID": aws_access_key_id,
        "AWS_SECRET_ACCESS_KEY": aws_secret_access_key,
        "AWS_SESSION_TOKEN": aws_session_token,
        "S3_ENDPOINT_URL": s3_endpoint_url,
        "S3_FORCE_PATH_STYLE": s3_force_path_style,
        "SESSION_SECRET": session_secret,
        "FILE_VIEW_TOKEN_SECRET": file_view_token_secret,
        "STRIPE_BILLING_ENABLED": stripe_billing_enabled,
        "STRIPE_SECRET_KEY": stripe_secret_key,
        "STRIPE_PUBLISHABLE_KEY": stripe_publishable_key,
        "STRIPE_WEBHOOK_SECRET": stripe_webhook_secret,
        "STRIPE_CHECKOUT_SUCCESS_URL": stripe_checkout_success_url,
        "STRIPE_CHECKOUT_CANCEL_URL": stripe_checkout_cancel_url,
    "STRIPE_PORTAL_RETURN_URL": stripe_portal_return_url,
    "STRIPE_PRICE_OVERRIDES": raw_price_overrides,
    "BILLING_PROVIDER_DEFAULT": billing_default_provider,
    "STRIPE_PRICE_OVERRIDES_MAP": stripe_price_overrides,
    "STRIPE_PRICE_OVERRIDES": raw_price_overrides,
    }

    config_map = {
        "environment": environment,
        "is_development": is_development,
        "agent_catalog_enabled": catalog_enabled,
        "agent_catalog_environment": catalog_environment,
        "suppress_system_logs": suppress_system_logs,
        "system_log_prefixes": system_log_prefixes,
        "details_interpreter_enabled": details_interpreter_enabled,
        "details_interpreter_model": details_interpreter_model,
        "details_interpreter_system_prompt": details_interpreter_system_prompt,
        "details_interpreter_temperature": details_interpreter_temperature,
        "user_display_name": user_display_name,
        "user_email": user_email,
        "supabase_url": supabase_url,
        "supabase_anon_key": supabase_anon_key,
        "supabase_service_role_key": supabase_service_role_key,
        "supabase_storage_bucket": supabase_storage_bucket,
        "supabase_storage_prefix": supabase_storage_prefix,
        "supabase_signed_url_ttl": supabase_signed_url_ttl,
        "use_supabase_storage": use_supabase_storage,
        "file_storage_backend": file_storage_backend,
        "s3_bucket_name": s3_bucket_name,
        "s3_storage_prefix": s3_storage_prefix,
        "s3_signed_url_ttl": s3_signed_url_ttl,
        "aws_region": aws_region,
        "aws_profile": aws_profile,
        "aws_access_key_id": aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "aws_session_token": aws_session_token,
        "s3_endpoint_url": s3_endpoint_url,
        "s3_force_path_style": s3_force_path_style,
        "session_secret": session_secret,
        "file_view_token_secret": file_view_token_secret,
        "stripe_billing_flag": raw_stripe_billing_flag,
        "stripe_billing_enabled": stripe_billing_enabled,
        "stripe_billing_configured": stripe_configured,
        "stripe_secret_key": stripe_secret_key,
        "stripe_publishable_key": stripe_publishable_key,
        "stripe_webhook_secret": stripe_webhook_secret,
        "stripe_checkout_success_url": stripe_checkout_success_url,
        "stripe_checkout_cancel_url": stripe_checkout_cancel_url,
        "stripe_portal_return_url": stripe_portal_return_url,
        "stripe_price_overrides": stripe_price_overrides,
        "billing_default_provider": billing_default_provider,
    }

    return globals_map, config_map


def reload_config() -> None:
    globals_map, config_map = _compute_values()
    globals().update(globals_map)
    CONFIG.__dict__.update(config_map)


def load_envs(global_dir: str, agent_key: str | None = None) -> None:
    """Load environment variables from global and agent-specific .env files."""
    from dotenv import load_dotenv
    
    # Load global .env
    load_dotenv(os.path.join(global_dir, ".env"))
    
    # Load agent-specific .env if provided
    if agent_key:
        agent_env = os.path.join(global_dir, "app", "agents", agent_key, ".env")
        if os.path.isfile(agent_env):
            load_dotenv(agent_env, override=True)
    
    # Reload configuration after environment changes
    reload_config()
    
    # Refresh settings module if available
    try:
        from .db import settings as _settings
        _settings.refresh_from_env()
    except Exception:
        pass  # Settings module not available or doesn't have refresh_from_env


# Load once on import so downstream modules can use CONFIG immediately.
reload_config()
