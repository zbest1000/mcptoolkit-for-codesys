"""POU / DUT / GVL MCP tools."""
from __future__ import annotations

from typing import Any, Callable

from . import REGISTRY, ToolContext, ToolSpec, format_result
from .._validation import ValidationError, validate_object_path


async def _call(ctx: ToolContext, op: str, args: dict[str, Any], timeout: float = 60.0) -> str:
    return format_result(await ctx.ipc.call(op, args, timeout_s=timeout))


def _proj_arg() -> dict:
    return {"project_path": {"type": "string", "description": "Already-open project to target."}}


def _validation_error(message: str, *, field: str | None = None) -> str:
    return format_result({
        "status": "error",
        "error": message,
        "error_kind": "ValidationError",
        "data": {"field": field} if field else {},
    })


def _validate_args(
    args: dict[str, Any],
    rules: list[tuple[str, Callable[[Any], Any], bool]],
) -> str | None:
    """See mcptoolkit_for_codesys.tools.project._validate_args.

    A field is "absent" only when its value is `None` (or it's not in the
    dict). Empty strings reach the validator — they're typically invalid
    (e.g., `target=""` is not the same as "no target supplied")."""
    for field, validator, required in rules:
        val = args.get(field, None)
        if val is None:
            if required:
                return _validation_error(
                    "missing required field: {!r}".format(field), field=field
                )
            continue
        try:
            validator(val)
        except ValidationError as exc:
            return _validation_error(str(exc), field=field)
    return None


def _validated(op: str, *path_fields: str, timeout: float = 60.0):
    """Build a handler that validates the named fields as object paths then
    forwards to `op`. Fields not in `args` are skipped (so optional path
    fields like `parent` work too)."""
    rules = [(f, validate_object_path, False) for f in path_fields]

    async def _handler(ctx: ToolContext, args: dict[str, Any]) -> str:
        err = _validate_args(args, rules)
        if err is not None:
            return err
        return await _call(ctx, op, args, timeout)

    _handler.__name__ = "_handler_" + op.replace(".", "_")
    return _handler


