"""Tests for the server's CLI/env configuration layer.

The precedence chain is documented as: command-line flag > environment
variable > built-in default. These tests pin that chain so a refactor
can't silently change how a deployment is configured.
"""
from __future__ import annotations

import argparse

import pytest

from mcptoolkit_for_codesys.server import (
    DEFAULT_HEADLESS_STARTUP_TIMEOUT_S,
    DEFAULT_STARTUP_TIMEOUT_S,
    _build_parser,
    _env_startup_timeout,
    _resolve_startup_timeout,
)


class TestStartupTimeout:
    def test_default_is_90_seconds(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MCPTOOLKIT_STARTUP_TIMEOUT", raising=False)
        assert _resolve_startup_timeout(None, headless=False) == DEFAULT_STARTUP_TIMEOUT_S

    def test_headless_default_is_longer(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MCPTOOLKIT_STARTUP_TIMEOUT", raising=False)
        timeout = _resolve_startup_timeout(None, headless=True)
        assert timeout == DEFAULT_HEADLESS_STARTUP_TIMEOUT_S
        assert timeout > DEFAULT_STARTUP_TIMEOUT_S

    def test_headless_default_covers_the_measured_cold_start(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # A cold headless start on SP22 was measured at ~110 s.
        monkeypatch.delenv("MCPTOOLKIT_STARTUP_TIMEOUT", raising=False)
        assert _resolve_startup_timeout(None, headless=True) >= 110

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MCPTOOLKIT_STARTUP_TIMEOUT", "300")
        assert _env_startup_timeout() == 300.0
        assert _resolve_startup_timeout(None, headless=False) == 300.0
        assert _resolve_startup_timeout(None, headless=True) == 300.0

    def test_flag_overrides_env_var(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MCPTOOLKIT_STARTUP_TIMEOUT", "300")
        args = _build_parser().parse_args(["--startup-timeout", "240"])
        assert _resolve_startup_timeout(args.startup_timeout, headless=False) == 240.0

    def test_invalid_env_var_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MCPTOOLKIT_STARTUP_TIMEOUT", "not-a-number")
        assert _env_startup_timeout() is None
        assert _resolve_startup_timeout(None, headless=False) == DEFAULT_STARTUP_TIMEOUT_S

    def test_non_positive_env_var_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MCPTOOLKIT_STARTUP_TIMEOUT", "0")
        assert _env_startup_timeout() is None

    def test_non_positive_flag_is_rejected(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--startup-timeout", "0"])
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--startup-timeout", "-5"])

    def test_non_numeric_flag_is_rejected(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--startup-timeout", "soon"])


class TestHeadlessFlag:
    def test_headless_defaults_off(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MCPTOOLKIT_HEADLESS", raising=False)
        assert _build_parser().parse_args([]).headless is False

    def test_headless_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MCPTOOLKIT_HEADLESS", raising=False)
        assert _build_parser().parse_args(["--headless"]).headless is True


class TestParser:
    def test_parser_is_argparse(self):
        assert isinstance(_build_parser(), argparse.ArgumentParser)
