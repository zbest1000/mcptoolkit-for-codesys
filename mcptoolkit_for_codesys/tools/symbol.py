"""Symbol Configuration MCP tools.

SP22 scripting only lets you create the configuration object and toggle its
top-level flags (comments export, OPC-UA support). Which variables are exported
is controlled by the `{attribute 'symbol' := ...}` pragma in declarations, not
by per-symbol scripting — so these tools cover create + list, and the pragma is
written through `pou.set_text`.
"""
from __future__ import annotations

from typing import Any

from . import REGISTRY, ToolContext, ToolSpec, format_result


async def _call(ctx: ToolContext, op: str, args: dict[str, Any], timeout: float = 60.0) -> str:
    return format_result(await ctx.ipc.call(op, args, timeout_s=timeout))


def _common() -> dict:
    return {
        "project_path": {"type": "string"},
        "application": {"type": "string", "description": "Name of the Application object."},
    }


REGISTRY.register(ToolSpec(
    name="codesys.symbol.create_config",
    description=(
        "Add a Symbol Configuration under the application (the symbol set "
        "exposed for OPC-UA / external comms). `export_comments` and "
        "`support_opc_ua` default true. Idempotent: if one already exists it's "
        "returned (pass `force: true` to add another).\n"
        "\n"
        "Selecting WHICH variables are exported is done with the declaration "
        "pragma `{attribute 'symbol' := 'readwrite'}` (or 'read'/'write'/'none') "
        "— write it via `pou.set_text`; the configuration collects the marked "
        "symbols at build time. SP22 has no per-symbol scripting API."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "export_comments": {"type": "boolean", "default": True},
            "support_opc_ua": {"type": "boolean", "default": True},
            "force": {
                "type": "boolean",
                "default": False,
                "description": "Add a new config even if one already exists.",
            },
            **_common(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "symbol.create_config", args, 60.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.symbol.list",
    description=(
        "List the symbol configurations under the application, with their "
        "names and GUIDs. Use to check whether one exists before "
        "`symbol.create_config`."
    ),
    input_schema={
        "type": "object",
        "properties": _common(),
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "symbol.list", args, 30.0),
))
