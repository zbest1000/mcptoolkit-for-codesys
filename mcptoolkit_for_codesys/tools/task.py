"""Task configuration MCP tools.

Covers what the SP22 script API exposes: list tasks (with interval/priority/
watchdog and their POU calls), set a task's interval/priority/watchdog, and
create a new task. Assigning a POU to a task and the execution type
(cyclic/freewheeling/event) are NOT scriptable — set those in the IDE or import
a configured task via PLCopenXML.
"""
from __future__ import annotations

from typing import Any

from . import REGISTRY, ToolContext, ToolSpec, format_result


async def _call(ctx: ToolContext, op: str, args: dict[str, Any], timeout: float = 30.0) -> str:
    return format_result(await ctx.ipc.call(op, args, timeout_s=timeout))


def _common() -> dict:
    return {"project_path": {"type": "string"}}


REGISTRY.register(ToolSpec(
    name="codesys.task.list",
    description=(
        "List the project's tasks with their `interval` (IEC TIME, e.g. "
        "`t#50ms`), `priority`, `watchdog` settings, and the POU calls assigned "
        "to each (child task-references). The starting point for `task.set`."
    ),
    input_schema={"type": "object", "properties": _common(), "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "task.list", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.task.set",
    description=(
        "Set a task's scheduling. `interval` is an IEC TIME literal "
        "(`\"t#100ms\"`), `priority` a number 0–31 (sent as a string). "
        "`watchdog_enabled` / `watchdog_time` configure the watchdog. `name` "
        "selects the task (default the only one). Returns before/after per "
        "field."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Task name; default the only task."},
            "interval": {"type": "string", "description": "IEC TIME, e.g. 't#100ms'."},
            "priority": {"type": ["integer", "string"], "description": "0–31."},
            "watchdog_enabled": {"type": "boolean"},
            "watchdog_time": {"type": "string", "description": "IEC TIME for the watchdog."},
            **_common(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "task.set", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.task.create",
    description=(
        "Create a new task under the task configuration, optionally setting its "
        "`interval` and `priority`. Note: assigning a POU to the task is not "
        "scriptable on SP22 — do that in the IDE or via PLCopenXML import."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "New task name."},
            "interval": {"type": "string", "description": "IEC TIME, e.g. 't#20ms'."},
            "priority": {"type": ["integer", "string"]},
            **_common(),
        },
        "required": ["name"],
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "task.create", args, 30.0),
))
