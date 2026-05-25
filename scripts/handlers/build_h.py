# -*- coding: utf-8 -*-
"""
Build / compile handlers.

Ops:
  build.build              incremental build of an application
  build.rebuild            full rebuild
  build.clean              drop compile output
  build.clean_all          project-wide
  build.generate_code      codegen pass
  build.messages           read recent build messages (errors/warnings/info)
"""
import _registry
import _codesys_helpers as h

# Well-known message-category GUIDs on SP22 (discovered at runtime via
# the introspection probes — system.get_message_categories() returns
# System.Guid instances, NOT category objects with methods. To fetch
# messages from a specific category we pass the GUID into
# system.get_message_objects(guid) or system.get_messages(guid). The
# Build category contains the actual compile errors that show up in
# the IDE's Messages window.
_BUILD_CATEGORY_GUID_STR = "97f48d64-a2a3-4856-b640-75c046e37ea9"


def _build_guid():
    """System.Guid for the Build category. Returns None if CLR not available."""
    try:
        import System
        return System.Guid(_BUILD_CATEGORY_GUID_STR)
    except Exception:
        return None


def _clear_build_messages():
    """Clear messages in the Build category specifically (not Guid.Empty,
    which would also wipe library / device / MCP-log entries we want to
    keep across calls)."""
    g = _build_guid()
    if g is None:
        return False
    try:
        h.system_obj().clear_messages(g)
        return True
    except Exception:
        return False


# Backward-compat alias for callers we already wrote.
def _clear_all_messages():
    return _clear_build_messages()


# Object type GUIDs from the CODESYS Scripting API. Stable across SP versions.
# Verified at runtime via the project.tree output on Standard.project.
_TYPEGUID_APPLICATION = "ae1de277-a207-4a28-9efb-456c06bd52f3"


def _type_guid(obj):
    try:
        return str(getattr(obj, "type", "")).lower()
    except Exception:
        return ""


def _find_application(proj, name=None):
    """Return the build-capable ScriptApplication, or the one named explicitly.

    On SP22, walking the tree via `proj.get_children(False)` returns generic
    ScriptObject wrappers WITHOUT the application extension methods (no
    `build`, `rebuild`, `generate_code`). Calling those methods raises
    AttributeError. The properly-wrapped Application — with extension methods
    dispatched dynamically — is available as `proj.active_application`.

    Strategy:
      1. If `name` is None, return `proj.active_application` (set by the IDE
         when the project loaded; for a Standard.project there's exactly one).
      2. If `name` is given, walk the tree to locate the candidate by name +
         Application type GUID, then `proj.set_active_application(candidate)`,
         then return `proj.active_application`. This guarantees we hand back
         the build-capable wrapper, not the raw ScriptObject.
    """
    if not name:
        active = getattr(proj, "active_application", None)
        if active is None:
            raise RuntimeError(
                "No active application on the project. Open a project with at "
                "least one Application object, or pass `application=...` to "
                "select one explicitly."
            )
        return active

    # Specific application requested — find by name + type GUID, then activate.
    found = []

    def visit(obj):
        try:
            children = list(obj.get_children(False))
        except Exception:
            children = []
        for ch in children:
            if (not getattr(ch, "is_folder", False)
                    and _type_guid(ch) == _TYPEGUID_APPLICATION
                    and h._safe_name(ch) == name):
                found.append(ch)
            visit(ch)

    visit(proj)
    if not found:
        raise RuntimeError("Application not found: " + name)
    try:
        proj.set_active_application(found[0])
    except Exception as exc:
        raise RuntimeError("set_active_application failed: " + str(exc))
    return proj.active_application


def _classify_severity(sev):
    """Classify a CLR Severity value into one of:
      - "error"   (Error, FatalError)
      - "warning" (Warning)
      - "info"    (Information)
      - "status"  (Text — section headers / progress lines like
                  "Build started: ...", "Typify code...")

    The IDE renders Severity.Text differently from Information (no icon,
    used for build log section markers). We treat it as status so it
    doesn't inflate info-message counts.

    Also handles SuppressedInformation / SuppressedWarning, which the IDE
    hides by default — return them as "info" / "warning" but tag them
    via the display string.
    """
    try:
        Sev = h._g("Severity")
    except Exception:
        Sev = None
    s_str = str(sev).lower()
    if Sev is not None:
        try:
            if sev == Sev.Error or sev == Sev.FatalError:
                return ("error", str(sev))
        except Exception:
            pass
        try:
            if sev == Sev.Warning or sev == Sev.SuppressedWarning:
                return ("warning", str(sev))
        except Exception:
            pass
        try:
            if sev == Sev.Information or sev == Sev.SuppressedInformation:
                return ("info", str(sev))
        except Exception:
            pass
        try:
            if sev == Sev.Text:
                return ("status", str(sev))
        except Exception:
            pass
    # Fallback to string match.
    if "fatal" in s_str or "error" in s_str:
        return ("error", str(sev))
    if "warn" in s_str:
        return ("warning", str(sev))
    if s_str == "text":
        return ("status", str(sev))
    return ("info", str(sev))


