"""Library Manager MCP tools.

System-wide library inventory (`list_installed`) and project-scoped
reference management (`list_project` / `add` / `remove`).
"""
from __future__ import annotations

from typing import Any

from . import REGISTRY, ToolContext, ToolSpec, format_result


async def _call(ctx: ToolContext, op: str, args: dict[str, Any], timeout: float) -> str:
    return format_result(await ctx.ipc.call(op, args, timeout_s=timeout))


def _proj_arg() -> dict:
    return {
        "project_path": {
            "type": "string",
            "description": "Already-open project to target.",
        }
    }


REGISTRY.register(ToolSpec(
    name="codesys.library.list_installed",
    description=(
        "Enumerate libraries installed in the system-wide CODESYS library "
        "repositories. These are libraries the IDE knows about and can "
        "add to projects. Filter with `pattern` (case-insensitive substring "
        "match against display_name / name / namespace). Caps at 200 by "
        "default — set `limit` higher for a full dump."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Case-insensitive substring filter.",
            },
            "limit": {
                "type": "integer",
                "default": 200,
                "minimum": 1,
                "description": "Cap on returned entries.",
            },
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "library.list_installed", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.library.list_project",
    description=(
        "List library references in the current project. Returns each "
        "reference's name, namespace, version, placeholder status, and "
        "resolution. The result also includes the Library Manager "
        "object's GUID for follow-up calls."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "Already-open project to target. Omit for primary.",
            },
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "library.list_project", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.library.add",
    description=(
        "Add a library reference to the project. By default the library "
        "must already exist in the system repositories — pass "
        "`allow_unresolved=true` to add it as a placeholder anyway "
        "(useful when you know the library will be installed later, "
        "but riskier because the project compile will fail until then)."
    ),
    input_schema={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Library namespace/name as it appears in the IDE's "
                    "Library Manager (e.g. 'Standard', 'Util', "
                    "'CAA Memory Block Manager')."
                ),
            },
            "version": {
                "type": "string",
                "description": (
                    "Optional version pin (e.g. '3.5.17.0'). Omit for latest."
                ),
            },
            "allow_unresolved": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Add the reference even if the library isn't currently "
                    "installed. Compile will fail until the library is "
                    "installed and the placeholder is resolved."
                ),
            },
            "project_path": {
                "type": "string",
                "description": "Already-open project to target.",
            },
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "library.add", args, 60.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.library.remove",
    description=(
        "Remove a library reference from the project. Idempotent in spirit — "
        "raises if the reference isn't present, otherwise removes."
    ),
    input_schema={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Library name/namespace to remove.",
            },
            "project_path": {
                "type": "string",
                "description": "Already-open project to target.",
            },
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "library.remove", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.library.update",
    description=(
        "Bump a project library reference to the latest installed version by "
        "removing it and re-adding by namespace (CODESYS resolves to the "
        "highest installed). Pass `to` to pin a specific version string "
        "instead (e.g. \"Util, 3.5.17.0 (System)\"). Returns before/after "
        "reference names."
    ),
    input_schema={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Library name/namespace currently referenced.",
            },
            "to": {
                "type": "string",
                "description": "Optional exact version string to pin instead of latest.",
            },
            "project_path": {"type": "string", "description": "Already-open project to target."},
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "library.update", args, 30.0),
))


import json as _json
import os as _os
import re as _re
from pathlib import Path as _Path


# Standard locations CODESYS keeps libraries in. Probed and confirmed on
# SP22; the managed-libraries dir is where ~840 files live by default.
_LIB_SEARCH_DEFAULTS = [
    r"C:\ProgramData\CODESYS\Managed Libraries",
    r"C:\Program Files\CODESYS 3.5.22.10\CODESYS\Library",
]
_LIB_FILE_EXTS = (
    ".library",
    ".compiled-library",
    ".compiled-library-v3",
)