REGISTRY.register(ToolSpec(
    name="codesys.pou.create",
    description=(
        "Create a POU (Program, Function Block, or Function) with a chosen "
        "implementation language. Optionally set declaration / implementation "
        "text in the same call. For Functions, pass `return_type` (e.g. BOOL, "
        "INT, REAL)."
    ),
    input_schema={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "parent": {
                "type": "string",
                "description": "Parent path or folder name. Default: project root.",
            },
            "pou_type": {
                "type": "string",
                "enum": ["program", "function_block", "function"],
                "default": "program",
            },
            "language": {
                "type": "string",
                "enum": ["st", "ld", "fbd", "sfc", "cfc", "il", "page_cfc", "uml"],
                "default": "st",
            },
            "return_type": {"type": "string", "description": "Required for `function`."},
            "declaration": {"type": "string", "description": "Full VAR section text."},
            "implementation": {"type": "string", "description": "Full body text (ST only)."},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.create", "parent"),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.create_dut",
    description="Create a DUT (struct, enum, union, alias).",
    input_schema={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["structure", "enumeration", "union", "alias"],
                "default": "structure",
            },
            "parent": {"type": "string"},
            "declaration": {"type": "string"},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.create_dut", "parent"),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.create_gvl",
    description="Create a Global Variable List.",
    input_schema={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "parent": {"type": "string"},
            "declaration": {"type": "string"},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.create_gvl", "parent"),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.set_text",
    description=(
        "Replace the textual declaration and/or implementation of a POU/DUT/GVL. "
        "Use `target` = name or '/'-separated path."
    ),
    input_schema={
        "type": "object",
        "required": ["target"],
        "properties": {
            "target": {"type": "string"},
            "declaration": {"type": "string"},
            "implementation": {"type": "string"},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.set_text", "target"),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.get_text",
    description="Read the declaration and implementation text of a POU/DUT/GVL.",
    input_schema={
        "type": "object",
        "required": ["target"],
        "properties": {
            "target": {"type": "string"},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.get_text", "target", timeout=30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.delete",
    description="Remove a tree object (POU, DUT, GVL, folder, ...) and its children.",
    input_schema={
        "type": "object",
        "required": ["target"],
        "properties": {
            "target": {"type": "string"},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.delete", "target", timeout=30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.rename",
    description="Rename a tree object.",
    input_schema={
        "type": "object",
        "required": ["target", "new_name"],
        "properties": {
            "target": {"type": "string"},
            "new_name": {"type": "string"},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.rename", "target", timeout=30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.find",
    description="Look up an object by name or '/'-path and return its summary.",
    input_schema={
        "type": "object",
        "required": ["target"],
        "properties": {
            "target": {"type": "string"},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.find", "target", timeout=15.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.create_folder",
    description=(
        "Create a folder under a parent (defaults to project root). Use to "
        "organize POUs/DUTs/GVLs into a tree before creating them with a "
        "`parent` argument."
    ),
    input_schema={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "parent": {
                "type": "string",
                "description": "Parent path or folder name. Default: project root.",
            },
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.create_folder", "parent", timeout=30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.create_method",
    description=(
        "Create a method on a Function Block. The parent must resolve to a "
        "Function Block (methods only make sense on FBs). `return_type` is "
        "the IEC type the method returns (e.g. 'BOOL', 'INT', 'REAL'); omit "
        "for a void method. Optionally set declaration/implementation in the "
        "same call."
    ),
    input_schema={
        "type": "object",
        "required": ["name", "parent"],
        "properties": {
            "name": {"type": "string"},
            "parent": {
                "type": "string",
                "description": "Path or name resolving to a Function Block POU.",
            },
            "return_type": {
                "type": "string",
                "description": "IEC return type, e.g. 'BOOL', 'INT'. Omit for void.",
            },
            "language": {
                "type": "string",
                "enum": ["st", "ld", "fbd", "sfc", "cfc", "il", "page_cfc", "uml"],
                "default": "st",
            },
            "declaration": {"type": "string"},
            "implementation": {"type": "string"},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.create_method", "parent", timeout=30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.create_property",
    description=(
        "Create a property on a Function Block. Returns the property container "
        "object; the Get/Set accessors are children of this object — set their "
        "bodies via `pou.set_text` targeting `<parent>/<name>/Get` and "
        "`<parent>/<name>/Set`."
    ),
    input_schema={
        "type": "object",
        "required": ["name", "parent", "return_type"],
        "properties": {
            "name": {"type": "string"},
            "parent": {
                "type": "string",
                "description": "Path or name resolving to a Function Block POU.",
            },
            "return_type": {
                "type": "string",
                "description": "IEC type the property exposes, e.g. 'INT'.",
            },
            "language": {
                "type": "string",
                "enum": ["st", "ld", "fbd", "sfc", "cfc", "il", "page_cfc", "uml"],
                "default": "st",
            },
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.create_property", "parent", timeout=30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.list_variables",
    description=(
        "Parse the target POU/GVL/DUT declaration into structured variables: "
        "each with `section` (VAR/VAR_INPUT/…), `name`, `type`, `init`, and any "
        "attached `pragma`. Read this before `pou.add_variable` / "
        "`pou.add_symbol_pragma` to see what's there. (One-declaration-per-line "
        "ST style.)"
    ),
    input_schema={
        "type": "object",
        "required": ["target"],
        "properties": {"target": {"type": "string"}, **_proj_arg()},
        "additionalProperties": False,
    },
    handler=_validated("pou.list_variables", "target", timeout=20.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.add_variable",
    description=(
        "Insert a single variable into a declaration section without rewriting "
        "the whole declaration. `name` + `type` required; optional `init`, "
        "`comment`, `section` (default `VAR`), and `pragma`. Creates the section "
        "if it doesn't exist. Prefer this over `pou.set_text` for incremental "
        "edits — it preserves the rest of the declaration."
    ),
    input_schema={
        "type": "object",
        "required": ["target", "name", "type"],
        "properties": {
            "target": {"type": "string"},
            "name": {"type": "string"},
            "type": {"type": "string", "description": "IEC type, e.g. 'INT', 'BOOL', 'FB_Pump'."},
            "init": {"type": "string", "description": "Initial value, e.g. '0', 'TRUE'."},
            "comment": {"type": "string"},
            "section": {
                "type": "string",
                "enum": ["VAR", "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT",
                         "VAR_GLOBAL", "VAR_TEMP", "VAR_STAT"],
                "default": "VAR",
            },
            "pragma": {"type": "string", "description": "Attribute pragma, e.g. \"{attribute 'symbol' := 'read'}\"."},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.add_variable", "target", timeout=20.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.pou.add_symbol_pragma",
    description=(
        "Mark a declared variable for symbol export by inserting "
        "`{attribute 'symbol' := '<access>'}` on the line above it. `access` is "
        "read / write / readwrite (default) / none. Pair with "
        "`symbol.create_config` to expose variables over OPC-UA / comms."
    ),
    input_schema={
        "type": "object",
        "required": ["target", "name"],
        "properties": {
            "target": {"type": "string"},
            "name": {"type": "string", "description": "Name of the declared variable."},
            "access": {
                "type": "string",
                "enum": ["read", "write", "readwrite", "none"],
                "default": "readwrite",
            },
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_validated("pou.add_symbol_pragma", "target", timeout=20.0),
))
