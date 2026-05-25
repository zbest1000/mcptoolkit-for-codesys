"""System-level MCP tools that run host-side (no watcher / IDE involved).

These wrap the CODESYS APInstaller CLI for package installation / listing,
which is the canonical way to add devices, libraries, and IDE features.
Pure subprocess — much more reliable than driving the IDE installer GUI.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from . import REGISTRY, ToolContext, ToolSpec, format_result
from .._validation import ValidationError


def _validation_error(message: str, *, field: str | None = None) -> str:
    return format_result({
        "status": "error",
        "error": message,
        "error_kind": "ValidationError",
        "data": {"field": field} if field else {},
    })


# Hardcoded fallback for the APInstaller CLI location. The Phase 1 install
# discovery in watcher_manager hits the same path; if it works there, it
# works here. Same env var override semantics for consistency.
def _find_apinstaller() -> Path | None:
    import os as _os
    explicit = _os.environ.get("CODESYS_APINSTALLER")
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
    standard = Path(r"C:\Program Files (x86)\CODESYS\APInstaller\APInstaller.CLI.exe")
    if standard.exists():
        return standard
    return None


async def _run_apinstaller(args: list[str], timeout_s: float = 120.0) -> dict:
    """Run APInstaller.CLI with the given args. Returns
    {returncode, stdout, stderr, cmd, timed_out}.
    """
    cli = _find_apinstaller()
    if cli is None:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": "APInstaller.CLI.exe not found at the standard path "
                      "(C:\\Program Files (x86)\\CODESYS\\APInstaller\\APInstaller.CLI.exe) "
                      "and CODESYS_APINSTALLER env var not set.",
            "cmd": [],
            "timed_out": False,
        }
    cmd = [str(cli)] + args
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "returncode": -1, "stdout": "", "stderr": "",
                "cmd": cmd, "timed_out": True,
            }
        return {
            "returncode": proc.returncode if proc.returncode is not None else -1,
            "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
            "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
            "cmd": cmd,
            "timed_out": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": "subprocess launch failed: " + str(exc),
            "cmd": cmd,
            "timed_out": False,
        }


# ---------------------------------------------------------------------------


async def _list_installations_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Wrapper around `APInstaller.CLI --getInstallations`. Mirrors what the
    Phase 1 startup probe does, exposed as a tool so the LLM can re-probe
    after installing packages."""
    out = await _run_apinstaller(["--getInstallations"], timeout_s=30.0)
    parsed: Any = None
    parse_err = None
    if out["returncode"] == 0:
        try:
            parsed = json.loads(out["stdout"])
        except Exception as exc:
            parse_err = "JSON parse failed: " + str(exc)
    return format_result({
        "status": "ok" if out["returncode"] == 0 and parse_err is None else "error",
        "returncode": out["returncode"],
        "installations": parsed,
        "parse_error": parse_err,
        "stderr": out["stderr"][:2000],
        "timed_out": out["timed_out"],
    })


REGISTRY.register(ToolSpec(
    name="codesys.system.list_installations",
    description=(
        "Run `APInstaller.CLI --getInstallations` and return the parsed "
        "list of installed CODESYS Development System instances on this "
        "machine. Each entry includes the install path, SP/patch version, "
        "and profile file. Useful for verifying which install the server "
        "is currently driving (compare against `codesys.info`) and for "
        "spotting older instances that may have stale device packages."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    handler=_list_installations_handler,
))


async def _install_package_handler(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Install a CODESYS package (.package file) into an existing install.

    The IDE's "CODESYS Installer..." dialog wraps the same CLI we're
    invoking here. After install, you typically need to restart CODESYS
    for the new devices/libraries to surface — call
    `build.force_recompile` or restart Claude Desktop.
    """
    path = args.get("path")
    if not path or not isinstance(path, str):
        return _validation_error("'path' is required and must be a string", field="path")
    p = Path(path)
    if not p.is_absolute():
        return _validation_error("'path' must be absolute", field="path")
    if not p.exists():
        return _validation_error("package file does not exist: {}".format(p), field="path")
    if p.suffix.lower() not in (".package", ".cdsv3pkg"):
        return _validation_error(
            "expected .package or .cdsv3pkg; got {!r}".format(p.suffix),
            field="path",
        )

    # APInstaller flags: vary by version. `--install` is the modern flag;
    # `--installpackage` is the legacy alias.
    out = await _run_apinstaller(["--install", str(p)], timeout_s=600.0)
    if out["returncode"] != 0:
        # Retry with legacy flag name.
        out_alt = await _run_apinstaller(["--installpackage", str(p)], timeout_s=600.0)
        if out_alt["returncode"] == 0:
            out = out_alt
    return format_result({
        "status": "ok" if out["returncode"] == 0 else "error",
        "returncode": out["returncode"],
        "package": str(p),
        "stdout": out["stdout"][:4000],
        "stderr": out["stderr"][:2000],
        "timed_out": out["timed_out"],
        "note": (
            "Restart CODESYS (e.g. via build.force_recompile) for newly "
            "installed devices/libraries to be visible to the watcher."
        ),
    })


REGISTRY.register(ToolSpec(
    name="codesys.system.install_package",
    description=(
        "Install a CODESYS package (.package / .cdsv3pkg) via "
        "APInstaller.CLI. Adds devices, libraries, IDE add-ons, etc. The "
        "package file usually comes from CODESYS Store or a vendor's "
        "device-package release. After install, CODESYS needs to be "
        "restarted for the new content to surface — kick "
        "`build.force_recompile` to bounce the watcher's CODESYS instance."
    ),
    input_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to a .package or .cdsv3pkg file.",
            },
        },
        "additionalProperties": False,
    },
    handler=_install_package_handler,
))