def _summarize_message(m):
    """Pull a structured dict out of a SP22 IScriptMessage. Fields are best-
    effort — older SPs returned strings here, so be defensive."""
    if isinstance(m, str):
        # `system.get_messages()` returns formatted strings (deprecated path).
        return {"severity": "info", "text": m, "prefix": "", "position": ""}
    sev = getattr(m, "severity", "")
    text = getattr(m, "text", "") or ""
    prefix = getattr(m, "prefix", "") or ""
    position_text = getattr(m, "position_text", "") or ""
    number = getattr(m, "number", None)
    sev_kind, sev_display = _classify_severity(sev)
    try:
        # Resolve the source object's name when available.
        src_obj = getattr(m, "object", None)
        src_name = h._safe_name(src_obj) if src_obj is not None else ""
    except Exception:
        src_name = ""
    return {
        "severity": sev_kind,
        "severity_raw": sev_display,
        "text": str(text),
        "prefix": str(prefix),
        "position": str(position_text),
        "number": _coerce_int(number),
        "source": src_name,
    }


def _coerce_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None


def _build_summary(messages):
    err = 0
    warn = 0
    out = []
    for m in messages:
        info = _summarize_message(m)
        if info["severity"] == "error":
            err += 1
        elif info["severity"] == "warning":
            warn += 1
        out.append(info)
    return {"errors": err, "warnings": warn, "messages": out}


def _drain_messages():
    """Collect build/compile messages from CODESYS's message service.

    SP22 facts (verified at runtime, see CHANGES.md):
      - `system.get_message_service()` does NOT exist; methods are direct on
        `system`.
      - `system.get_message_categories()` returns System.Guid INSTANCES, not
        category objects. Calling .get_messages() on a Guid fails silently
        (which is why earlier versions of this code returned 0 messages
        even when builds were emitting real errors).
      - The correct API is `system.get_message_objects(category_guid)`
        which returns structured IScriptMessage objects. The Build
        category GUID is 97f48d64-a2a3-4856-b640-75c046e37ea9 and contains
        the actual compile errors that show in the IDE Messages window.
      - `system.get_message_objects()` with no args returns messages from
        the "default" category (typically empty or status-only — not
        where the compiler writes errors).
    """
    system_obj = h.system_obj()
    g = _build_guid()
    # Preferred path: query the Build category by GUID. This is where
    # the compiler actually writes errors/warnings.
    if g is not None:
        try:
            msgs = list(system_obj.get_message_objects(g))
            if msgs:
                return msgs
        except Exception:
            pass
        # String fallback within Build category.
        try:
            return list(system_obj.get_messages(g))
        except Exception:
            pass
    # Last resort: parameter-less get_message_objects (may be empty).
    try:
        return list(system_obj.get_message_objects())
    except Exception:
        return []


@_registry.handler("build.build")
def op_build(args):
    proj = h.resolve_project(args)
    app = _find_application(proj, args.get("application"))
    _clear_all_messages()
    app.build()
    return _build_summary(_drain_messages())


@_registry.handler("build.rebuild")
def op_rebuild(args):
    proj = h.resolve_project(args)
    app = _find_application(proj, args.get("application"))
    _clear_all_messages()
    app.rebuild()
    return _build_summary(_drain_messages())


@_registry.handler("build.clean")
def op_clean(args):
    proj = h.resolve_project(args)
    app = _find_application(proj, args.get("application"))
    app.clean()
    return {"cleaned": True, "application": h._safe_name(app)}


@_registry.handler("build.clean_all")
def op_clean_all(args):
    proj = h.resolve_project(args)
    proj.clean_all()
    return {"cleaned_all": True}


@_registry.handler("build.generate_code")
def op_generate(args):
    proj = h.resolve_project(args)
    app = _find_application(proj, args.get("application"))
    _clear_all_messages()
    app.generate_code()
    return _build_summary(_drain_messages())


@_registry.handler("build.messages")
def op_messages(args):
    return _build_summary(_drain_messages())
