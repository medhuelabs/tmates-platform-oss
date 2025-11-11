"""
Lightweight run logging helpers retained for legacy interfaces.

Modern agents typically emit run metadata through Supabase or structured logs,
but a few older code paths still import these stubs. They intentionally no-op
apart from emitting debug logs so that removing Dwight does not break imports.
"""
from typing import Optional, Any
import logging
import uuid

# Set up a simple logger
logger = logging.getLogger(__name__)

def begin(agent_key: Optional[str], run_id: Optional[str]) -> None:
    """Initialize a run log session (stub implementation)."""
    if agent_key and run_id:
        logger.debug(f"[{agent_key}] Starting run {run_id}")

def append(agent_key: Optional[str], message: str) -> None:
    """Append a message to the run log (stub implementation)."""
    if agent_key and message:
        logger.debug(f"[{agent_key}] {message}")

def flush(_client: Any, agent_key: Optional[str], run_id: Optional[str]) -> None:
    """Flush the run log to storage (stub implementation)."""
    if agent_key and run_id:
        logger.debug(f"[{agent_key}] Completed run {run_id}")

# Additional legacy stubs maintained for backwards compatibility
def runs_create(_client: Any, task_id: Optional[str] = None, agent_key: Optional[str] = None, user_id: Optional[str] = None) -> str:
    """Create a run entry (stub implementation)."""
    run_id = str(uuid.uuid4())
    if agent_key:
        logger.debug(f"[{agent_key}] Created run {run_id}")
    return run_id

def runs_finish(_client: Any, run_id: str, status: str, agent_key: Optional[str] = None, details: Optional[str] = None) -> None:
    """Finish a run entry (stub implementation)."""
    if agent_key:
        logger.debug(f"[{agent_key}] Finished run {run_id} with status: {status}")

def register_run(agent_key: str, run_id: str, _client: Any) -> None:
    """Register a run (stub implementation)."""
    logger.debug(f"[{agent_key}] Registered run {run_id}")

def clear_run(run_id: str) -> None:
    """Clear a run (stub implementation)."""
    logger.debug(f"Cleared run {run_id}")
