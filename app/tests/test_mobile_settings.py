from app.db.settings import (
    DEFAULT_MOBILE_SETTINGS,
    MOBILE_SETTINGS_KEY,
    load_user_mobile_settings,
    save_user_mobile_settings,
)


def test_load_user_mobile_settings_returns_defaults_when_missing(monkeypatch):
    def fake_load_user_system_settings(user_id: str):
        return {"SUPPRESS_SYSTEM_LOGS": True}

    monkeypatch.setattr(
        "app.db.settings.load_user_system_settings",
        fake_load_user_system_settings,
    )

    settings = load_user_mobile_settings("user-123")

    assert settings == DEFAULT_MOBILE_SETTINGS
    assert settings is not DEFAULT_MOBILE_SETTINGS  # ensure copy returned


def test_save_user_mobile_settings_merges_updates(monkeypatch):
    persisted_payload = {}

    def fake_load_user_system_settings(user_id: str):
        return {
            MOBILE_SETTINGS_KEY: {
                "allow_notifications": False,
                "mentions": False,
                "usage_analytics": True,
                "theme_preference": "dark",
            }
        }

    def fake_save_user_system_settings(user_id: str, payload):
        nonlocal persisted_payload
        persisted_payload = payload
        return True

    monkeypatch.setattr(
        "app.db.settings.load_user_system_settings",
        fake_load_user_system_settings,
    )
    monkeypatch.setattr(
        "app.db.settings.save_user_system_settings",
        fake_save_user_system_settings,
    )

    success, merged = save_user_mobile_settings(
        "user-123",
        {"mentions": True, "usage_analytics": False},
    )

    assert success is True
    assert merged["allow_notifications"] is False
    assert merged["mentions"] is True
    assert merged["usage_analytics"] is False
    assert merged["theme_preference"] == "dark"
    assert persisted_payload[MOBILE_SETTINGS_KEY]["mentions"] is True
    assert persisted_payload[MOBILE_SETTINGS_KEY]["usage_analytics"] is False


def test_save_user_mobile_settings_ignores_invalid_values(monkeypatch):
    persisted_payload = {}

    def fake_load_user_system_settings(user_id: str):
        return {MOBILE_SETTINGS_KEY: {"theme_preference": "light"}}

    def fake_save_user_system_settings(user_id: str, payload):
        nonlocal persisted_payload
        persisted_payload = payload
        return True

    monkeypatch.setattr(
        "app.db.settings.load_user_system_settings",
        fake_load_user_system_settings,
    )
    monkeypatch.setattr(
        "app.db.settings.save_user_system_settings",
        fake_save_user_system_settings,
    )

    success, merged = save_user_mobile_settings(
        "user-123",
        {"theme_preference": "BLUE", "unknown": True},
    )

    assert success is True
    assert merged["theme_preference"] == "light"
    assert persisted_payload[MOBILE_SETTINGS_KEY]["theme_preference"] == "light"
    assert "unknown" not in persisted_payload[MOBILE_SETTINGS_KEY]