def _parse_library_filename(path: _Path) -> dict:
    """Extract {vendor, name, version} from the standard layout
    `<repo>/<Vendor>/<Name>/<Version>/<Name>.<ext>`. Falls back to just
    the filename stem when the layout doesn't match."""
    parts = list(path.parts)
    out = {"vendor": "", "name": "", "version": "", "path": str(path)}
    # Walk backwards from the file: file, version, name, vendor
    if len(parts) >= 4:
        out["version"] = parts[-2]
        out["name"] = parts[-3]
        out["vendor"] = parts[-4]
    # Sanitize: name might come with extension if filename mode failed
    stem = path.stem
    if not out["name"]:
        out["name"] = stem
    return out


def _scan_dir_for_libraries(root: _Path, max_depth: int = 6) -> list[dict]:
    """Walk `root` looking for .library / .compiled-library files. Returns
    a list of parsed entries. Capped at depth 6 to bound runtime."""
    found: list[dict] = []
    if not root.exists():
        return found
    try:
        root_str = str(root)
        for dirpath, _dirs, files in _os.walk(root_str):
            rel_depth = _os.path.relpath(dirpath, root_str).count(_os.sep)
            if rel_depth > max_depth:
                continue
            for fn in files:
                lower = fn.lower()
                if not any(lower.endswith(ext) for ext in _LIB_FILE_EXTS):
                    continue
                p = _Path(dirpath) / fn
                found.append(_parse_library_filename(p))
    except OSError:
        pass
    return found


def _normalize(name: str) -> str:
    """Library names sometimes ship with underscores instead of spaces, or
    have different case. Normalize for matching."""
    return _re.sub(r"[\s_-]+", "", (name or "").lower())


