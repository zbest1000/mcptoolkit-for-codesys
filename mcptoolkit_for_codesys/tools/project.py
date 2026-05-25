"""
Project lifecycle MCP tools. Thin shells around the watcher ops, with
host-side input validation in front (so bad paths fail fast with a
useful error rather than crossing the IPC boundary).
"""
from __future__ import annotations

from typing import Any, Callable

from . import REGISTRY, ToolContext, ToolSpec, format_result
from .._validation import (
    ValidationError,
    validate_object_path,
    validate_project_path,
    validate_template_name,
)


def _proj_arg() -> dict:
    return {
        "project_path": {
            "type": "string",
            "description": (
                "Path of an already-open project to target. Omit to use the "
                "active (primary) project."
            ),
        }
    }


async def _call(ctx: ToolContext, op: str, args: dict[str, Any], timeout: float = 60.0) -> str:
    result = await ctx.ipc.call(op, args, timeout_s=timeout)
    return format_result(result)


def _validation_error(message: str, *, field: str | None = None) -> str:
    """Render a validation error in the same shape watcher errors take, so
    the LLM client sees a consistent format whether the error is host- or
    watcher-side."""
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
    """Apply a list of (field, validator, required) rules to args.

    Returns None if all pass, or a rendered error response string on the
    first failure. Validators receive the raw arg value and either return
    the (possibly-normalized) value or raise ValidationError.

    A field is "absent" only when its value is `None` (or it's not in the
    dict). Empty strings reach the validator — they're typically invalid
    (e.g., `path=""` is not the same as "no path supplied")."""
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


# ---------------------------------------------------------------------------

async def _open_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    err = _validate_args(args, [
        ("path", lambda p: validate_project_path(p, must_exist=True), True),
    ])
    if err is not None:
        return err
    return await _call(ctx, "project.open", args, timeout=120.0)


REGISTRY.register(ToolSpec(
    name="codesys.project.open",
    description=(
        "Open a CODESYS .project file. Returns project metadata. The opened "
        "project becomes the primary project unless one was already primary. "
        "If the project references libraries the local install doesn't have, "
        "the response includes a `library_diagnostics` block listing the "
        "missing names (parsed from the IDE's Library Manager error messages). "
        "Use `diagnose_libraries=false` to suppress that side-channel."
    ),
    input_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the .project file."},
            "update_storage_format": {
                "type": "boolean",
                "description": "Migrate older storage format on open. Default false.",
                "default": False,
            },
            "password": {
                "type": "string",
                "description": "Project password if the file is encrypted.",
            },
            "diagnose_libraries": {
                "type": "boolean",
                "default": True,
                "description": (
                    "When true (default), surface a `library_diagnostics` "
                    "block in the response IF the project has unresolved "
                    "library references. Set false to skip that scan."
                ),
            },
        },
        "additionalProperties": False,
    },
    handler=_open_handler,
))


async def _create_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    err = _validate_args(args, [
        ("path", validate_project_path, True),
    ])
    if err is not None:
        return err
    return await _call(ctx, "project.create", args, timeout=60.0)


REGISTRY.register(ToolSpec(
    name="codesys.project.create",
    description="Create a new empty .project at the given path.",
    input_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "overwrite": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    handler=_create_handler,
))


async def _create_standard_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Validates path + template name, auto-injects `templates_dir` from
    the host's install metadata so the watcher doesn't have to derive it
    (CODESYS's `system.executable_filename` only returns the basename)."""
    err = _validate_args(args, [
        ("path", validate_project_path, True),
        ("template", validate_template_name, False),
    ])
    if err is not None:
        return err
    args = dict(args or {})
    templates_dir = str(ctx.install.install_dir / "Templates")
    args.setdefault("templates_dir", templates_dir)
    return await _call(ctx, "project.create_standard", args, timeout=120.0)


REGISTRY.register(ToolSpec(
    name="codesys.project.create_standard",
    description=(
        "Create a new project from the CODESYS Standard.project template "
        "(Device + Application + MainTask + PLC_PRG). This is what the IDE's "
        "New Project wizard produces; use this instead of `project.create` "
        "when you want a project that can actually compile. Optionally specify "
        "`template=\"Empty\"` for the bare template, or any other .project "
        "filename present in `<install>/CODESYS/Templates/`."
    ),
    input_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {
                "type": "string",
                "description": "Destination .project path.",
            },
            "overwrite": {
                "type": "boolean",
                "default": False,
                "description": "Replace any existing file at `path`.",
            },
            "template": {
                "type": "string",
                "default": "Standard",
                "description": (
                    "Template basename (without .project extension). "
                    "Defaults to 'Standard'. Other valid values depend on what "
                    "ships with the CODESYS install — typically 'Empty' is "
                    "also available."
                ),
            },
        },
        "additionalProperties": False,
    },
    handler=_create_standard_handler,
))


REGISTRY.register(ToolSpec(
    name="codesys.project.save",
    description="Save the targeted project (or the primary project).",
    input_schema={
        "type": "object",
        "properties": _proj_arg(),
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "project.save", args, timeout=60.0),
))


