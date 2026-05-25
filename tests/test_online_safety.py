"""Tests for the online-tool safety controls: env-var credential references
and the confirm-gate on physical-impact ops.

These are enforced host-side (before anything reaches CODESYS), so they're
testable with a fake IPC client and no live runtime.
"""
from __future__ import annotations

import json

import pytest

from mcptoolkit_for_codesys.schemas import Result
from mcptoolkit_for_codesys.tools import REGISTRY, ToolContext


class _FakeIpc:
    """Records the last call and returns a canned ok Result."""
    def __init__(self):
        self.workdir = None
        self.calls = []

    async def call(self, op, args=None, timeout_s=120.0):
        self.calls.append((op, dict(args or {})))
        return Result(id="x", status="ok", data={"op": op, "args": args or {}})


def _ctx():
    ipc = _FakeIpc()
    return ToolContext(ipc=ipc, install=object(), manager=object()), ipc


async def _run(tool_name, args):
    ctx, ipc = _ctx()
    spec = REGISTRY.get(tool_name)
    out = await spec.handler(ctx, args)
    return json.loads(out), ipc


class TestConfirmGate:
    @pytest.mark.parametrize("tool,op", [
        ("codesys.online.start", "online.start"),
        ("codesys.online.reset", "online.reset"),
        ("codesys.online.write", "online.write"),
        ("codesys.online.force", "online.force"),
    ])
    async def test_blocked_without_confirm(self, tool, op):
        out, ipc = await _run(tool, {"expression": "X", "value": 1})
        assert out["status"] == "error"
        assert out["error_kind"] == "ConfirmationRequired"
        assert ipc.calls == []  # never reached the watcher

    async def test_proceeds_with_confirm_and_strips_it(self):
        out, ipc = await _run("codesys.online.start", {"confirm": True})
        assert out["status"] == "ok"
        assert len(ipc.calls) == 1
        op, args = ipc.calls[0]
        assert op == "online.start"
        assert "confirm" not in args  # not forwarded to the watcher

    async def test_stop_and_read_are_not_gated(self):
        # stop/read don't actuate — no confirm required.
        out, ipc = await _run("codesys.online.stop", {})
        assert out["status"] == "ok"
        assert ipc.calls[0][0] == "online.stop"


class TestEnvCredentials:
    async def test_password_env_resolved(self, monkeypatch):
        monkeypatch.setenv("MY_PLC_PW", "Str0ng_Pass!")
        out, ipc = await _run("codesys.online.login", {"username": "admin", "password_env": "MY_PLC_PW"})
        assert out["status"] == "ok"
        op, args = ipc.calls[0]
        assert args["password"] == "Str0ng_Pass!"
        assert "password_env" not in args  # reference not forwarded

    async def test_missing_env_var_errors(self, monkeypatch):
        monkeypatch.delenv("ABSENT_PW", raising=False)
        out, ipc = await _run("codesys.online.login", {"username": "admin", "password_env": "ABSENT_PW"})
        assert out["status"] == "error"
        assert out["error_kind"] == "MissingEnvCredential"
        assert ipc.calls == []

    async def test_explicit_password_still_works(self):
        out, ipc = await _run("codesys.online.login", {"username": "admin", "password": "Str0ng_Pass!"})
        assert out["status"] == "ok"
        assert ipc.calls[0][1]["password"] == "Str0ng_Pass!"
