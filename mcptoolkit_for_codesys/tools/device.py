"""Device Repository MCP tools.

Discovery and metadata for devices installed in the system device
repository. Direct programmatic addition is NOT exposed (the SP22
script API has no `add_device` method on any tree node); for adding
devices use `project.import_xml` with a pre-baked PLCopenXML snippet,
or install missing device packages via `system.install_package`.
"""
from __future__ import annotations

from typing import Any

from . import REGISTRY, ToolContext, ToolSpec, format_result


async def _call(ctx: ToolContext, op: str, args: dict[str, Any], timeout: float) -> str:
    return format_result(await ctx.ipc.call(op, args, timeout_s=timeout))


REGISTRY.register(ToolSpec(
    name="codesys.device.list_installed",
    description=(
        "Enumerate devices in the system device repository (typically 3000+ "
        "entries on a base SP22 install — Modbus masters/slaves, EtherNet/"
        "IP scanners/adapters, EtherCAT slaves, PROFINET devices, Soft PLCs, "
        "etc.). Filter by `name`/`vendor`/`description` (case-insensitive "
        "substrings), `family`, or `category` (integer id from "
        "`device.categories`). Pass `keywords` as a list to require ALL "
        "to match across name+vendor+description. Each result includes the "
        "DeviceID (type/id/version) you need to build a PLCopenXML import."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Substring match on device_info.name."},
            "vendor": {"type": "string", "description": "Substring match on vendor."},
            "description": {"type": "string", "description": "Substring match on description."},
            "family": {"type": "string", "description": "Substring match against families."},
            "category": {
                "type": "integer",
                "description": "Exact category id; see `device.categories`.",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "All must match against name+vendor+description "
                    "combined (case-insensitive)."
                ),
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "minimum": 1,
                "maximum": 500,
            },
            "resolve_categories": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Include resolved category info ({id, name, description}) "
                    "in each result. Set False for a leaner payload."
                ),
            },
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "device.list_installed", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.device.categories",
    description=(
        "List device categories visible in the repository. The repo "
        "doesn't expose a direct enumeration, so the watcher walks the "
        "device list, gathers unique category ids, and resolves each "
        "through `get_device_category`. Use the returned ids with "
        "`device.list_installed { category: <id> }`."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    handler=lambda ctx, args: _call(ctx, "device.categories", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.device.tree",
    description=(
        "List every device node in the open project with its current "
        "DeviceId (type/id/version) and tree path. This is the starting "
        "point for `device.update` (what's plugged where, at which version) "
        "and for choosing a `parent` for `device.add`. Many projects' build "
        "errors trace to an old device version dragging in an uninstalled "
        "library — this surfaces those versions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "Target an already-open project by path; default primary.",
            },
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "device.tree", args, 30.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.device.add",
    description=(
        "Add a device to the project tree. Resolve the device either by "
        "`device_name` (substring; highest installed version is chosen unless "
        "`version` is given) or by an explicit `type`+`id`(+`version`) DeviceId "
        "triple from `device.list_installed`. `name` is the new node's instance "
        "name. `parent` is the tree path to add under (default: the single root "
        "PLC node). NOTE: fieldbus devices (e.g. an EtherNet/IP scanner) must be "
        "added under a compatible bus/adapter node, not directly under the PLC — "
        "check `device.tree` for the structure and pass `parent` accordingly."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Instance name for the new node."},
            "device_name": {
                "type": "string",
                "description": "Repo device name (substring) to resolve the DeviceId.",
            },
            "type": {"type": "integer", "description": "Explicit DeviceId type."},
            "id": {"type": "string", "description": "Explicit DeviceId id (e.g. '0000 100B')."},
            "version": {"type": "string", "description": "Specific version; default highest."},
            "parent": {
                "type": "string",
                "description": (
                    "Tree path of the node to add under; default the root PLC. "
                    "Use '/' (or 'root'/'project') to add a top-level PLC at the "
                    "project root."
                ),
            },
            "module": {"type": "string", "description": "Optional module string (default '')."},
            "project_path": {"type": "string"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
    # Device add/reconfig triggers a library reload and can take well over a
    # minute on the first add; give it room before declaring a timeout.
    handler=lambda ctx, args: _call(ctx, "device.add", args, 180.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.device.parameters",
    description=(
        "List a device's parameters across its connectors — both configuration "
        "parameters (IPAddress, SubnetMask, DeviceName, comms settings) and I/O "
        "channels (`is_mappable_io: true`, with `channel_type` and whether "
        "they're `mapped`). Filter by `name` (substring) or `mappable_only`. "
        "Use this to read current device config and to discover I/O channels "
        "before binding them. `target` is the device tree path (default the "
        "root PLC)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Device tree path; default root PLC."},
            "name": {"type": "string", "description": "Substring filter on parameter name."},
            "mappable_only": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 200, "minimum": 1, "maximum": 1000},
            "project_path": {"type": "string"},
        },
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "device.parameters", args, 60.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.device.set_parameter",
    description=(
        "Set a device parameter's value by name — e.g. configure a fieldbus "
        "adapter's `IPAddress` to `\"[192, 168, 0, 10]\"`, `DeviceName` to "
        "`\"'plc1'\"`, or `WebServer` to `\"true\"`. The `value` is the "
        "parameter's textual representation; call `device.parameters` first to "
        "see the exact current format. `target` is the device tree path "
        "(default the root PLC); `connector` disambiguates if the same name "
        "appears on multiple connectors. Returns before/after."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Device tree path; default root PLC."},
            "name": {"type": "string", "description": "Parameter name (exact)."},
            "value": {"type": "string", "description": "New value, in the parameter's textual format."},
            "connector": {"type": "string", "description": "Connector id, if name is ambiguous."},
            "project_path": {"type": "string"},
        },
        "required": ["name", "value"],
        "additionalProperties": False,
    },
    handler=lambda ctx, args: _call(ctx, "device.set_parameter", args, 60.0),
))


REGISTRY.register(ToolSpec(
    name="codesys.device.update",
    description=(
        "Change a device node's version (or swap its type/id). The fix for "
        "device-descriptor library pins: an old device version (e.g. PLCWinNT "
        "3.1.3.0) references an old library (IoStandard 3.1.3.1) that may not be "
        "installed, breaking the build. Updating the device to a newer installed "
        "version re-points it at current libraries. `target` is the device tree "
        "path (default: the single root PLC); `version` defaults to the highest "
        "installed for the same type+id. Pair with `device.tree` to find targets "
        "and `build.build` to confirm the fix."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Tree path of the device node; default the root PLC.",
            },
            "version": {
                "type": "string",
                "description": "Desired version; default highest installed for type+id.",
            },
            "type": {"type": "integer", "description": "Optional — swap to a different device type."},
            "id": {"type": "string", "description": "Optional — swap to a different device id."},
            "module": {"type": "string", "description": "Optional module string (default '')."},
            "project_path": {"type": "string"},
        },
        "additionalProperties": False,
    },
    # Updating a device version reconfigures its whole subtree + reloads
    # libraries — this is slow (observed >60s on PLCWinNT). Allow 180s.
    handler=lambda ctx, args: _call(ctx, "device.update", args, 180.0),
))
