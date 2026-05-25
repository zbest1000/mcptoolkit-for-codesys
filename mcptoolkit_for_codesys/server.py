"""
MCP stdio server entry point.

Run via:

    mcptoolkit-for-codesys                  # uses defaults + env vars
    mcptoolkit-for-codesys --workdir <dir>  # override IPC dir
    mcptoolkit-for-codesys --sp 22          # pin a specific CODESYS service pack

Env vars:
    MCPTOOLKIT_WORKDIR   directory for commands/ + results/ (default: %TEMP%/mcptoolkit-for-codesys)
    CODESYS_EXE           full path to CODESYS.exe (overrides discovery)
    CODESYS_PROFILE       full path to .profile.xml (overrides discovery)
    MCPTOOLKIT_SP        prefer this SP number (e.g. 22)
    MCPTOOLKIT_HEADLESS  set "1" to launch CODESYS with --noUI
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .ipc import IpcClient, IpcTimeout
from .watcher_manager import (
    WatcherManager,
    find_watcher_script,
    pick_install,
)
from .tools import REGISTRY, ToolContext, error_envelope


log = logging.getLogger("mcptoolkit_for_codesys")


def _default_workdir() -> Path:
    env = os.environ.get("MCPTOOLKIT_WORKDIR")
    if env:
        return Path(env)
    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or "."
    return Path(tmp) / "mcptoolkit-for-codesys"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mcptoolkit-for-codesys")
    p.add_argument("--workdir", type=Path, default=_default_workdir())
    p.add_argument("--sp", type=int, default=None, help="Prefer this CODESYS SP (e.g. 22).")
    p.add_argument("--headless", action="store_true", help="Launch CODESYS with --noUI.")
    p.add_argument(
        "--log-level",
        default=os.environ.get("MCPTOOLKIT_LOG", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def _resolve_install(args: argparse.Namespace):
    explicit_exe = os.environ.get("CODESYS_EXE")
    explicit_profile = os.environ.get("CODESYS_PROFILE")
    prefer_sp = args.sp or (
        int(os.environ["MCPTOOLKIT_SP"]) if os.environ.get("MCPTOOLKIT_SP") else None
    )
    return pick_install(
        prefer_sp=prefer_sp,
        explicit_exe=Path(explicit_exe) if explicit_exe else None,
        explicit_profile=Path(explicit_profile) if explicit_profile else None,
    )


async def _serve(args: argparse.Namespace) -> None:
    install = _resolve_install(args)
    log.info("using CODESYS: %s @ %s", install.key, install.install_dir)
    log.info("profile: %s", install.profile_name())

    headless = args.headless or os.environ.get("MCPTOOLKIT_HEADLESS") == "1"
    manager = WatcherManager(
        workdir=args.workdir,
        watcher_script=find_watcher_script(),
        install=install,
        show_ide=not headless,
    )
    # Do NOT await manager.start() here. CODESYS startup takes 30-90s, and the
    # MCP client's `initialize` request has a 60s timeout — blocking init on
    # spawn would always trip the timeout. Spawn lazily on the first tool call
    # via manager.ensure_started() so initialize + list_tools return instantly.

    ipc = IpcClient(workdir=args.workdir)
    ctx = ToolContext(ipc=ipc, install=install, manager=manager)

    server = Server("mcptoolkit-for-codesys")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [spec.to_mcp_tool() for spec in REGISTRY.specs()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        spec = REGISTRY.get(name)
        if spec is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            await manager.ensure_started()
            payload = await spec.handler(ctx, arguments or {})
        except IpcTimeout as exc:
            # A timeout almost always means CODESYS is blocked on a modal
            # dialog. Capture which one and hand the LLM something actionable
            # instead of a bare "no result" — then mark the watcher for
            # recovery on the next call.
            diag = manager.diagnose_hang()
            log.warning("tool %s timed out; hang diagnosis: %s", name, diag)
            return [TextContent(type="text", text=error_envelope(
                "IpcTimeout", str(exc),
                hang_diagnosis=diag,
                advice=(
                    "CODESYS did not respond in time. If 'dialogs' lists a "
                    "modal window, the IDE is waiting for input — the dialog "
                    "guard auto-confirms safe prompts; a hung watcher is "
                    "killed and respawned on the next call. Retry shortly."
                ),
            ))]
        except Exception as exc:  # noqa: BLE001 — surface all failures to the LLM
            log.exception("tool %s failed", name)
            return [TextContent(type="text", text=error_envelope(
                type(exc).__name__, str(exc),
                advice="Host-side tool error. See the message; retry or adjust arguments.",
            ))]
        return [TextContent(type="text", text=payload)]

    # Background guard: auto-confirm the watcher's own modal dialogs (storage
    # upgrade, save prompts) so a scripted op never wedges waiting for a click.
    manager.start_dialog_guard()

    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await manager.stop_dialog_guard()
        await manager.stop()


def main() -> None:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        log.info("interrupted; shutting down.")


if __name__ == "__main__":
    main()
