"""Synchronize user_profiles.display_name with Supabase Auth metadata.

Usage:
    python -m scripts.backfill_user_profile_display_names

Requires SUPABASE_URL plus SUPABASE_SERVICE_ROLE_KEY (or anon key for read-only)
so the Supabase client and Auth admin API can be accessed.
"""

from __future__ import annotations

import json
from typing import Dict, List

from app.auth.manager import get_auth_manager
from app.db import get_database_client


def main() -> int:
    db = get_database_client()
    auth_manager = get_auth_manager()

    client = getattr(db, "client", None)
    if client is None:
        raise RuntimeError("Database client does not expose a Supabase client; run against Supabase environment")

    result = client.table("user_profiles").select("auth_user_id, display_name, email").execute()
    rows: List[Dict[str, str]] = result.data or []

    updates = 0
    skipped = 0

    for row in rows:
        auth_user_id = row.get("auth_user_id")
        if not auth_user_id:
            skipped += 1
            continue

        auth_user = auth_manager.get_auth_user(auth_user_id)
        if not auth_user:
            skipped += 1
            continue

        auth_display = auth_user.get("display_name")
        if not auth_display:
            email = auth_user.get("email")
            auth_display = email.split("@")[0] if email else None

        if not auth_display:
            skipped += 1
            continue

        profile_display = row.get("display_name")
        if profile_display == auth_display:
            continue

        db.update_user_profile(auth_user_id, {"display_name": auth_display})
        updates += 1

    summary = {"updated": updates, "skipped": skipped, "total": len(rows)}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
