# -*- coding: utf-8 -*-
"""
Tiny helpers that wrap awkward parts of the CODESYS Scripting API.

IronPython 2.7. No f-strings, no type hints, no Pydantic.

The global names `system`, `projects`, `online`, `device_repository`, ... are
injected by the CODESYS script host into the RUNSCRIPT's module globals
(i.e., watcher.py's globals). Imported modules don't share that namespace —
they only see __builtin__, which has just stdlib names. So at startup
watcher.py calls `register_injected(globals())` and `_g` looks names up
there first.
"""

import codecs
import json
import locale
import os

try:
    import __builtin__ as _b
except ImportError:  # CPython 3 — lets the host-side tests import this module
    import builtins as _b

# Populated by watcher.py at startup via register_injected(globals()).
_INJECTED = {}


def register_injected(g):
    """Capture the runscript's globals dict so helpers can see CODESYS-injected names.

    `g` is the watcher module's globals() dict. We hold a reference (not a
    copy), so anything CODESYS adds to those globals after this call is also
    visible. Safe to call multiple times.
    """
    global _INJECTED
    _INJECTED = g


def _g(name):
    """Get a CODESYS-injected global by name, or raise a clean error."""
    if _INJECTED and name in _INJECTED:
        return _INJECTED[name]
    if hasattr(_b, name):
        return getattr(_b, name)
    # Actionable diagnostic: show what IS visible so we can spot rename/drift.
    visible = sorted(k for k in _INJECTED if not k.startswith("_")) if _INJECTED else []
    raise RuntimeError(
        "CODESYS global '" + name + "' not found. "
        "Did watcher.py call _codesys_helpers.register_injected(globals())? "
        "Visible injected names: " + (", ".join(visible) if visible else "(none)")
    )


def list_injected():
    """Return the names of all currently-registered injected globals (diagnostic)."""
    return sorted(k for k in _INJECTED if not k.startswith("_"))


# ---------------------------------------------------------------------------
# JSON-serialization safety
# ---------------------------------------------------------------------------

# IronPython 2.7 has two string types: `str` (bytes) and `unicode` (text).
# CPython 3 has only `str` (text) plus `bytes`. Resolve both once.
try:
    _TEXT_TYPE = unicode  # noqa: F821 — IronPython 2.7 builtin
except NameError:
    _TEXT_TYPE = str

try:
    _BINARY_TYPE = bytes
except NameError:  # pragma: no cover — py2 always has bytes
    _BINARY_TYPE = str


def _locale_encoding():
    """The process ANSI code page (cp1252 on a Spanish Windows)."""
    try:
        enc = locale.getpreferredencoding(False)
    except Exception:
        enc = ""
    return enc or "latin-1"


def safe_text(value):
    """Return a string that survives a JSON round-trip.

    On CPython this normalizes `bytes` to text. On IronPython 2.7 `str` and
    `unicode` are the SAME type (both are System.String) while `bytes` is a
    distinct type, so bytes still need decoding and text is left alone.

    The real protection for non-ASCII characters is the serializer —
    `json_payload` below dumps with ensure_ascii=False and writes UTF-8.
    Without that, IronPython's json.dumps decodes every string as UTF-8 and
    raises UnicodeDecodeError on the first non-ASCII character, silently
    killing the whole response.
    """
    if isinstance(value, _TEXT_TYPE):
        return value
    if isinstance(value, _BINARY_TYPE):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            pass
        for enc in (_locale_encoding(), "latin-1"):
            try:
                return value.decode(enc, "replace")
            except (LookupError, UnicodeDecodeError):
                continue
        return repr(value)
    try:
        return str(value)
    except Exception:
        return repr(value)


# ---------------------------------------------------------------------------
# File / JSON plumbing
# ---------------------------------------------------------------------------
#
# ENCODING CONTRACT (host <-> watcher):
#   host -> watcher : UTF-8   (commands/*.json)
#   watcher -> host : UTF-8   (results/*.json, watcher.ready, watcher.heartbeat)
#   log             : UTF-8   (log/watcher.log)
#
# The IronPython 2.7 facts that break this contract if you ignore them:
#   - `str is unicode` (both are System.String), but `bytes is not str`.
#   - `open(path, "r")` decodes with the process ANSI code page (cp1252 on a
#     Spanish Windows) — NOT UTF-8. Reading a host-written command file that
#     way silently mangles every accented character in the arguments.
#   - `json.dumps(..., ensure_ascii=True)` (the default) decodes every string
#     as UTF-8 before escaping it, so the first non-ASCII character raises
#     UnicodeDecodeError and the whole response is lost.
#   - `str.encode("utf-8")` returns a `str`, not `bytes` — so the payload is
#     built with `bytes(text, "utf-8")`, which is real bytes on both runtimes.