async def _find_on_disk_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Pure host-side: scan the filesystem for .library files matching a
    name pattern. No CODESYS involvement."""
    name = (args.get("name") or "").strip()
    version = (args.get("version") or "").strip()
    search_paths = args.get("search_paths") or _LIB_SEARCH_DEFAULTS
    include_install_dir = bool(args.get("include_install_dir", True))

    # Optionally include the actively-driven install's Library dir
    extra: list[str] = []
    if include_install_dir:
        candidate = ctx.install.install_dir.parent / "Library"
        if candidate.exists():
            extra.append(str(candidate))

    seen_paths = set()
    matches: list[dict] = []
    for root in list(search_paths) + extra:
        root_path = _Path(root)
        for entry in _scan_dir_for_libraries(root_path):
            if entry["path"] in seen_paths:
                continue
            seen_paths.add(entry["path"])
            if name:
                if _normalize(name) not in _normalize(entry["name"]):
                    continue
            if version:
                if entry["version"] != version:
                    continue
            matches.append(entry)

    # Group by name + version
    by_name: dict[str, dict] = {}
    for m in matches:
        key = m["name"]
        by_name.setdefault(key, {"name": key, "versions": []})
        if m["version"] not in by_name[key]["versions"]:
            by_name[key]["versions"].append(m["version"])

    return format_result({
        "status": "ok",
        "search_paths": list(search_paths) + extra,
        "match_count": len(matches),
        "matches": matches[:200],
        "by_name": list(by_name.values()),
    })


REGISTRY.register(ToolSpec(
    name="codesys.library.find_on_disk",
    description=(
        "Scan known library locations (default: "
        "`C:\\ProgramData\\CODESYS\\Managed Libraries` and the driven "
        "install's `Library` subdir) for `.library` / `.compiled-library` "
        "files matching `name` (substring, case-insensitive, ignores "
        "whitespace/underscores) and/or exact `version`. Pure host-side "
        "filesystem walk — no CODESYS involvement. Use to confirm a "
        "specific library file exists before invoking `library.install`, "
        "or to discover what versions of a library are available."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Substring filter on library name (normalized).",
            },
            "version": {
                "type": "string",
                "description": "Exact version match (e.g. '3.5.22.0').",
            },
            "search_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Override the default search roots. Each path is "
                    "walked recursively to depth 6."
                ),
            },
            "include_install_dir": {
                "type": "boolean",
                "default": True,
                "description": "Also scan `<install>/Library` if it exists.",
            },
        },
        "additionalProperties": False,
    },
    handler=_find_on_disk_handler,
))


def _parse_version_tuple(v: str) -> tuple[int, ...]:
    """Parse '3.5.22.0' → (3, 5, 22, 0) for sortable comparison.
    Non-numeric segments collapse to 0 so we never crash on odd strings."""
    out = []
    for part in (v or "").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


async def _install_missing_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Orchestrator: diagnose → search disk → install / fix version pin.

    For each missing library reported by `library.diagnose`:
      1. Scan the filesystem for matching name (and optionally exact version).
      2. If an exact-version match is found AND it's not already in a
         managed location, call `library.install` to register it.
      3. If only different versions are on disk (the "version pin" case),
         AND `auto_fix_version=True` (default), remove the broken pinned
         reference and re-add at the highest available installed version.
         The project moves from "broken" → "compilable" in one round-trip.
      4. If no matches found, report 'not_found' with searched paths.

    Re-runs `library.diagnose` at the end to confirm what's now resolved.
    """
    # Step 1: diagnose current state
    diag_result = await ctx.ipc.call("library.diagnose", {}, timeout_s=30.0)
    if diag_result.status != "ok":
        return format_result({
            "status": "error",
            "error": "library.diagnose failed: " + (diag_result.error or "unknown"),
        })
    missing = (diag_result.data or {}).get("missing", [])
    if not missing:
        return format_result({
            "status": "ok",
            "outcome": "nothing_missing",
            "diagnostics": diag_result.data,
        })

    # Step 2: for each missing, scan disk
    search_paths = args.get("search_paths") or _LIB_SEARCH_DEFAULTS
    auto_install = bool(args.get("auto_install", True))
    auto_fix_version = bool(args.get("auto_fix_version", True))
    strict_version = bool(args.get("strict_version", False))
    save_after_fix = bool(args.get("save_after_fix", True))

    actions: list[dict] = []
    fixes_applied = 0
    for entry in missing:
        name = entry.get("name", "")
        version = entry.get("version", "")
        raw = entry.get("raw", "")  # e.g. "IoStandard, 3.1.3.1 (System)"
        find_args = {"name": name, "search_paths": search_paths}
        if strict_version and version and version != "*":
            find_args["version"] = version
        find_result = _json.loads(
            await _find_on_disk_handler(ctx, find_args)
        )
        matches = find_result.get("matches", [])
        action: dict[str, Any] = {
            "missing_entry": entry,
            "matches_on_disk": len(matches),
            "search_paths": find_result.get("search_paths", []),
        }
        if not matches:
            action["outcome"] = "not_found"
            actions.append(action)
            continue

        # Prefer exact version match if available
        exact = [m for m in matches if m["version"] == version] if version else []
        candidate = exact[0] if exact else matches[0]
        action["selected_file"] = candidate
        action["exact_version_match"] = bool(exact)

        # If the file already lives in a known managed-libraries tree, the
        # library is already "installed" from CODESYS's perspective — the
        # real issue is the project's version pin pointing at a version
        # that's no longer on disk. The pragmatic fix is to drop the pinned
        # reference and re-add the library by name (CODESYS resolves the
        # name to the highest installed version automatically).
        is_already_managed = (
            "Managed Libraries" in candidate["path"]
            or "\\Library\\" in candidate["path"]
        )
        if is_already_managed and not exact:
            available = sorted(
                {m["version"] for m in matches},
                key=_parse_version_tuple,
            )
            action["outcome"] = "version_mismatch_on_disk"
            action["available_versions"] = available

            if not auto_fix_version:
                action["advice"] = (
                    "Library '{}' is installed but not at the pinned "
                    "version '{}'. Available: {}. Re-run with "
                    "`auto_fix_version=true` to auto-update the project's "
                    "reference pin, or do it manually via "
                    "`library.remove`/`library.add`.".format(
                        name, version, available
                    )
                )
                actions.append(action)
                continue

            # Auto-fix path: remove the broken pinned ref and re-add at
            # the library name (which CODESYS resolves to highest installed
            # version). The `raw` field holds the full display name needed
            # by remove_library; fall back to the parsed `name` if absent.
            best_version = available[-1] if available else ""
            action["target_version"] = best_version
            remove_target = raw or name
            try:
                remove_result = await ctx.ipc.call(
                    "library.remove",
                    {"name": remove_target},
                    timeout_s=30.0,
                )
                action["remove_result"] = {
                    "status": remove_result.status,
                    "error": remove_result.error,
                }
                if remove_result.status != "ok":
                    action["outcome"] = "fix_failed_at_remove"
                    actions.append(action)
                    continue

                add_result = await ctx.ipc.call(
                    "library.add",
                    {"name": name},
                    timeout_s=30.0,
                )
                action["add_result"] = {
                    "status": add_result.status,
                    "error": add_result.error,
                    "data": add_result.data,
                }
                if add_result.status != "ok":
                    action["outcome"] = "fix_failed_at_add"
                    actions.append(action)
                    continue

                action["outcome"] = "version_fixed"
                fixes_applied += 1
            except Exception as exc:  # noqa: BLE001
                action["outcome"] = "fix_failed_at_remove"
                action["fix_error"] = repr(exc)
            actions.append(action)
            continue

        if not auto_install:
            action["outcome"] = "candidate_found_not_installed"
            actions.append(action)
            continue

        # Attempt install
        install_result = await ctx.ipc.call(
            "library.install",
            {"path": candidate["path"], "overwrite": False},
            timeout_s=60.0,
        )
        action["install_result"] = (
            install_result.model_dump() if hasattr(install_result, "model_dump")
            else install_result
        )
        action["outcome"] = (
            "installed" if install_result.status == "ok" else "install_failed"
        )
        actions.append(action)

    # Save the project if we fixed any version pins, so the project file on
    # disk reflects the new references.
    saved_after = False
    if fixes_applied > 0 and save_after_fix:
        save_result = await ctx.ipc.call("project.save", {}, timeout_s=30.0)
        saved_after = save_result.status == "ok"

    # Step 3: re-diagnose
    post_diag = await ctx.ipc.call("library.diagnose", {}, timeout_s=30.0)
    post_missing = (post_diag.data or {}).get("missing", []) if post_diag.status == "ok" else []

    installed_count = sum(1 for a in actions if a.get("outcome") == "installed")
    return format_result({
        "status": "ok",
        "before_missing_count": len(missing),
        "after_missing_count": len(post_missing),
        "installed_count": installed_count,
        "fixes_applied": fixes_applied,
        "saved_after_fix": saved_after,
        "actions": actions,
        "post_diagnostics": post_diag.data if post_diag.status == "ok" else None,
    })


