"""Verify that tool handlers run host-side validation BEFORE reaching the
IPC layer. Bad args should produce a structured `ValidationError` response
without spending a watcher round-trip on it.

Strategy: build a real `ToolContext` with a fake `IpcClient` that fails
loudly if called. If validation works, the test passes without the IPC
client ever being touched. If validation is missing, the fake IPC raises
and the test fails with a clear message.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from mcptoolkit_for_codesys.tools import REGISTRY, ToolContext


class _FailingIpc:
    """Mock IpcClient that raises if .call() is invoked. Used to assert
    validation rejected the args before the IPC layer."""

    def __init__(self):
        self.workdir = Path("/fake/workdir")
        self.call_count = 0

    async def call(self, op, args, timeout_s=60.0):
        self.call_count += 1
        raise AssertionError(
            "IPC should not have been called for invalid args; got op={!r}, args={!r}".format(op, args)
        )


def _make_ctx() -> ToolContext:
    """ToolContext with a failing IPC and stub install/manager."""
    install = MagicMock()
    install.install_dir = Path("C:/fake/CODESYS")
    install.key = "fake"
    install.version = "fake"
    install.sp = 22
    install.patch = 0
    install.exe = Path("C:/fake/CODESYS.exe")
    install.profile_name = lambda: "fake"
    manager = MagicMock()
    return ToolContext(ipc=_FailingIpc(), install=install, manager=manager)


def _decode(result_str: str) -> dict:
    return json.loads(result_str)


@pytest.mark.asyncio
class TestProjectOpenValidation:
    async def test_missing_path_field(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.project.open").handler(ctx, {})
        decoded = _decode(result)
        assert decoded["status"] == "error"
        assert decoded["error_kind"] == "ValidationError"
        assert "path" in decoded["error"]

    async def test_relative_path(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.project.open").handler(ctx, {"path": "foo.project"})
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"
        assert "absolute" in decoded["error"]

    async def test_wrong_extension(self, tmp_path: Path):
        ctx = _make_ctx()
        bad = tmp_path / "x.txt"
        bad.write_text("")
        result = await REGISTRY.get("codesys.project.open").handler(ctx, {"path": str(bad)})
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"

    async def test_nonexistent_file(self, tmp_path: Path):
        ctx = _make_ctx()
        path = tmp_path / "ghost.project"
        result = await REGISTRY.get("codesys.project.open").handler(ctx, {"path": str(path)})
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"
        assert "does not exist" in decoded["error"]


@pytest.mark.asyncio
class TestProjectCreateValidation:
    async def test_relative_path(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.project.create").handler(ctx, {"path": "rel.project"})
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"

    async def test_missing_parent_dir(self, tmp_path: Path):
        ctx = _make_ctx()
        deep = tmp_path / "no" / "such" / "dir" / "x.project"
        result = await REGISTRY.get("codesys.project.create").handler(ctx, {"path": str(deep)})
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"
        assert "parent" in decoded["error"]

    async def test_valid_path_reaches_ipc(self, tmp_path: Path):
        """When path is valid the handler should attempt IPC. Our failing
        IPC turns that into an AssertionError — confirming validation
        passed and got out of the way."""
        ctx = _make_ctx()
        target = tmp_path / "real.project"
        with pytest.raises(AssertionError, match="IPC should not have been called"):
            await REGISTRY.get("codesys.project.create").handler(ctx, {"path": str(target)})


@pytest.mark.asyncio
class TestProjectCreateStandardValidation:
    async def test_template_path_traversal_rejected(self, tmp_path: Path):
        ctx = _make_ctx()
        target = tmp_path / "x.project"
        result = await REGISTRY.get("codesys.project.create_standard").handler(
            ctx,
            {"path": str(target), "template": "../etc/passwd"},
        )
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"
        assert "template" in decoded["error"].lower() or decoded["data"].get("field") == "template"


@pytest.mark.asyncio
class TestPouTargetValidation:
    async def test_set_text_dotdot_rejected(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.pou.set_text").handler(
            ctx,
            {"target": "A/../B", "implementation": "x := 1;"},
        )
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"

    async def test_get_text_empty_target_rejected(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.pou.get_text").handler(ctx, {"target": ""})
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"

    async def test_set_text_null_byte_rejected(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.pou.set_text").handler(
            ctx,
            {"target": "PLC_PRG\x00", "implementation": "x;"},
        )
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"

    async def test_valid_target_reaches_ipc(self):
        ctx = _make_ctx()
        with pytest.raises(AssertionError, match="IPC should not have been called"):
            await REGISTRY.get("codesys.pou.set_text").handler(
                ctx,
                {"target": "PLC_PRG", "implementation": "x := 1;"},
            )


@pytest.mark.asyncio
class TestImportXmlValidation:
    async def test_missing_path(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.project.import_xml").handler(ctx, {})
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"

    async def test_nonexistent_file(self, tmp_path: Path):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.project.import_xml").handler(
            ctx, {"path": str(tmp_path / "missing.xml")}
        )
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"
        assert "does not exist" in decoded["error"]

    async def test_wrong_extension(self, tmp_path: Path):
        ctx = _make_ctx()
        not_xml = tmp_path / "x.txt"
        not_xml.write_text("")
        result = await REGISTRY.get("codesys.project.import_xml").handler(
            ctx, {"path": str(not_xml)}
        )
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"

    async def test_parent_path_dotdot_rejected(self, tmp_path: Path):
        ctx = _make_ctx()
        good = tmp_path / "ok.xml"
        good.write_text("<xml/>")
        result = await REGISTRY.get("codesys.project.import_xml").handler(
            ctx, {"path": str(good), "parent": "A/../B"}
        )
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"


@pytest.mark.asyncio
class TestMirrorExportValidation:
    async def test_missing_out_dir(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.project.mirror_export").handler(ctx, {})
        decoded = _decode(result)
        assert decoded["status"] == "error"
        assert decoded["error_kind"] == "ValidationError"
        assert decoded["data"]["field"] == "out_dir"

    async def test_relative_out_dir_rejected(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.project.mirror_export").handler(
            ctx, {"out_dir": "mirror"}
        )
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"
        assert "absolute" in decoded["error"]

    async def test_null_byte_rejected(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.project.mirror_export").handler(
            ctx, {"out_dir": "C:\\mirror\x00"}
        )
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"
        assert "null byte" in decoded["error"]

    async def test_valid_out_dir_reaches_ipc(self, tmp_path: Path):
        ctx = _make_ctx()
        with pytest.raises(AssertionError, match="IPC should not have been called"):
            await REGISTRY.get("codesys.project.mirror_export").handler(
                ctx, {"out_dir": str(tmp_path / "mirror")}
            )


@pytest.mark.asyncio
class TestPouCreateValidation:
    """`parent` is optional but if supplied must be a valid object path."""

    async def test_dotdot_parent_rejected(self):
        ctx = _make_ctx()
        result = await REGISTRY.get("codesys.pou.create").handler(
            ctx,
            {"name": "P", "parent": "POUs/../etc"},
        )
        decoded = _decode(result)
        assert decoded["error_kind"] == "ValidationError"

    async def test_no_parent_passes_through(self):
        """Omitting parent should not trigger validation — IPC is called."""
        ctx = _make_ctx()
        with pytest.raises(AssertionError, match="IPC should not have been called"):
            await REGISTRY.get("codesys.pou.create").handler(ctx, {"name": "P"})