async def _save_as_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    err = _validate_args(args, [
        ("new_path", validate_project_path, True),
    ])
    if err is not None:
        return err
    return await _call(ctx, "project.save_as", args, timeout=120.0)


REGISTRY.register(ToolSpec(
    name="codesys.project.save_as",
    description="Save the targeted project to a new path.",
    input_schema={
        "type": "object",
        "required": ["new_path"],
        "properties": {
            "new_path": {"type": "string"},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_save_as_handler,
))


async def _save_archive_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    err = _validate_args(args, [
        ("path", lambda p: validate_project_path(p, extension=".projectarchive"), True),
    ])
    if err is not None:
        return err
    return await _call(ctx, "project.save_archive", args, timeout=180.0)


REGISTRY.register(ToolSpec(
    name="codesys.project.save_archive",
    description="Save a .projectarchive (bundles libraries / device descriptions / compile info).",
    input_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {
                "type": "string",
                "description": "Output path for the .projectarchive.",
            },
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_save_archive_handler,
))


REGISTRY.register(ToolSpec(
    name="codesys.project.close",
    description="Close the targeted project (or the primary project).",
    input_schema={
        "type": "object",
        "properties": _proj_arg(),
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "project.close", args, timeout=30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.project.list_open",
    description="List all currently-open projects in the IDE.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "project.list_open", args, timeout=10.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.project.info",
    description="Return metadata (title, author, version, ...) for the targeted project.",
    input_schema={
        "type": "object",
        "properties": _proj_arg(),
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "project.info", args, timeout=10.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.project.tree",
    description=(
        "Walk the project tree and return objects up to `max_depth`. Use this "
        "to discover names/paths before calling `pou.set_text` etc."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "max_depth": {"type": "integer", "minimum": 1, "default": 3},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "project.tree", args, timeout=30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.project.set_info",
    description=(
        "Update Project Information fields (title, version, author, company, "
        "description). Only the fields you supply are updated — others are "
        "preserved. Useful for CI bump-version workflows."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "version": {
                "type": "string",
                "description": "Free-form version string, e.g. '1.2.3'.",
            },
            "author": {"type": "string"},
            "company": {"type": "string"},
            "description": {"type": "string"},
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "project.set_info", args, timeout=15.0),
))


async def _mirror_export_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Validates the out_dir path is absolute + safe before forwarding."""
    out_dir = args.get("out_dir")
    if not out_dir:
        return _validation_error("missing required field: 'out_dir'", field="out_dir")
    if not isinstance(out_dir, str):
        return _validation_error("'out_dir' must be a string", field="out_dir")
    if "\x00" in out_dir:
        return _validation_error("'out_dir' contains null byte", field="out_dir")
    from pathlib import Path as _P
    p = _P(out_dir)
    if not p.is_absolute():
        return _validation_error(
            "'out_dir' must be absolute (got {!r})".format(out_dir), field="out_dir"
        )
    return await _call(ctx, "project.mirror_export", args, timeout=120.0)


async def _export_xml_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Validates the output path; objects/recursive pass through."""
    path_str = args.get("path")
    if not path_str or not isinstance(path_str, str):
        return _validation_error("'path' is required and must be a string", field="path")
    if "\x00" in path_str:
        return _validation_error("'path' contains null byte", field="path")
    from pathlib import Path as _P
    p = _P(path_str)
    if not p.is_absolute():
        return _validation_error("'path' must be absolute", field="path")
    if not p.parent.exists():
        return _validation_error(
            "parent directory does not exist: {}".format(p.parent), field="path"
        )
    return await _call(ctx, "project.export_xml", args, timeout=120.0)


REGISTRY.register(ToolSpec(
    name="codesys.project.export_xml",
    description=(
        "Export tree objects to a PLCopenXML file. The complement to "
        "`project.import_xml` — useful for building snippet libraries "
        "of device templates, application skeletons, library reference "
        "sets. Export from a working project once, save the .xml next "
        "to your CI, then re-import into target projects. Default "
        "exports the whole project; pass `objects` (list of names or "
        "paths) to scope."
    ),
    input_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute output .xml path.",
            },
            "objects": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of tree-object names/paths to export. Empty or "
                    "omitted = whole project."
                ),
            },
            "recursive": {
                "type": "boolean",
                "default": True,
                "description": "Include children of selected objects.",
            },
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_export_xml_handler,
))


async def _import_xml_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Validates path (must exist + .xml extension) and optional parent path."""
    err = _validate_args(args, [
        ("path", lambda p: validate_project_path(p, must_exist=True, extension=".xml"), True),
        ("parent", validate_object_path, False),
    ])
    if err is not None:
        return err
    return await _call(ctx, "project.import_xml", args, timeout=120.0)


REGISTRY.register(ToolSpec(
    name="codesys.project.import_xml",
    description=(
        "Import objects from a PLCopenXML file. The canonical way to add "
        "devices, POU trees, GVLs, or whole subsystems programmatically "
        "without invoking interactive IDE wizards. Most production CODESYS "
        "workflows keep a small library of pre-baked PLCopenXML snippets "
        "and feed them through here. Pass `parent` to scope the import to "
        "a specific tree node (e.g. 'Application'); omit for project root."
    ),
    input_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the .xml file to import.",
            },
            "parent": {
                "type": "string",
                "description": (
                    "Optional tree path of the object to import INTO. "
                    "Default: project root."
                ),
            },
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_import_xml_handler,
))


REGISTRY.register(ToolSpec(
    name="codesys.project.mirror_export",
    description=(
        "Walk the project tree and write one source file per code-bearing "
        "object to a filesystem mirror. Output is git-friendly: each POU/DUT/"
        "GVL becomes a `<name>.st` file containing the declaration and "
        "implementation joined by `(* === DECLARATION === *)` / "
        "`(* === IMPLEMENTATION === *)` separators. Function-Block children "
        "(methods, properties) are written under a `<FB>/` subdirectory with "
        "the FB's own body in `__self__.st`. Use to feed CI/code-review "
        "tooling that operates on text. Pass `clean=true` to wipe the output "
        "dir first; `dryrun=true` to see the file list without writing."
    ),
    input_schema={
        "type": "object",
        "required": ["out_dir"],
        "properties": {
            "out_dir": {
                "type": "string",
                "description": "Absolute output directory. Created if missing.",
            },
            "clean": {
                "type": "boolean",
                "default": False,
                "description": "If true, wipe out_dir before writing.",
            },
            "dryrun": {
                "type": "boolean",
                "default": False,
                "description": "If true, returns the file list without writing.",
            },
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=_mirror_export_handler,
))


# ---------------------------------------------------------------------------
# Host-side structured diff of two mirror_export snapshots. Pure filesystem +
# difflib — no CODESYS involvement, so it's instant and never touches the IDE.
# ---------------------------------------------------------------------------
import os as _os
import difflib as _difflib
from pathlib import Path as _Path


def _collect_st(root: _Path) -> dict[str, _Path]:
    out: dict[str, _Path] = {}
    if not root.exists():
        return out
    for p in root.rglob("*.st"):
        out[p.relative_to(root).as_posix()] = p
    return out


def _line_delta(a_text: str, b_text: str) -> tuple[int, int]:
    added = removed = 0
    for ln in _difflib.unified_diff(a_text.splitlines(), b_text.splitlines(), n=0):
        if ln.startswith("+") and not ln.startswith("+++"):
            added += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            removed += 1
    return added, removed


async def _diff_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    base = _Path(args.get("base_dir", ""))
    head = _Path(args.get("compare_dir", ""))
    if not args.get("base_dir") or not args.get("compare_dir"):
        return _validation_error("base_dir and compare_dir are required")
    if not base.exists():
        return _validation_error(f"base_dir does not exist: {base}", field="base_dir")
    if not head.exists():
        return _validation_error(f"compare_dir does not exist: {head}", field="compare_dir")
    include_diff = bool(args.get("include_diff", False))

    a = _collect_st(base)
    b = _collect_st(head)
    a_keys, b_keys = set(a), set(b)
    added = sorted(b_keys - a_keys)
    removed = sorted(a_keys - b_keys)
    changed = []
    for k in sorted(a_keys & b_keys):
        at = a[k].read_text(encoding="utf-8", errors="replace")
        bt = b[k].read_text(encoding="utf-8", errors="replace")
        if at != bt:
            la, lr = _line_delta(at, bt)
            entry = {"path": k, "added_lines": la, "removed_lines": lr}
            if include_diff:
                entry["diff"] = "".join(_difflib.unified_diff(
                    at.splitlines(keepends=True), bt.splitlines(keepends=True),
                    fromfile="base/" + k, tofile="head/" + k,
                ))[:4000]
            changed.append(entry)
    return format_result({
        "status": "ok",
        "base_dir": str(base),
        "compare_dir": str(head),
        "summary": {
            "added": len(added), "removed": len(removed),
            "changed": len(changed), "unchanged": len(a_keys & b_keys) - len(changed),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    })


REGISTRY.register(ToolSpec(
    name="codesys.project.diff",
    description=(
        "Diff two `mirror_export` snapshots (directories of `.st` files) into a "
        "structured change report: added / removed / changed objects with "
        "per-file line +/- counts. Pure host-side (no CODESYS), so it's instant. "
        "Workflow: `mirror_export` a baseline, make changes, `mirror_export` "
        "again, then `diff` the two dirs to see exactly what changed. Pass "
        "`include_diff=true` for unified-diff text per changed file."
    ),
    input_schema={
        "type": "object",
        "required": ["base_dir", "compare_dir"],
        "properties": {
            "base_dir": {"type": "string", "description": "Baseline mirror dir."},
            "compare_dir": {"type": "string", "description": "Newer mirror dir to compare."},
            "include_diff": {
                "type": "boolean",
                "default": False,
                "description": "Include unified-diff text (capped) per changed file.",
            },
        },
        "additionalProperties": False,
    },
    handler=_diff_handler,
))
