"""Online / runtime MCP tools."""
from __future__ import annotations

import os
from typing import Any

from . import REGISTRY, ToolContext, ToolSpec, error_envelope, format_result


async def _call(ctx: ToolContext, op: str, args: dict[str, Any], timeout: float = 60.0) -> str:
    return format_result(await ctx.ipc.call(op, args, timeout_s=timeout))


# Ops that actuate physical equipment. They require an explicit `confirm: true`
# so an LLM can't start/reset/perturb a live PLC by accident — the skill's
# "confirm destructive actions" pattern, enforced at the MCP boundary before
# anything reaches CODESYS.
_CONFIRM_REQUIRED = {
    "online.start": "START the PLC application — the controlled equipment may move/run",
    "online.reset": "RESET the PLC application — clears retained state and outputs",
    "online.write": "WRITE values to the LIVE PLC — can change outputs / move actuators",
    "online.force": "FORCE values on the LIVE PLC — overrides program logic on outputs",
}


async def _online_call(ctx: ToolContext, op: str, args: dict[str, Any], timeout: float = 60.0) -> str:
    """Host-side wrapper for online ops adding two safety controls:

    1. Env-var credential references: `username_env`/`password_env` name an
       environment variable the HOST reads, so the raw secret never appears in
       the tool-call arguments (which are logged in the conversation transcript).
    2. Confirm-gate: physical-impact ops require `confirm: true`.
    """
    args = dict(args or {})
    # 1. Resolve env-var credential references.
    for field in ("username", "password"):
        env_key = args.pop(field + "_env", None)
        if env_key and not args.get(field):
            val = os.environ.get(env_key)
            if val is None or val == "":
                return error_envelope(
                    "MissingEnvCredential",
                    f"environment variable {env_key!r} (for {field}) is not set on the host",
                )
            args[field] = val
    # 2. Confirm-gate for actuating ops.
    if op in _CONFIRM_REQUIRED:
        if not bool(args.pop("confirm", False)):
            return error_envelope(
                "ConfirmationRequired",
                f"This will {_CONFIRM_REQUIRED[op]}. Re-call with confirm: true to proceed.",
                requires="confirm: true",
            )
    args.pop("confirm", None)  # never forward to the watcher
    return format_result(await ctx.ipc.call(op, args, timeout_s=timeout))


def _common() -> dict:
    return {
        "project_path": {"type": "string"},
        "application": {"type": "string", "description": "Name of the Application object."},
    }


def _cred_props() -> dict:
    return {
        "username_env": {
            "type": "string",
            "description": (
                "Name of a host environment variable holding the username. "
                "Preferred over `username` so the secret stays out of the "
                "tool-call log."
            ),
        },
        "password_env": {
            "type": "string",
            "description": (
                "Name of a host environment variable holding the password. "
                "Preferred over `password` so the secret stays out of the "
                "tool-call log."
            ),
        },
    }


