# -*- coding: utf-8 -*-
"""
Symbol Configuration handlers.

SP22 scripting exposes a single entry point for symbol config:

    app.create_symbol_config(export_comments_to_xml, support_opc_ua, layout_guid)

The created object is a plain tree node (type 21d4fe94-...) with no per-symbol
scripting API. WHICH symbols get exported is driven by the
`{attribute 'symbol' := 'read'|'write'|'readwrite'|'none'}` pragma on the
variable declaration (write it via pou.set_text); the configuration picks those
up at build time. So we expose create + list — symbol selection is a
code-attribute concern, not a scripted per-symbol one.

Ops:
  symbol.create_config   add a Symbol Configuration under the application
  symbol.list            list symbol configurations in the project
"""
import _registry
import _codesys_helpers as h

_APP_TYPE_GUID = "639b491f-5557-464c-af91-1471bac9f549"
_SYMBOL_CONFIG_TYPE_GUID = "21d4fe94-4123-4e23-9091-ead220afbd1f"


def _guid(obj):
    try:
        return str(getattr(obj, "guid", ""))
    except Exception:
        return ""


def _find_app(args):
    proj = h.resolve_project(args)
    apps = []

    def visit(o):
        try:
            children = list(o.get_children(False))
        except Exception:
            children = []
        for ch in children:
            if str(getattr(ch, "type", "")).lower() == _APP_TYPE_GUID:
                apps.append(ch)
            visit(ch)

    visit(proj)
    name = args.get("application")
    if name:
        for a in apps:
            if h._safe_name(a) == name:
                return a
        raise RuntimeError("Application not found: " + name)
    if not apps:
        raise RuntimeError("No Application object found in project tree.")
    if len(apps) > 1:
        names = [h._safe_name(a) for a in apps]
        raise RuntimeError(
            "Multiple applications (" + ", ".join(names) + "); pass 'application'."
        )
    return apps[0]


def _find_symbol_configs(app):
    out = []
    try:
        for ch in app.get_children(False):
            if str(getattr(ch, "type", "")).lower() == _SYMBOL_CONFIG_TYPE_GUID:
                out.append(ch)
    except Exception:
        pass
    return out


@_registry.handler("symbol.list")
def op_list(args):
    app = _find_app(args)
    cfgs = _find_symbol_configs(app)
    return {
        "application": h._safe_name(app),
        "count": len(cfgs),
        "symbol_configs": [
            {"name": h._safe_name(c), "guid": _guid(c)} for c in cfgs
        ],
    }


@_registry.handler("symbol.create_config")
def op_create_config(args):
    from System import Guid
    app = _find_app(args)
    existing = _find_symbol_configs(app)
    if existing and not args.get("force"):
        c = existing[0]
        return {
            "created": False,
            "existed": True,
            "name": h._safe_name(c),
            "guid": _guid(c),
            "note": ("a symbol configuration already exists; pass force=true "
                     "to add another"),
        }
    export_comments = bool(args.get("export_comments", True))
    support_opc_ua = bool(args.get("support_opc_ua", True))
    sc = app.create_symbol_config(export_comments, support_opc_ua, Guid.Empty)
    return {
        "created": True,
        "name": h._safe_name(sc),
        "guid": _guid(sc),
        "export_comments": export_comments,
        "support_opc_ua": support_opc_ua,
        "hint": ("Mark variables for export with the declaration pragma "
                 "{attribute 'symbol' := 'readwrite'} (or 'read'/'write'/'none'), "
                 "set via pou.set_text; the config collects them at build."),
    }
