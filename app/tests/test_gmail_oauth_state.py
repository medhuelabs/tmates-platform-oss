import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def _import_gmail_state():
    try:
        from app.services.google.gmail import GmailAuthError, GmailOAuthState  # type: ignore[import]
        return GmailAuthError, GmailOAuthState
    except ModuleNotFoundError:  # pragma: no cover - dependency optional in CI
        pytest.skip("Google auth dependencies are not installed.")


def test_gmail_oauth_state_round_trip() -> None:
    GmailAuthError, GmailOAuthState = _import_gmail_state()
    state = GmailOAuthState.issue("user-123")
    encoded = state.encode()

    decoded = GmailOAuthState.decode(encoded)
    assert decoded.user_id == state.user_id
    assert decoded.nonce == state.nonce


def test_gmail_oauth_state_invalid_payload() -> None:
    GmailAuthError, GmailOAuthState = _import_gmail_state()
    with pytest.raises(GmailAuthError):
        GmailOAuthState.decode("aW52YWxpZA")  # base64 for "invalid"