REGISTRY.register(ToolSpec(
    name="codesys.online.login",
    description=(
        "Log into the target device. `mode` maps to CODESYS's OnlineChangeOption:\n"
        "  - `never`/`download` (default): full download, no online change.\n"
        "  - `try`/`online_change`: online change if possible, else download.\n"
        "  - `force`: force online change.\n"
        "  - `keep`: keep the code currently on the target.\n"
        "Requires the device's connection (gateway + target node) configured AND "
        "a runtime reachable. `application` selects which app when the project "
        "has several (by app name OR owning device name).\n"
        "\n"
        "CREDENTIALS: if the runtime requires authentication, pass `username` and "
        "`password`. ALWAYS obtain these from the user — never invent, guess, "
        "default, or reuse a hardcoded value. If you don't have them and login "
        "fails on auth, ASK THE USER for the device username and password and "
        "retry. Creating a NEW device user (on a fresh runtime with none) is a "
        "deliberate act: only set `setup_initial_user: true` when the user has "
        "explicitly asked to provision that account."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["never", "download", "try", "online_change", "force", "keep"],
                "default": "never",
            },
            "delete_foreign_apps": {
                "type": "boolean",
                "default": True,
                "description": "Remove apps on the target not in this project.",
            },
            "username": {
                "type": "string",
                "description": (
                    "Device user to authenticate as — supplied BY THE USER. "
                    "Never auto-generate or default this."
                ),
            },
            "password": {
                "type": "string",
                "description": (
                    "Device password — supplied BY THE USER. Never invent one. "
                    "Must satisfy the runtime policy (8+ chars, "
                    "upper+lower+digit+special) for new accounts."
                ),
            },
            "setup_initial_user": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Create `username` as the initial device user if the runtime "
                    "has none. Off by default — only enable when the user has "
                    "explicitly asked to provision a new account, using "
                    "credentials they chose."
                ),
            },
            **_cred_props(),
            **_common(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _online_call(ctx, "online.login", args, 600.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.set_credentials",
    description=(
        "Register device-user credentials for the target runtime, separately "
        "from login. A fresh CODESYS SP22 soft PLC ships with no user and "
        "enforces a password policy (8+ chars, upper+lower+digit+special).\n"
        "\n"
        "ALWAYS get `username` and `password` FROM THE USER — never generate, "
        "guess, or hardcode them. `setup_initial_user` (default false) actually "
        "CREATES `username` on the runtime if it has none; only enable it when "
        "the user has explicitly asked to provision that account, with a "
        "password they chose. Without it, this just registers credentials for "
        "authenticating against existing users."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "Supplied by the user."},
            "password": {"type": "string", "description": "Supplied by the user; never invented."},
            "setup_initial_user": {"type": "boolean", "default": False},
            "can_change_password": {"type": "boolean", "default": True},
            "must_change_password": {"type": "boolean", "default": False},
            **_cred_props(),
            **_common(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _online_call(ctx, "online.set_credentials", args, 60.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.logout",
    description="Disconnect from the target device.",
    input_schema={"type": "object", "properties": _common(), "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "online.logout", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.start",
    description=(
        "Start the running application (transition to RUN). Physical-impact: "
        "controlled equipment may move/run, so this requires `confirm: true`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "confirm": {
                "type": "boolean",
                "description": "Must be true — acknowledges the PLC will run.",
            },
            **_common(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _online_call(ctx, "online.start", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.stop",
    description="Stop the running application (transition to STOP).",
    input_schema={"type": "object", "properties": _common(), "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "online.stop", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.reset",
    description=(
        "Reset the application: warm (keep retains), cold (clear retains), or "
        "origin/original (clear everything, like a fresh download). `force_kill` "
        "(default true) stops the app if it's running before resetting."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["warm", "cold", "origin", "original"], "default": "warm"},
            "force_kill": {"type": "boolean", "default": True},
            "confirm": {
                "type": "boolean",
                "description": "Must be true — acknowledges retained state/outputs are cleared.",
            },
            **_common(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _online_call(ctx, "online.reset", args, 60.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.state",
    description="Report the application_state / operation_state / is_logged_in flags.",
    input_schema={"type": "object", "properties": _common(), "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "online.state", args, 10.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.read",
    description=(
        "Read one or more IEC expressions from the live device. Expressions are "
        "the same syntax as the Watch window: e.g. 'PLC_PRG.iCounter', "
        "'Application.GVL.bDone'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "expression": {"type": "string"},
            "expressions": {"type": "array", "items": {"type": "string"}},
            **_common(),
        },
        "oneOf": [
            {"required": ["expression"]},
            {"required": ["expressions"]},
        ],
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "online.read", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.write",
    description=(
        "Write one or more IEC expressions to the live device. Values are sent "
        "as strings and CODESYS coerces to the declared type."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "expression": {"type": "string"},
            "value": {"type": ["string", "number", "boolean"]},
            "writes": {
                "type": "object",
                "additionalProperties": {"type": ["string", "number", "boolean"]},
                "description": "Map of expression -> value for batch writes.",
            },
            "confirm": {
                "type": "boolean",
                "description": "Must be true — acknowledges this changes live PLC values.",
            },
            **_common(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _online_call(ctx, "online.write", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.force",
    description="Force expressions to a value (override scan-cycle writes).",
    input_schema={
        "type": "object",
        "properties": {
            "expression": {"type": "string"},
            "value": {"type": ["string", "number", "boolean"]},
            "forces": {
                "type": "object",
                "additionalProperties": {"type": ["string", "number", "boolean"]},
            },
            "confirm": {
                "type": "boolean",
                "description": "Must be true — forcing overrides program logic on live outputs.",
            },
            **_common(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _online_call(ctx, "online.force", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.unforce_all",
    description="Release all forced expressions.",
    input_schema={"type": "object", "properties": _common(), "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "online.unforce_all", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.snapshot",
    description=(
        "Read several live expressions at one instant, returning "
        "`{timestamp, application_state, values}` — a monitoring snapshot. "
        "For a struct or array, list each member/element expression "
        "(`PLC_PRG.st.member`, `arr[1]`); SP22 returns one string per "
        "expression. Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "expression": {"type": "string"},
            "expressions": {"type": "array", "items": {"type": "string"}},
            **_common(),
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "online.snapshot", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.forced",
    description=(
        "List the expressions currently FORCED and PREPARED on the target "
        "(read-only). Forces override program logic on live outputs, so check "
        "this before a run; release them with `online.unforce_all`."
    ),
    input_schema={"type": "object", "properties": _common(), "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "online.forced", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.create_boot",
    description="Build the boot application on the target device.",
    input_schema={"type": "object", "properties": _common(), "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "online.create_boot", args, 120.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.online.source_download",
    description="Embed the project source archive on the target device.",
    input_schema={"type": "object", "properties": _common(), "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "online.source_download", args, 600.0),
))
