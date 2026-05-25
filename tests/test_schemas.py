"""Round-trip tests for the JSON IPC schemas (Command / Result).

The wire format between host and watcher is JSON-serialized Pydantic models;
the watcher emits them as plain dicts via `json.dumps`. Anything that breaks
the JSON shape on either side breaks the spine. Test the shape here.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from mcptoolkit_for_codesys.schemas import Command, Result


class TestCommand:
    def test_minimal(self):
        cmd = Command(id="abc", op="ping")
        assert cmd.id == "abc"
        assert cmd.op == "ping"
        assert cmd.args == {}
        assert cmd.deadline_s == 120.0

    def test_with_args(self):
        cmd = Command(id="abc", op="project.open", args={"path": "C:\\x.project"})
        assert cmd.args["path"] == "C:\\x.project"

    def test_json_roundtrip(self):
        cmd = Command(id="abc", op="ping", args={"echo": "hi"}, deadline_s=30.0)
        wire = cmd.model_dump_json()
        decoded = json.loads(wire)
        assert decoded["id"] == "abc"
        assert decoded["op"] == "ping"
        assert decoded["args"] == {"echo": "hi"}
        assert decoded["deadline_s"] == 30.0
        cmd2 = Command.model_validate_json(wire)
        assert cmd2 == cmd

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            Command()  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            Command(id="abc")  # type: ignore[call-arg]

    def test_extra_fields_ignored_or_kept(self):
        """Pydantic v2 default is 'ignore' for extra fields; either behavior
        is fine for forward compat as long as it doesn't raise."""
        Command.model_validate({"id": "a", "op": "x", "spurious": True})


class TestResult:
    def test_ok(self):
        r = Result(id="abc", status="ok", data={"v": 1}, elapsed_ms=12)
        assert r.status == "ok"
        assert r.error is None
        assert r.error_kind is None

    def test_error(self):
        r = Result(
            id="abc",
            status="error",
            error="boom",
            error_kind="HandlerException",
            elapsed_ms=3,
        )
        assert r.status == "error"
        assert r.error == "boom"
        assert r.error_kind == "HandlerException"

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            Result(id="abc", status="pending")  # type: ignore[arg-type]

    def test_watcher_dict_shape(self):
        """The watcher writes plain dicts; verify Result parses what it emits."""
        watcher_emitted = {
            "id": "x",
            "status": "ok",
            "data": {"pong": True, "echo": "hi"},
            "error": None,
            "error_kind": None,
            "elapsed_ms": 5,
        }
        r = Result.model_validate(watcher_emitted)
        assert r.data["pong"] is True

    def test_watcher_error_shape(self):
        watcher_emitted = {
            "id": "x",
            "status": "error",
            "data": {},
            "error": "Traceback...\nKeyError: 'foo'\n",
            "error_kind": "HandlerException",
            "elapsed_ms": 1,
        }
        r = Result.model_validate(watcher_emitted)
        assert r.status == "error"
        assert "KeyError" in r.error
