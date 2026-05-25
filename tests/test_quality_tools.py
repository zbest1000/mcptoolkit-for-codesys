"""Tests for the host-side quality tools: project.diff and build.validate.

Both are pure host-side (diff is filesystem-only; validate orchestrates watcher
ops we stub here), so they're fully unit-testable without CODESYS.
"""
from __future__ import annotations

import json

import pytest

from mcptoolkit_for_codesys.schemas import Result
from mcptoolkit_for_codesys.tools import REGISTRY, ToolContext


class _FakeIpc:
    """Returns canned Results keyed by op."""
    def __init__(self, responses):
        self.workdir = None
        self.responses = responses
        self.calls = []

    async def call(self, op, args=None, timeout_s=120.0):
        self.calls.append(op)
        return self.responses[op]


def _ctx(ipc):
    return ToolContext(ipc=ipc, install=object(), manager=object())


async def _run(tool, ctx, args):
    return json.loads(await REGISTRY.get(tool).handler(ctx, args))


class TestProjectDiff:
    def _mirror(self, root, files: dict[str, str]):
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    async def test_added_removed_changed(self, tmp_path):
        base = tmp_path / "base"
        head = tmp_path / "head"
        self._mirror(base, {
            "PLC_PRG.st": "a\nb\nc\n",
            "POUs/FB_Old.st": "x\n",
        })
        self._mirror(head, {
            "PLC_PRG.st": "a\nB2\nc\nd\n",   # 1 changed line + 1 added line
            "POUs/FB_New.st": "y\n",          # added
        })
        out = await _run("codesys.project.diff", _ctx(_FakeIpc({})),
                         {"base_dir": str(base), "compare_dir": str(head)})
        assert out["status"] == "ok"
        assert out["summary"]["added"] == 1
        assert out["summary"]["removed"] == 1
        assert out["summary"]["changed"] == 1
        assert "POUs/FB_New.st" in out["added"]
        assert "POUs/FB_Old.st" in out["removed"]
        ch = out["changed"][0]
        assert ch["path"] == "PLC_PRG.st"
        assert ch["added_lines"] == 2 and ch["removed_lines"] == 1

    async def test_missing_dir_errors(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        out = await _run("codesys.project.diff", _ctx(_FakeIpc({})),
                         {"base_dir": str(base), "compare_dir": str(tmp_path / "nope")})
        assert out["status"] == "error"
        assert out["error_kind"] == "ValidationError"

    async def test_include_diff_text(self, tmp_path):
        base = tmp_path / "base"; head = tmp_path / "head"
        self._mirror(base, {"X.st": "one\n"})
        self._mirror(head, {"X.st": "two\n"})
        out = await _run("codesys.project.diff", _ctx(_FakeIpc({})),
                         {"base_dir": str(base), "compare_dir": str(head), "include_diff": True})
        assert "diff" in out["changed"][0]
        assert "two" in out["changed"][0]["diff"]


class TestBuildValidate:
    async def test_clean_verdict(self):
        ipc = _FakeIpc({
            "build.build": Result(id="1", status="ok", data={"errors": 0, "warnings": 0, "messages": []}),
            "library.diagnose": Result(id="2", status="ok", data={"missing": []}),
        })
        out = await _run("codesys.build.validate", _ctx(ipc), {})
        assert out["verdict"] == "clean"
        assert out["flags"] == []

    async def test_flags_device_and_library(self):
        msgs = [
            {"severity": "error", "source": "Application", "number": 77, "text": "Device not installed to the system."},
            {"severity": "error", "source": "", "number": 46, "text": "Unknown type: 'IoConfigTaskMap'"},
        ]
        ipc = _FakeIpc({
            "build.build": Result(id="1", status="ok", data={"errors": 2, "warnings": 0, "messages": msgs}),
            "library.diagnose": Result(id="2", status="ok", data={"missing": [{"name": "IoStandard", "version": "3.1.3.1"}]}),
        })
        out = await _run("codesys.build.validate", _ctx(ipc), {})
        assert out["verdict"] == "errors"
        assert out["errors"] == 2
        assert any("device_not_installed" in f for f in out["flags"])
        assert any("missing_library" in f for f in out["flags"])
        assert out["missing_libraries"] == [{"name": "IoStandard", "version": "3.1.3.1"}]
        assert out["errors_by_source"]["Application"] == 1
