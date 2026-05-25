"""Sanity tests for the MCP tool registry: every tool has a unique name, a
well-formed JSON schema, and an async handler.

A bad ToolSpec breaks the entire MCP list_tools call, which breaks the
client's view of the server. These tests catch the kind of typo that takes
down the whole tool surface.
"""
from __future__ import annotations

import inspect

import pytest

from mcptoolkit_for_codesys.tools import REGISTRY, ToolSpec


def all_specs() -> list[ToolSpec]:
    return REGISTRY.specs()


class TestRegistryShape:
    def test_registry_is_populated(self):
        specs = all_specs()
        # We expect 30+ tools (Phase 5 count was 39 prod / 41 with dev ops).
        assert len(specs) >= 30, "registry too small — tools failed to import?"

    def test_no_duplicate_names(self):
        names = [s.name for s in all_specs()]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, "duplicate tool names: " + ", ".join(sorted(dupes))

    def test_names_are_dotted_codesys_prefix(self):
        for s in all_specs():
            assert s.name.startswith("codesys."), (
                "tool name should start with 'codesys.': " + s.name
            )

    def test_descriptions_nonempty(self):
        for s in all_specs():
            assert s.description and s.description.strip(), (
                "tool '" + s.name + "' has empty description"
            )

    def test_handlers_are_callable(self):
        for s in all_specs():
            assert callable(s.handler), (
                "tool '" + s.name + "' handler is not callable"
            )


class TestInputSchemas:
    def test_input_schema_is_object(self):
        for s in all_specs():
            schema = s.input_schema
            assert schema.get("type") == "object", (
                "tool '" + s.name + "' input_schema.type must be 'object'"
            )

    def test_properties_block_present(self):
        for s in all_specs():
            # MCP tools may have empty properties for no-arg tools, but the
            # 'properties' key should be present (consistency).
            assert "properties" in s.input_schema, (
                "tool '" + s.name + "' missing 'properties' in input_schema"
            )

    def test_required_fields_exist_in_properties(self):
        for s in all_specs():
            required = s.input_schema.get("required", [])
            properties = s.input_schema.get("properties", {})
            for req in required:
                assert req in properties, (
                    "tool '" + s.name + "' marks '" + req
                    + "' as required but it's not in properties"
                )

    def test_no_additional_properties_for_strict_tools(self):
        """Most tools should disallow extra args to catch typos. Allow opt-out
        per-tool but make it explicit."""
        opt_out = set()  # currently none
        for s in all_specs():
            if s.name in opt_out:
                continue
            ap = s.input_schema.get("additionalProperties")
            assert ap is False, (
                "tool '" + s.name + "' should set "
                "additionalProperties: false (got " + repr(ap) + ")"
            )

    def test_to_mcp_tool_renders(self):
        """to_mcp_tool() shouldn't raise — the Tool model is what MCP serves."""
        for s in all_specs():
            tool = s.to_mcp_tool()
            assert tool.name == s.name
            assert tool.description == s.description


class TestExpectedCoreTools:
    """Sanity: known tools we ship should be present. Catches accidental
    deregistration."""

    @pytest.mark.parametrize("name", [
        "codesys.ping",
        "codesys.info",
        "codesys.health",
        "codesys.diagnose",
        "codesys.project.open",
        "codesys.project.create",
        "codesys.project.create_standard",
        "codesys.project.save",
        "codesys.project.close",
        "codesys.project.tree",
        "codesys.pou.create",
        "codesys.pou.create_dut",
        "codesys.pou.create_gvl",
        "codesys.pou.create_folder",
        "codesys.pou.create_method",
        "codesys.pou.create_property",
        "codesys.pou.list_variables",
        "codesys.pou.add_variable",
        "codesys.pou.add_symbol_pragma",
        "codesys.pou.set_text",
        "codesys.pou.get_text",
        "codesys.build.build",
        "codesys.build.rebuild",
        "codesys.build.validate",
        "codesys.project.diff",
        "codesys.build.messages",
        "codesys.build.force_recompile",
        "codesys.online.login",
        "codesys.online.set_credentials",
        "codesys.online.logout",
        "codesys.online.read",
        "codesys.online.write",
        "codesys.online.snapshot",
        "codesys.online.forced",
        "codesys.library.list_installed",
        "codesys.library.list_project",
        "codesys.library.add",
        "codesys.library.remove",
        "codesys.library.update",
        "codesys.project.set_info",
        "codesys.project.mirror_export",
        "codesys.project.import_xml",
        "codesys.project.export_xml",
        "codesys.device.list_installed",
        "codesys.device.categories",
        "codesys.device.tree",
        "codesys.device.add",
        "codesys.device.update",
        "codesys.device.parameters",
        "codesys.device.set_parameter",
        "codesys.symbol.create_config",
        "codesys.symbol.list",
        "codesys.task.list",
        "codesys.task.set",
        "codesys.task.create",
        "codesys.library.diagnose",
        "codesys.library.find_on_disk",
        "codesys.library.install_missing",
        "codesys.library.create_repository",
        "codesys.library.repositories",
        "codesys.library.install",
        "codesys.library.resolve_missing",
        "codesys.system.list_installations",
        "codesys.system.install_package",
    ])
    def test_tool_registered(self, name):
        assert REGISTRY.get(name) is not None, (
            "expected tool '" + name + "' to be registered"
        )


class TestHandlerSignature:
    """Tool handlers are `async def fn(ctx, args) -> str` (or a lambda that
    returns the awaitable from `_call`). Both shapes should be detectable."""

    def test_handler_callable_with_two_args(self):
        for s in all_specs():
            # Inspect parameters — either a real async function or a lambda.
            sig = inspect.signature(s.handler)
            params = list(sig.parameters.values())
            # Allow self for bound methods, vararg for lambdas.
            non_self = [
                p for p in params
                if p.name not in ("self",) and p.kind != inspect.Parameter.VAR_POSITIONAL
            ]
            assert len(non_self) >= 2, (
                "tool '" + s.name + "' handler should accept (ctx, args); got "
                + str([p.name for p in params])
            )
