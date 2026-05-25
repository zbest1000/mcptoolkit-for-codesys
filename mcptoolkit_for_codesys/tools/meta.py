"""
Meta tools: ping, info — used to verify the IPC spine and inspect the watcher.
"""
from __future__ import annotations

from typing import Any

from . import REGISTRY, ToolContext, ToolSpec, format_result


async def _ping(ctx: ToolContext, args: dict[str, Any]) -> str:
    payload = {"echo": args.get("echo", "hello")}
    if args.get("verbose"):
        payload["verbose"] = True
    result = await ctx.ipc.call("ping", payload, timeout_s=15.0)
    return format_result(result)


async def _info(ctx: ToolContext, args: dict[str, Any]) -> str:
    info = {
        "host_install": {
            "key": ctx.install.key,
            "version": ctx.install.version,
            "sp": ctx.install.sp,
            "patch": ctx.install.patch,
            "install_dir": str(ctx.install.install_dir),
            "exe": str(ctx.install.exe),
            "profile": ctx.install.profile_name(),
        },
        "workdir": str(ctx.ipc.workdir),
    }
    return format_result(info)


async def _diagnose(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Host-side diagnosis; never calls into the watcher, so it returns even
    when the watcher is wedged. Reports PID liveness, the heartbeat, and any
    modal dialog blocking the IDE (from a Win32 window enumeration).
    """
    diag = ctx.manager.diagnose_hang()
    return format_result(diag)


async def _health(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Combine host-side process state with the watcher's self-report.

    The watcher's `health` op is cheap and is a useful liveness probe.
    Wrapping it on the host side lets us also report watcher.ready file
    age, command/result queue depth, and whether CODESYS is alive from
    the OS's perspective.
    """
    import os
    import time as _time

    # Host-side observations.
    workdir = ctx.ipc.workdir
    ready_path = workdir / "watcher.ready"
    ready_age = None
    if ready_path.exists():
        try:
            ready_age = round(_time.time() - ready_path.stat().st_mtime, 2)
        except OSError:
            ready_age = None

    try:
        queue_depth = len(list((workdir / "commands").glob("*.json")))
    except OSError:
        queue_depth = -1
    try:
        pending_results = len(list((workdir / "results").glob("*.json")))
    except OSError:
        pending_results = -1

    proc = ctx.manager.process
    host_view = {
        "workdir": str(workdir),
        "watcher_ready_present": ready_path.exists(),
        "watcher_ready_age_s": ready_age,
        "command_queue_depth": queue_depth,
        "pending_result_files": pending_results,
        "codesys_pid": ctx.manager.current_pid(),
        "codesys_running": bool(proc and proc.is_running()),
        "liveness": proc.liveness() if proc else "none",
        "adopted": bool(proc and proc.adopted),
    }

    # Watcher self-report (cheap call, but may time out if dispatch is wedged).
    watcher_view: dict[str, Any] = {}
    try:
        result = await ctx.ipc.call("health", {}, timeout_s=5.0)
        watcher_view = {
            "status": result.status,
            "data": result.data,
            "elapsed_ms": result.elapsed_ms,
        }
    except Exception as exc:  # noqa: BLE001
        watcher_view = {"status": "unreachable", "error": str(exc)}

    return format_result({"host": host_view, "watcher": watcher_view})


REGISTRY.register(
    ToolSpec(
        name="codesys.ping",
        description=(
            "Round-trip the watcher process. Returns IronPython/CODESYS version and "
            "the list of ops currently registered inside the watcher. Use this to "
            "verify the MCP <-> CODESYS bridge is alive."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "echo": {
                    "type": "string",
                    "description": "Optional string to echo back. Default: 'hello'.",
                },
                "verbose": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Include diagnostic fields (full list of CODESYS-"
                        "injected globals + enum member names per known enum). "
                        "Useful when debugging API drift; otherwise leave off "
                        "to avoid ~10KB of payload."
                    ),
                },
            },
            "additionalProperties": False,
        },
        handler=_ping,
    )
)

REGISTRY.register(
    ToolSpec(
        name="codesys.info",
        description=(
            "Report which CODESYS installation the MCP server is driving and where "
            "the IPC working directory lives. Host-side only — does not call into "
            "CODESYS."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_info,
    )
)

REGISTRY.register(
    ToolSpec(
        name="codesys.diagnose",
        description=(
            "Hang-proof health check. Reads only host-side signals (CODESYS "
            "PID liveness, the watcher heartbeat file, and an enumeration of "
            "CODESYS's visible windows) — it never calls into the watcher, so "
            "it returns instantly even when every other tool is timing out. "
            "Use this FIRST when tools hang: the `dialogs` list names any modal "
            "window blocking the IDE (e.g. 'Download missing libraries?'), and "
            "`liveness` is one of healthy/hung/dead/none. A 'hung' watcher will "
            "be killed and respawned automatically on the next tool call."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_diagnose,
    )
)

REGISTRY.register(
    ToolSpec(
        name="codesys.health",
        description=(
            "Liveness + state probe. Combines host-side process observations "
            "(CODESYS PID, watcher.ready file age, command/result queue depth) "
            "with the watcher's own snapshot (uptime, primary project, build "
            "message count, injected-globals sanity). Cheap (~100ms when "
            "healthy); use it as a first call when diagnosing why other tools "
            "timed out or returned odd data. If the watcher view returns "
            "`status: unreachable`, the IPC bridge is broken — try "
            "`build.force_recompile` to respawn CODESYS, or restart Claude "
            "Desktop entirely."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_health,
    )
)
