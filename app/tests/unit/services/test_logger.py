"""Tests for the shared logging shim."""

from __future__ import annotations

import logging

from app import logger


def test_log_initializes_basic_config(caplog) -> None:
    caplog.set_level(logging.INFO)

    logger.log("hello", "world", foo="bar")

    assert any("hello world" in message for message in caplog.messages)
    assert any("foo" in message for message in caplog.messages)
