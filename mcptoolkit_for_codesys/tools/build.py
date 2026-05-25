"""Build / compile MCP tools."""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import REGISTRY, ToolContext, ToolSpec, format_result


async def _call(ctx: ToolContext, op: str, args: dict[str, Any], timeout: float) -> str:
    return format_result(await ctx.ipc.call(op, args, timeout_s=timeout))


async def _validate_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Build + categorize messages + flag common issues in one call.

    Runs `build.build`, then `library.diagnose`, and synthesizes a lint-style
    report: error/warning counts, messages grouped by source and by error
    number, missing-library flags, and a verdict. Host-side orchestrator —
    no new watcher op.
    """
    build = await ctx.ipc.call("build.build", args, timeout_s=600.0)
    if build.status != "ok":
        return format_result({
            "status": "error", "error_kind": "BuildFailed",
            "error": build.error, "stage": "build.build",
        })
    data = build.data or {}
    messages = data.get("messages", [])
    errors = [m for m in messages if m.get("severity") == "error"]
    warnings = [m for m in messages if m.get("severity") == "warning"]

    by_source = Counter((m.get("source") or "<none>") for m in errors)
    by_number = Counter(str(m.get("number")) for m in errors if m.get("number"))

    # Common-issue flags.
    flags = []
    err_text = " ".join((m.get("text") or "") for m in errors).lower()
    if "device not installed" in err_text:
        flags.append("device_not_installed — a device package is missing or the "
                     "PLC descriptor is outdated; try device.update or "
                     "system.install_package")
    if "has not been installed" in err_text or "could not open library" in err_text:
        flags.append("missing_library — see library.diagnose / library.install_missing")
    if "maximum number of tasks" in err_text:
        flags.append("task_limit — the runtime's task limit was exceeded")

    # Cross-check unresolved libraries (best-effort; ignore if unavailable).
    missing_libs = []
    try:
        diag = await ctx.ipc.call("library.diagnose", _proj_only(args), timeout_s=30.0)
        if diag.status == "ok":
            missing_libs = (diag.data or {}).get("missing", [])
            if missing_libs and not any(f.startswith("missing_library") for f in flags):
                flags.append("missing_library — see library.diagnose")
    except Exception:  # noqa: BLE001
        pass

    verdict = ("clean" if not errors and not warnings
               else "errors" if errors else "warnings_only")
    return format_result({
        "status": "ok",
        "verdict": verdict,
        "errors": data.get("errors", len(errors)),
        "warnings": data.get("warnings", len(warnings)),
        "errors_by_source": dict(by_source),
        "errors_by_number": dict(by_number),
        "flags": flags,
        "missing_libraries": [
            {"name": m.get("name"), "version": m.get("version")} for m in missing_libs
        ],
        "first_errors": [
            {"source": m.get("source"), "text": m.get("text"), "position": m.get("position")}
            for m in errors[:10]
        ],
    })


def _proj_only(args: dict[str, Any]) -> dict:
    out = {}
    if args.get("project_path"):
        out["project_path"] = args["project_path"]
    return out


def _common() -> dict:
    return {
        "project_path": {"type": "string", "description": "Already-open project to target."},
        "application": {
            "type": "string",
            "description": "Name of the Application object; omit if the project has exactly one.",
        },
    }


REGISTRY.register(ToolSpec(
    name="codesys.build.build",
    description=(
        "Incremental build of an Application. Returns counts of errors/warnings "
        "and the most recent build messages."
    ),
    input_schema={
        "type": "object",
        "properties": _common(),
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "build.build", args, 600.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.build.rebuild",
    description="Full rebuild of an Application. Slower; use after deep changes.",
    input_schema={
        "type": "object",
        "properties": _common(),
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "build.rebuild", args, 900.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.build.validate",
    description=(
        "Build and return a lint-style report: a `verdict` "
        "(clean/errors/warnings_only), error/warning counts, errors grouped by "
        "`source` and by error `number`, the first few errors, detected "
        "`missing_libraries`, and `flags` for common root causes "
        "(device_not_installed, missing_library, task_limit) with the tool to "
        "fix each. One call instead of build + manual triage."
    ),
    input_schema={
        "type": "object",
        "properties": _common(),
        "additionalProperties": False,
    },
    handler=_validate_handler,
))


REGISTRY.register(ToolSpec(
    name="codesys.build.clean",
    description="Drop compile output for one Application.",
    input_schema={
        "type": "object",
        "properties": _common(),
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "build.clean", args, 60.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.build.clean_all",
    description="Drop compile output for the entire project.",
    input_schema={
        "type": "object",
        "properties": {"project_path": {"type": "string"}},
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "build.clean_all", args, 120.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.build.generate_code",
    description="Run the codegen pass (does NOT download; safe to call before login).",
    input_schema={
        "type": "object",
        "properties": _common(),
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "build.generate_code", args, 600.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.build.messages",
    description="Return the most recent build/compile messages without running a build.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "build.messages", args, 10.0),
))


async def _force_recompile_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Kill+respawn CODESYS to guarantee a fresh compile context, then
    rebuild. Workaround for the SP22 build-no-op-after-first-call
    limitation (see CHANGES.md). Costs ~30-60s wall-clock per call because
    CODESYS startup is slow; use sparingly, e.g. for CI-style verification
    after a batch of source edits.
    """
    import logging
    log = logging.getLogger("mcptoolkit_for_codesys.build.force_recompile")

    # 1. Save + capture project path.
    save = await ctx.ipc.call("project.save", {}, timeout_s=60.0)
    proj = (save.data or {}).get("project") or {}
    path = proj.get("path")
    if not path:
        return format_result({
            "status": "error",
            "error": "force_recompile: could not determine project path from project.save",
            "save_result": save.model_dump() if hasattr(save, "model_dump") else save,
        })

    # 2. Stop the watcher + CODESYS.
    log.info("force_recompile: stopping watcher to drop compile context")
    await ctx.manager.stop()

    # 3. Respawn. ensure_started() acquires its internal lock; the next
    # tool call would also lazy-spawn, but we want a deterministic
    # sequence so we wait here.
    log.info("force_recompile: respawning CODESYS")
    await ctx.manager.ensure_started()

    # 4. Reopen the project (fresh CODESYS has no primary).
    open_result = await ctx.ipc.call("project.open", {"path": path}, timeout_s=120.0)
    if open_result.status != "ok":
        return format_result({
            "status": "error",
            "error": "force_recompile: project.open failed after respawn",
            "open_result": open_result.model_dump() if hasattr(open_result, "model_dump") else open_result,
        })

    # 5. Build — first build in a fresh CODESYS session is the one that
    # actually runs.
    build_args = {k: v for k, v in (args or {}).items()
                  if k in ("application", "project_path")}
    build_result = await ctx.ipc.call("build.build", build_args, timeout_s=600.0)
    return format_result({
        "status": "ok",
        "force_recompile": True,
        "reopened_project": path,
        "build": build_result.model_dump() if hasattr(build_result, "model_dump") else build_result,
    })


REGISTRY.register(ToolSpec(
    name="codesys.build.force_recompile",
    description=(
        "Force a real recompile by killing+respawning CODESYS to get a fresh "
        "compile context, then reopening the current project and building. "
        "Workaround for the SP22 'build no-op after the first call per "
        "CODESYS session' limitation. Saves the current project first. "
        "Costs ~30-60s wall-clock; only use for CI-style verification, "
        "not interactive editing."
    ),
    input_schema={
        "type": "object",
        "properties": _common(),
        "additionalProperties": False,
    },
    handler=_force_recompile_handler,
))
