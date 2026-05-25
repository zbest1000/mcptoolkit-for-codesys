"""
MCP tool registry.

Each tool module declares one or more `ToolSpec`s and self-registers in
`REGISTRY`. The registry is what `server.py` enumerates for `list_tools()`
and dispatches via `call_tool()`.

A tool's `handler` does ONE thing: call into the watcher via `ctx.ipc.call(op, args)`
and format the result as a human-readable string. The Pydantic/JSON-schema
validation of `arguments` happens in the MCP framework before the handler runs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from mcp.types import Tool, ToolAnnotations

from ..ipc import IpcClient
from ..watcher_manager import CodesysInstall, WatcherManager


@dataclass
class ToolContext:
    ipc: IpcClient
    install: CodesysInstall
    manager: WatcherManager


ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[str]]


# --- Tool risk classification (single source of truth) -----------------------
# MCP tool annotations are advisory hints a client surfaces to the user and the
# model — badge read-only tools, flag/confirm destructive ones. We classify
# centrally by name (not per-ToolSpec across 10 modules) so the entire risk
# picture is reviewable in one place; the test suite guards these sets against
# typos and against drift as tools are added/renamed.

# Tools that only QUERY state — no change to project, IDE, or device. Note a
# tool that writes a file to disk (e.g. *.export_xml, *.save_archive) is NOT
# read-only: it modifies the filesystem.
_READ_ONLY = {
    "codesys.ping", "codesys.info", "codesys.diagnose", "codesys.health",
    "codesys.project.info", "codesys.project.tree", "codesys.project.list_open",
    "codesys.project.diff",
    "codesys.build.messages",
    "codesys.pou.find", "codesys.pou.get_text", "codesys.pou.list_variables",
    "codesys.online.read", "codesys.online.state", "codesys.online.snapshot",
    "codesys.online.forced",
    "codesys.library.list_installed", "codesys.library.list_project",
    "codesys.library.find_on_disk", "codesys.library.repositories",
    "codesys.library.diagnose",
    "codesys.device.list_installed", "codesys.device.categories",
    "codesys.device.tree", "codesys.device.parameters",
    "codesys.symbol.list",
    "codesys.task.list",
    "codesys.system.list_installations",
}

# Irreversible or physically-actuating tools. Mirrors online.py's confirm-gated
# set (start/reset/write/force — equipment may move / state may be cleared) plus
# recursive tree deletion. Keep in sync when adding a high-impact op.
_DESTRUCTIVE = {
    "codesys.online.start", "codesys.online.reset",
    "codesys.online.write", "codesys.online.force",
    "codesys.pou.delete",
}

# Tools that reach an EXTERNAL entity: the live PLC (all online.*) or remote
# library repositories (the online-fetch resolvers). Everything else operates on
# the local IDE/project/filesystem and is closed-world.
_OPEN_WORLD = {
    "codesys.online.login", "codesys.online.logout", "codesys.online.read",
    "codesys.online.write", "codesys.online.force", "codesys.online.forced",
    "codesys.online.unforce_all", "codesys.online.start", "codesys.online.stop",
    "codesys.online.reset", "codesys.online.state", "codesys.online.snapshot",
    "codesys.online.create_boot", "codesys.online.source_download",
    "codesys.online.set_credentials",
    "codesys.library.install_missing", "codesys.library.resolve_missing",
}


def annotations_for(name: str) -> ToolAnnotations:
    """Derive MCP tool annotations from a tool's name — see the sets above."""
    read_only = name in _READ_ONLY
    return ToolAnnotations(
        readOnlyHint=read_only,
        # destructiveHint is only meaningful when the tool is NOT read-only.
        # Leave it unset for read-only tools; otherwise set it explicitly so the
        # safe-mutating majority is marked non-destructive (the spec default is
        # True, which would wrongly flag every editing tool).
        destructiveHint=None if read_only else (name in _DESTRUCTIVE),
        openWorldHint=name in _OPEN_WORLD,
    )


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def to_mcp_tool(self) -> Tool:
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema=self.input_schema,
            annotations=annotations_for(self.name),
        )


class _Registry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())


REGISTRY = _Registry()


def format_result(result: Any) -> str:
    """Render a watcher Result (or other payload) as a compact, LLM-friendly string.

    When the Result is an error, add a concise `error_summary` (the last line
    of the traceback) so the LLM gets the actionable gist without parsing the
    full multi-line `error` blob. The full traceback stays in `error` for when
    detail is needed.
    """
    # Accept Result Pydantic model, plain dict, or anything JSON-able.
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    if isinstance(result, dict) and result.get("status") == "error":
        err = result.get("error")
        if err and "error_summary" not in result:
            last = next(
                (ln.strip() for ln in reversed(str(err).splitlines()) if ln.strip()),
                None,
            )
            if last:
                result = {**result, "error_summary": last}
    return json.dumps(result, indent=2, default=str)


def error_envelope(kind: str, message: str, **extra: Any) -> str:
    """Build a consistent, parseable error envelope for host-side failures.

    Every tool error the LLM sees should have the same shape: a machine-
    readable `error_kind`, a human `error`, and `status: error` — never a bare
    string it has to regex. `extra` carries tool-specific context (e.g.
    hang_diagnosis, advice)."""
    payload = {"status": "error", "error_kind": kind, "error": message}
    payload.update(extra)
    return json.dumps(payload, indent=2, default=str)


# Side-effect imports register their tools with REGISTRY.
from . import meta  # noqa: E402, F401
from . import project  # noqa: E402, F401
from . import build  # noqa: E402, F401
from . import pou  # noqa: E402, F401
from . import online  # noqa: E402, F401
from . import library  # noqa: E402, F401
from . import device  # noqa: E402, F401
from . import symbol  # noqa: E402, F401
from . import task  # noqa: E402, F401
from . import system  # noqa: E402, F401
