"""Start CODESYS + the watcher VISIBLY in the current interactive desktop.

Run this ON the CODESYS machine (via start-codesys-visible.cmd) when you want to
WATCH the IDE on the physical screen while a remote Claude drives it over SSH.
An MCP server launched over SSH runs in a non-interactive Windows session, so a
CODESYS it spawns would render invisibly. Start it here instead, and the server
ADOPTS this already-running watcher via the shared workdir.

Closing this window does NOT close CODESYS — it keeps running until you close it
yourself. A remote client disconnecting also leaves it running (adopted watchers
are not stopped on disconnect).

The workdir below MUST match the --workdir in codesys-mcp-stdio.cmd.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcptoolkit_for_codesys.watcher_manager import (
    WatcherManager,
    find_watcher_script,
    pick_install,
)

WORKDIR = Path(
    os.environ.get(
        "MCPTOOLKIT_WORKDIR",
        os.path.join(os.environ["LOCALAPPDATA"], "mcptoolkit-for-codesys"),
    )
)
SP = int(os.environ.get("MCPTOOLKIT_SP", "22"))


def main() -> None:
    mgr = WatcherManager(
        workdir=WORKDIR,
        watcher_script=find_watcher_script(),
        install=pick_install(prefer_sp=SP),
        show_ide=True,            # render on THIS interactive desktop
        startup_timeout_s=180.0,
    )
    print(f"Starting CODESYS SP{SP} (visible) on workdir:\n  {WORKDIR}\n")
    asyncio.run(mgr.ensure_started())
    print("\nwatcher ready. CODESYS stays up after this window closes.")
    print("A local or SSH-launched MCP server will now ADOPT this instance.")


if __name__ == "__main__":
    main()