def json_payload(obj):
    """Serialize `obj` to a UTF-8 JSON payload (real bytes on both runtimes)."""
    text = json.dumps(obj, ensure_ascii=False)
    if isinstance(text, _BINARY_TYPE) and not isinstance(text, _TEXT_TYPE):
        return text
    try:
        return bytes(text, "utf-8")
    except UnicodeEncodeError:
        # Lone surrogates (a non-BMP character that JSON split into a pair).
        # Drop them instead of losing the whole response.
        return bytes(text.encode("utf-8", "replace"), "utf-8")


def atomic_write(path, payload):
    """Write `payload` (UTF-8 bytes) to `path` atomically.

    The payload is written verbatim in binary mode. Re-encoding here would
    double-encode the bytes.
    """
    tmp = path + ".tmp"
    f = open(tmp, "wb")
    try:
        f.write(payload)
    finally:
        f.close()
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def read_json(path):
    """Read a UTF-8 JSON file. Returns None on any failure.

    MUST decode as UTF-8: the host writes command files as UTF-8, and
    `open(path, "r")` on IronPython decodes with the ANSI code page, which
    silently corrupts accented characters in the arguments.
    """
    try:
        f = codecs.open(path, "r", "utf-8")
        try:
            raw = f.read()
        finally:
            f.close()
    except (IOError, OSError):
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def coerce_for_json(value):
    """Recursively coerce IronPython-isms into json-serializable Python.

    IronPython 2.7 sometimes returns .NET integer types (`long`, `Int64`, etc.)
    that Python's stdlib `json` cannot serialize natively. The CODESYS message
    service in particular returns severity bitmasks as `long` on some SPs.
    This walker converts anything `int`/`float`/`str`/`bool`/None straight
    through, recurses into list/tuple/dict, and falls back to `str(value)` for
    anything else (CLR objects, COM proxies, ...).

    Safe to apply at the result-serialization boundary; cost is O(n) walk and
    we accept that for the safety it buys us.
    """
    # Fast path for cheap immutable types.
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    # IronPython 2.7 `long` — not the same isinstance class on .NET hosts; the
    # int branch above catches CPython longs. Try a numeric cast.
    try:
        # Catches IronPython `long`, `System.Int64`, etc.
        if isinstance(value, long):  # noqa: F821 — IronPython 2.7 builtin
            return int(value)
    except NameError:
        # CPython 3 — `long` doesn't exist. The int branch above handled it.
        pass
    # Strings: in IronPython `str` and `unicode` are the SAME type (both are
    # System.String), so there is no byte/text distinction to act on here —
    # the serializer (watcher._json_payload) is what keeps non-ASCII
    # characters from killing the response. safe_text normalizes them anyway.
    if isinstance(value, _TEXT_TYPE) or isinstance(value, _BINARY_TYPE):
        return safe_text(value)
    if isinstance(value, dict):
        return dict((k, coerce_for_json(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return [coerce_for_json(v) for v in value]
    # Unknown CLR / COM / opaque object — render to string so the LLM sees
    # something rather than a JSON encode error. `str()` on a CLR object gives
    # ANSI bytes in IronPython, so run it through safe_text as well.
    try:
        return safe_text(str(value))
    except Exception:
        return repr(value)


def projects_obj():
    return _g("projects")


def system_obj():
    return _g("system")


def online_obj():
    return _g("online")


def device_repository_obj():
    return _g("device_repository")


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


def primary_project():
    p = projects_obj().primary
    if p is None:
        raise RuntimeError(
            "No primary (active) project. Open a project first via "
            "projects.open(path) or use the 'codesys.project.open' tool."
        )
    return p


def find_project_by_path(path):
    """Return an already-open project by file path, or None."""
    try:
        return projects_obj().get_by_path(path)
    except Exception:
        return None


def resolve_project(args):
    """Pick which project a command targets.

    Lookup order:
      1. args['project_path'] -> open project at that path (must already be open)
      2. primary project
    """
    p = args.get("project_path")
    if p:
        proj = find_project_by_path(p)
        if proj is None:
            raise RuntimeError("Project not open: " + p)
        return proj
    return primary_project()


# ---------------------------------------------------------------------------
# Tree object resolution
# ---------------------------------------------------------------------------


# Stable type GUIDs of objects that LOOK like POUs by name but aren't.
# A "task-reference" object appears under MainTask in a Standard.project and
# has the same name as the POU it references — this would otherwise make
# pou.set_text("PLC_PRG") ambiguous.
_TYPEGUID_TASK_REFERENCE = "413e2a7d-adb1-4d2c-be29-6ae6e4fab820"


def _is_textual(obj):
    """A textual code-bearing object exposes textual_declaration and/or
    textual_implementation. POUs, DUTs, GVLs match; task references and
    folders don't."""
    return (hasattr(obj, "textual_declaration")
            or hasattr(obj, "textual_implementation"))


def _type_guid(obj):
    try:
        return str(getattr(obj, "type", "")).lower()
    except Exception:
        return ""


def find_object(proj, name_or_path, prefer_textual=True):
    """Find a tree object by name (case-sensitive) or '/'-separated path.

    Falls back to ScriptObject.find which already does recursive name search.

    When `prefer_textual=True` (the default), an ambiguous name match is
    resolved in favor of objects that look textual (POU/DUT/GVL — anything
    with textual_declaration/textual_implementation). Task references that
    appear under MainTask shadow the POU they reference; this filter drops
    them so callers like `pou.set_text("PLC_PRG")` work without forcing a
    full path. Set `prefer_textual=False` if you specifically want the
    legacy strict-ambiguity behavior.
    """
    if not name_or_path:
        raise RuntimeError("find_object: empty name_or_path")

    if "/" in name_or_path:
        return _find_by_path(proj, name_or_path)

    matches = list(proj.find(name_or_path, True))
    if not matches:
        raise RuntimeError("Object not found: " + name_or_path)
    if len(matches) == 1:
        return matches[0]

    if prefer_textual:
        # First filter: drop known non-textual reference types (task refs).
        filtered = [m for m in matches
                    if _type_guid(m) != _TYPEGUID_TASK_REFERENCE]
        # Second filter: keep only ones that actually have text attributes.
        textual = [m for m in filtered if _is_textual(m)]
        if len(textual) == 1:
            return textual[0]
        if len(filtered) == 1:
            return filtered[0]
        # Still ambiguous after filtering — fall through to error.

    names = [_safe_name(m) + " [" + _type_guid(m)[:8] + "]" for m in matches]
    raise RuntimeError(
        "Ambiguous name '" + name_or_path + "' matches " + str(len(matches))
        + " objects: " + ", ".join(names) + ". Use a '/' path instead."
    )


def _find_by_path(proj, path):
    """Resolve a '/'-separated path to a tree object.

    The first component is looked up RECURSIVELY anywhere in the project
    (so `FB_Counter/Current/Get` works even when FB_Counter is nested
    under a folder). Subsequent components are direct-child lookups from
    the previous match. Use a leading '/' to force root-relative semantics
    when you specifically want disambiguation.

    Examples:
      "FB_Counter/Current/Get"            -> finds FB_Counter anywhere
      "/POUs/FB_Counter"                  -> root-relative: POUs at root only
      "PLCWinNT/Plc Logic/Application/PLC_PRG"  -> works either way (PLCWinNT is at root)
    """
    root_relative = path.startswith("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        raise RuntimeError("Empty path")

    # Locate the first component.
    if root_relative:
        # Direct child of project root only.
        current = None
        for child in proj.get_children(False):
            if _safe_name(child) == parts[0]:
                current = child
                break
        if current is None:
            raise RuntimeError(
                "Path component not found at root: '" + parts[0] + "' in '" + path + "'"
            )
    else:
        # Recursive find — first match wins, with task-reference filtering
        # already baked into find_object's contract (use _is_textual + the
        # task-reference type guid blacklist when the name is ambiguous).
        matches = list(proj.find(parts[0], True))
        if not matches:
            raise RuntimeError(
                "Path component not found anywhere: '" + parts[0] + "' in '" + path + "'"
            )
        if len(matches) > 1:
            # Apply same disambiguation as find_object's name-only path.
            filtered = [m for m in matches
                        if _type_guid(m) != _TYPEGUID_TASK_REFERENCE]
            if len(filtered) == 1:
                current = filtered[0]
            else:
                # Still ambiguous. Caller needs root-relative path.
                names = [_safe_name(m) + " [" + _type_guid(m)[:8] + "]" for m in matches]
                raise RuntimeError(
                    "Ambiguous starting component '" + parts[0]
                    + "' matches " + str(len(matches)) + ": " + ", ".join(names)
                    + ". Prefix with '/' for root-relative."
                )
        else:
            current = matches[0]

    # Walk the rest as direct children.
    for part in parts[1:]:
        found = None
        try:
            children = list(current.get_children(False))
        except Exception:
            children = []
        for child in children:
            if _safe_name(child) == part:
                found = child
                break
        if found is None:
            raise RuntimeError(
                "Path component not found: '" + part
                + "' (under '" + _safe_name(current) + "') in '" + path + "'"
            )
        current = found
    return current


def _safe_name(obj):
    try:
        return obj.get_name()
    except Exception:
        try:
            return obj.name
        except Exception:
            return "<unnamed>"


# ---------------------------------------------------------------------------
# Implementation languages — string -> ScriptImplementationLanguage enum
# ---------------------------------------------------------------------------


def language_from_string(s):
    """Map a friendly string to the ImplementationLanguages enum.

    Accepts: st, ld, fbd, sfc, cfc, il, page_cfc, uml.

    Note: CODESYS V3.5 SP22 injects this enum as `ImplementationLanguages`
    (not `ScriptImplementationLanguage` as some older docs suggest). The
    enum's member names are lowercase identifiers — verified at runtime via
    the ping diagnostic.
    """
    s = (s or "st").strip().lower()
    try:
        lang = _g("ImplementationLanguages")
    except Exception:
        # Fallback for older SPs that may still use the legacy name.
        try:
            lang = _g("ScriptImplementationLanguage")
        except Exception:
            raise

    mapping = {
        "st": getattr(lang, "st", None),
        "structured_text": getattr(lang, "st", None),
        "ld": getattr(lang, "ladder", None),
        "ladder": getattr(lang, "ladder", None),
        "fbd": getattr(lang, "fbd", None),
        "sfc": getattr(lang, "sfc", None),
        "cfc": getattr(lang, "cfc", None),
        "il": getattr(lang, "instruction_list", None),
        "instruction_list": getattr(lang, "instruction_list", None),
        "page_cfc": getattr(lang, "page_oriented_cfc", None),
        "uml": getattr(lang, "uml_statechart", None),
    }
    val = mapping.get(s)
    if val is None:
        raise RuntimeError(
            "Unknown implementation language: " + repr(s)
            + ". Use one of: st, ld, fbd, sfc, cfc, il, page_cfc, uml."
        )
    return val


def pou_type_from_string(s):
    """Map a friendly string to PouType enum: program, function_block, function.

    Note: SP22's PouType uses PascalCase member names (`Program`,
    `FunctionBlock`, `Function`) — verified at runtime via the ping
    diagnostic. We accept lowercase strings from callers and translate.
    """
    s = (s or "program").strip().lower()
    pt = _g("PouType")
    mapping = {
        "program": getattr(pt, "Program", None),
        "prg": getattr(pt, "Program", None),
        "function_block": getattr(pt, "FunctionBlock", None),
        "fb": getattr(pt, "FunctionBlock", None),
        "function": getattr(pt, "Function", None),
        "fun": getattr(pt, "Function", None),
    }
    v = mapping.get(s)
    if v is None:
        raise RuntimeError(
            "Unknown POU type: " + repr(s) + ". Use program / function_block / function."
        )
    return v


# ---------------------------------------------------------------------------
# Textual editing — declaration + implementation
# ---------------------------------------------------------------------------


def set_text_object_content(text_obj, new_text):
    """Replace the contents of a textual object (declaration or implementation).

    The scripting docs use `replace()` on `textual_declaration` /
    `textual_implementation`. This wrapper centralizes the call so we can
    swap to a different mechanism if SP22 changes it.
    """
    if text_obj is None:
        raise RuntimeError("textual object is None (POU may be graphical, not textual)")
    text_obj.replace(new_text)


def get_text_object_content(text_obj):
    if text_obj is None:
        return ""
    try:
        return text_obj.text
    except Exception:
        # Some versions expose a `get_text` method instead.
        if hasattr(text_obj, "get_text"):
            return text_obj.get_text()
        return ""
