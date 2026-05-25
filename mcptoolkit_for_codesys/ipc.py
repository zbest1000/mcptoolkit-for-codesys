"""
File-based JSON IPC client (host side).

Why file IPC and not sockets:
- IronPython 2.7's networking story is fragile under the CODESYS UI thread.
- File IPC survives IDE freezes / dialogs that would block a socket reader.

Writes go through a *.tmp + rename so the watcher never reads a half-written
command. Reads tolerate the same on the way back.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from .schemas import Command, Result


class IpcError(RuntimeError):
    pass


class IpcTimeout(IpcError):
    pass


class IpcClient:
    """One client per workdir. Workdir layout:

        <workdir>/
            commands/   host writes here, watcher consumes
            results/    watcher writes here, host consumes
            inbox.lock  optional advisory marker for the watcher (unused for now)
    """

    def __init__(self, workdir: Path, poll_interval_s: float = 0.05):
        self.workdir = Path(workdir).resolve()
        self.commands_dir = self.workdir / "commands"
        self.results_dir = self.workdir / "results"
        self.poll_interval_s = poll_interval_s
        self.commands_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return uuid.uuid4().hex

    async def call(
        self,
        op: str,
        args: dict | None = None,
        timeout_s: float = 120.0,
    ) -> Result:
        cmd = Command(
            id=self.new_id(),
            op=op,
            args=args or {},
            deadline_s=timeout_s,
        )
        self._write_command(cmd)
        return await self._await_result(cmd.id, timeout_s)

    def _write_command(self, cmd: Command) -> None:
        target = self.commands_dir / f"{cmd.id}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(cmd.model_dump_json(), encoding="utf-8")
        os.replace(tmp, target)

    async def _await_result(self, cmd_id: str, timeout_s: float) -> Result:
        path = self.results_dir / f"{cmd_id}.json"
        deadline = time.monotonic() + timeout_s
        while True:
            if path.exists():
                try:
                    raw = path.read_text(encoding="utf-8")
                    result = Result.model_validate_json(raw)
                    path.unlink(missing_ok=True)
                    return result
                except (OSError, ValueError):
                    # Mid-write race; retry next tick.
                    pass
            if time.monotonic() >= deadline:
                raise IpcTimeout(
                    f"No result for op after {timeout_s:.1f}s (id={cmd_id})"
                )
            await asyncio.sleep(self.poll_interval_s)
