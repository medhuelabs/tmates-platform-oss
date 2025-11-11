"""Utility to inspect user profile records in the database.

Run with:

    python -m scripts.inspect_user_profile --auth-id <uuid>

Requires SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY environment variables.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict


def get_database():
    from app.db import get_database_client  # Lazy import to ensure env is loaded

    return get_database_client()


def dump(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


def inspect_profile(auth_id: str) -> int:
    db = get_database()
    profile = db.get_user_profile_by_auth_id(auth_id)
    if not profile:
        print("No profile found", file=sys.stderr)
        return 1

    print("Profile record:\n" + dump(profile))

    context = db.get_user_context(auth_id)
    if context:
        print("\nResolved user context:\n" + dump(context.__dict__))
    else:
        print("\nNo user context available")

    org = db.get_user_organization(auth_id)
    if org:
        print("\nPrimary organization:\n" + dump(org))
    else:
        print("\nNo primary organization found")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a user profile from the database")
    parser.add_argument("--auth-id", required=True, help="Auth user UUID")
    args = parser.parse_args()

    return inspect_profile(args.auth_id)


if __name__ == "__main__":
    raise SystemExit(main())
