import os
import sys
from typing import Dict, List, Tuple
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from logs import log
from app.auth import UserContext
from app.core import (
    apply_user_context_to_env as _apply_user_context_to_env,
    resolve_user_context as _resolve_user_context,
    run_worker,
)


def _parse_cli_args(extra_args: List[str]) -> Tuple[Dict[str, object], List[str]]:
    """Parse CLI-style ``--key value`` pairs and return remaining positional args."""

    remaining: List[str] = []

    for token in extra_args:
        if token == "--mode":
            raise ValueError("Legacy --mode flag has been removed; please drop it from the command.")
        remaining.append(token)

    consumed_indexes: set[int] = set()
    cli_params: Dict[str, object] = {}

    i = 0
    while i < len(remaining):
        token = remaining[i]
        if token.startswith("--") and len(token) > 2:
            key = token[2:].strip().replace("-", "_")
            if not key:
                i += 1
                continue
            consumed_indexes.add(i)
            value: object = True
            if i + 1 < len(remaining) and not remaining[i + 1].startswith("--"):
                value = remaining[i + 1]
                consumed_indexes.add(i + 1)
                i += 2
            else:
                i += 1
            cli_params[key] = value
        else:
            i += 1

    residual = [token for idx, token in enumerate(remaining) if idx not in consumed_indexes]
    return cli_params, residual


def _print_usage() -> None:
    usage = (
        "Usage:\n"
        "  WORKER_KEY=<agent_key> python run.py [agent <agent_key>] [options]\n"
        "  python run.py agent <agent_key> [options]\n"
        "Legacy compatibility: python run.py <agent_key> [options]\n"
    )
    print(usage.strip())

def main(argv: list[str] | None = None):
    args = argv or sys.argv[1:]
    env_key = os.getenv("WORKER_KEY")
    
    # Parse --user-id argument
    user_id = None
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--user-id" and i + 1 < len(args):
            user_id = args[i + 1]
            i += 2  # Skip both --user-id and the value
        else:
            filtered_args.append(args[i])
            i += 1
    
    key: str | None = None
    extra_args: list[str] = []
    legacy_invocation = False

    if env_key:
        key = env_key
        extra_args = filtered_args
    else:
        if not filtered_args:
            _print_usage()
            return 1

        command = filtered_args[0]
        if command == "agent":
            if len(filtered_args) < 2:
                print("[dispatcher error] Missing agent key after 'agent' command.", file=sys.stderr)
                _print_usage()
                return 1
            key = filtered_args[1]
            extra_args = filtered_args[2:]
        else:
            # Legacy direct invocation support (python run.py <agent>)
            key = command
            extra_args = filtered_args[1:]
            legacy_invocation = True

    if not key:
        _print_usage()
        return 1

    if legacy_invocation:
        print(
            f"[dispatcher] Legacy invocation detected for agent '{key}'. "
            "Use 'python run.py agent <agent_key>' instead.",
            file=sys.stderr,
        )

    try:
        cli_params, residual_args = _parse_cli_args(extra_args)
    except ValueError as exc:
        print(f"[dispatcher error] {exc}", file=sys.stderr)
        return 1

    task_name = cli_params.pop("task", None)
    if task_name is not None:
        if isinstance(task_name, bool):
            print(
                "[dispatcher error] --task flag requires a value.",
                file=sys.stderr,
            )
            return 1
        # Task value is passed to the agent as a parameter
        cli_params["task"] = str(task_name).strip()

    if not cli_params and not residual_args:
        log(
            f"[dispatcher] no CLI parameters supplied for agent '{key}'. "
            f"Nothing to do; provide options like '--prompt \"...\"' or '--task <task_name>'."
        )
        return 0

    env_overrides: dict[str, str] = {}

    user_context_override: UserContext | None = None
    if user_id:
        try:
            user_context_override, org, enabled_agents = _resolve_user_context(user_id)
        except LookupError as exc:
            print(f"[dispatcher error] {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"[dispatcher error] Failed to load user context for {user_id}: {exc}", file=sys.stderr)
            return 1

        _apply_user_context_to_env(user_context_override)
        org_name = org.get("name") if isinstance(org, dict) else str(org)
        log(
            f"[dispatcher] user {user_id} has {len(enabled_agents)} agents in organization '{org_name}'"
        )

    try:
        result = run_worker(
            key,
            cli_args=cli_params,
            env_overrides=env_overrides,
            user_context=user_context_override,
            extra_args=residual_args,
        )
        return int(result or 0)
    except Exception as exc:
        print(f"[dispatcher error] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