REGISTRY.register(ToolSpec(
    name="codesys.library.install_missing",
    description=(
        "End-to-end remediation for missing library references. Workflow:\n"
        "  1. Calls `library.diagnose` to get the missing list.\n"
        "  2. For each missing entry, searches the filesystem for a "
        "matching `.library` / `.compiled-library` file.\n"
        "  3. If an exact-version file is found, installs it via "
        "`library.install` (unless `auto_install=false`).\n"
        "  4. If only different versions are on disk (the project pins a "
        "version that's no longer installed), and `auto_fix_version=true` "
        "(default), the broken reference is removed and re-added at the "
        "library's name — CODESYS resolves it to the highest installed "
        "version. The project is then saved (unless `save_after_fix=false`).\n"
        "  5. Re-runs diagnose to confirm what's now resolved.\n"
        "\n"
        "Returns per-entry actions so the caller can see exactly what "
        "happened: `installed`, `version_fixed`, `version_mismatch_on_disk`, "
        "`not_found`, `install_failed`, `fix_failed_at_remove`, "
        "`fix_failed_at_add`, `candidate_found_not_installed`. The summary "
        "fields `installed_count` and `fixes_applied` count those outcomes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "auto_install": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If false, only report what WOULD be installed from "
                    "disk. Does not affect version-fix behavior."
                ),
            },
            "auto_fix_version": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If true (default), when a project pins a version of "
                    "an already-installed library that no longer exists on "
                    "disk, remove the broken pin and re-add the library by "
                    "name — letting CODESYS resolve to the highest "
                    "available installed version. The project is saved "
                    "afterwards. Set to false to keep the legacy "
                    "diagnostic-only behavior."
                ),
            },
            "save_after_fix": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If true (default), call `project.save` after applying "
                    "any version fixes so the change persists on disk."
                ),
            },
            "strict_version": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, only match exact version files on disk. "
                    "Default (false) finds any version of a named library."
                ),
            },
            "search_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Override default library search paths.",
            },
        },
        "additionalProperties": False,
    },
    handler=_install_missing_handler,
))


