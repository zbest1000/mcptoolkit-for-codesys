"""Tests for the host-side error-envelope contract.

Every error an LLM sees from a tool should be a parseable JSON object with a
machine-readable `error_kind` and `status: error` — never a bare string. And
when a watcher Result is an error, format_result should surface a concise
`error_summary` so the model gets the gist without parsing a full traceback.
"""
from __future__ import annotations

import json

from mcptoolkit_for_codesys.schemas import Result
from mcptoolkit_for_codesys.tools import error_envelope, format_result


class TestErrorEnvelope:
    def test_is_valid_json_with_required_fields(self):
        out = error_envelope("IpcTimeout", "no result after 60s")
        d = json.loads(out)
        assert d["status"] == "error"
        assert d["error_kind"] == "IpcTimeout"
        assert d["error"] == "no result after 60s"

    def test_extra_fields_merge(self):
        out = error_envelope("X", "msg", advice="retry", hang_diagnosis={"dialog_count": 1})
        d = json.loads(out)
        assert d["advice"] == "retry"
        assert d["hang_diagnosis"]["dialog_count"] == 1


class TestFormatResultErrorSummary:
    def test_error_result_gets_summary_last_line(self):
        r = Result(
            id="1",
            status="error",
            error="Traceback (most recent call last):\n  File ...\nRuntimeError: Object not found: Foo",
            error_kind="HandlerException",
        )
        out = json.loads(format_result(r))
        assert out["error_summary"] == "RuntimeError: Object not found: Foo"
        # full traceback preserved
        assert "Traceback" in out["error"]

    def test_ok_result_has_no_summary(self):
        r = Result(id="1", status="ok", data={"x": 1})
        out = json.loads(format_result(r))
        assert "error_summary" not in out

    def test_error_without_error_text_is_safe(self):
        r = Result(id="1", status="error", error=None, error_kind="X")
        out = json.loads(format_result(r))
        assert "error_summary" not in out