REGISTRY.register(ToolSpec(
    name="codesys.library.create_repository",
    description=(
        "Create an editable library repository (a 'User repo'). Most "
        "fresh CODESYS installs only ship a read-only System repo, which "
        "prevents `library.install` from working until you add an "
        "editable one. The repo's `folder` is created if missing."
    ),
    input_schema={
        "type": "object",
        "required": ["name", "folder"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Display name (e.g. 'User')."
            },
            "folder": {
                "type": "string",
                "description": "Absolute path where library files will live.",
            },
            "index": {
                "type": "integer",
                "default": 0,
                "description": "Insertion position (0 = highest priority).",
            },
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "library.create_repository", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.library.diagnose",
    description=(
        "Identify unresolved / missing library references in the project. "
        "Reads the IDE's Library Manager message category for 'Could not "
        "open library ... has not been installed to the system' errors "
        "and cross-references each one against the project's lib refs. "
        "Returns a structured `missing` list with name/version/vendor "
        "parsed from the message — feed each into `library.install` (if "
        "you have the .library file), `system.install_package` (if you "
        "have the .package), or `library.resolve_missing` (online fetch) "
        "to remediate. Call again after each install to confirm."
    ),
    input_schema={
        "type": "object",
        "properties": {
            **_proj_arg(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "library.diagnose", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.library.repositories",
    description=(
        "List library repositories known to the system. Each entry has "
        "`name`, `root_folder`, and `editable`. Most fresh CODESYS installs "
        "ship with only a read-only `System` repository. Adding an editable "
        "User repository is a one-time setup via the IDE Library Repository "
        "dialog (or APInstaller); after that, `library.install` can drop "
        ".library files into it."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "library.repositories", args, 10.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.library.install",
    description=(
        "Install a `.library` or `.compiled-library` file into a library "
        "repository. Requires at least one editable repository; defaults "
        "to the first one found, or specify `repository` by name. Set "
        "`overwrite=true` to replace an existing entry. Returns an "
        "actionable error if no editable repo is configured."
    ),
    input_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the .library / .compiled-library file.",
            },
            "repository": {
                "type": "string",
                "description": "Optional target repository name; default = first editable.",
            },
            "overwrite": {
                "type": "boolean",
                "default": False,
            },
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "library.install", args, 60.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.library.resolve_missing",
    description=(
        "Trigger the IDE's 'Download missing libraries...' workflow with "
        "prompts suppressed. Pairs with `library.list_project` to confirm "
        "what was resolved. If libraries remain unresolved, install them "
        "manually via `system.install_package` (APInstaller) or by adding "
        "the .library file via `library.install`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "forward_prompts": {
                "type": "boolean",
                "default": False,
                "description": "Advanced: forward unknown prompts instead of suppressing them.",
            },
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "library.resolve_missing", args, 60.0),
))
