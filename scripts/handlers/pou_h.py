# -*- coding: utf-8 -*-
"""
POU / DUT / GVL handlers.

Ops:
  pou.create        create a POU (Program / FB / Function) with a given language
  pou.create_dut    create a DUT (Struct / Enum / Union / Alias)
  pou.create_gvl    create a Global Variable List
  pou.set_text      replace declaration and/or implementation text
  pou.get_text      read declaration and/or implementation text
  pou.delete        remove a tree object
  pou.rename        rename a tree object
  pou.find          look up an object by name or '/'-path; return metadata
"""
import _registry
import _codesys_helpers as h


def _resolve_parent(proj, args):
    """Where to create the new object. Defaults to the project root."""
    parent_path = args.get("parent")
    if not parent_path:
        return proj
    return h.find_object(proj, parent_path)


def _summarize(obj):
    return {
        "name": h._safe_name(obj),
        "type": str(getattr(obj, "type", "")),
        "guid": str(getattr(obj, "guid", "")),
        "parent": h._safe_name(obj.parent) if getattr(obj, "parent", None) else None,
    }


@_registry.handler("pou.create")
def op_create_pou(args):
    name = args.get("name")
    if not name:
        raise RuntimeError("pou.create: 'name' is required")
    proj = h.resolve_project(args)
    parent = _resolve_parent(proj, args)

    pou_type = h.pou_type_from_string(args.get("pou_type", "program"))
    language = h.language_from_string(args.get("language", "st"))
    return_type = args.get("return_type")

    if str(pou_type).lower().endswith("function") and return_type:
        new_obj = parent.create_pou(name, pou_type, language, return_type)
    else:
        new_obj = parent.create_pou(name, pou_type, language)

    summary = _summarize(new_obj)
    declaration = args.get("declaration")
    implementation = args.get("implementation")
    if declaration is not None:
        h.set_text_object_content(getattr(new_obj, "textual_declaration", None), declaration)
    if implementation is not None:
        h.set_text_object_content(getattr(new_obj, "textual_implementation", None), implementation)
    summary["declaration_set"] = declaration is not None
    summary["implementation_set"] = implementation is not None
    return summary


@_registry.handler("pou.create_dut")
def op_create_dut(args):
    name = args.get("name")
    if not name:
        raise RuntimeError("pou.create_dut: 'name' is required")
    proj = h.resolve_project(args)
    parent = _resolve_parent(proj, args)

    dut_kind = (args.get("kind") or "structure").strip().lower()
    try:
        DutType = h._g("DutType")
    except Exception:
        raise RuntimeError("DutType enum not found in this SP. Pass kind=structure or upgrade.")

    # SP22 DutType uses PascalCase member names (Alias, Enumeration,
    # EnumerationWithTextList, Structure, Union) — confirmed at runtime via
    # the ping diagnostic.
    mapping = {
        "structure": getattr(DutType, "Structure", None),
        "struct": getattr(DutType, "Structure", None),
        "enumeration": getattr(DutType, "Enumeration", None),
        "enum": getattr(DutType, "Enumeration", None),
        "enumeration_with_textlist": getattr(DutType, "EnumerationWithTextList", None),
        "enum_textlist": getattr(DutType, "EnumerationWithTextList", None),
        "union": getattr(DutType, "Union", None),
        "alias": getattr(DutType, "Alias", None),
    }
    kind = mapping.get(dut_kind)
    if kind is None:
        raise RuntimeError("Unknown DUT kind: " + repr(dut_kind))

    new_obj = parent.create_dut(name, kind)
    summary = _summarize(new_obj)
    declaration = args.get("declaration")
    if declaration is not None:
        h.set_text_object_content(getattr(new_obj, "textual_declaration", None), declaration)
        summary["declaration_set"] = True
    return summary


@_registry.handler("pou.create_gvl")
def op_create_gvl(args):
    name = args.get("name")
    if not name:
        raise RuntimeError("pou.create_gvl: 'name' is required")
    proj = h.resolve_project(args)
    parent = _resolve_parent(proj, args)
    new_obj = parent.create_gvl(name)
    summary = _summarize(new_obj)
    declaration = args.get("declaration")
    if declaration is not None:
        h.set_text_object_content(getattr(new_obj, "textual_declaration", None), declaration)
        summary["declaration_set"] = True
    return summary


@_registry.handler("pou.set_text")
def op_set_text(args):
    target = args.get("target")
    if not target:
        raise RuntimeError("pou.set_text: 'target' is required")
    proj = h.resolve_project(args)
    obj = h.find_object(proj, target)

    declaration = args.get("declaration")
    implementation = args.get("implementation")
    if declaration is None and implementation is None:
        raise RuntimeError("pou.set_text: provide at least one of declaration / implementation")

    out = {"name": h._safe_name(obj)}
    if declaration is not None:
        h.set_text_object_content(getattr(obj, "textual_declaration", None), declaration)
        out["declaration_set"] = True
    if implementation is not None:
        h.set_text_object_content(getattr(obj, "textual_implementation", None), implementation)
        out["implementation_set"] = True
    return out


@_registry.handler("pou.get_text")
def op_get_text(args):
    target = args.get("target")
    if not target:
        raise RuntimeError("pou.get_text: 'target' is required")
    proj = h.resolve_project(args)
    obj = h.find_object(proj, target)
    return {
        "name": h._safe_name(obj),
        "declaration": h.get_text_object_content(getattr(obj, "textual_declaration", None)),
        "implementation": h.get_text_object_content(getattr(obj, "textual_implementation", None)),
    }


@_registry.handler("pou.delete")
def op_delete(args):
    target = args.get("target")
    if not target:
        raise RuntimeError("pou.delete: 'target' is required")
    proj = h.resolve_project(args)
    obj = h.find_object(proj, target)
    name = h._safe_name(obj)
    obj.remove()
    return {"deleted": True, "name": name}


@_registry.handler("pou.rename")
def op_rename(args):
    target = args.get("target")
    new_name = args.get("new_name")
    if not target or not new_name:
        raise RuntimeError("pou.rename: 'target' and 'new_name' are required")
    proj = h.resolve_project(args)
    obj = h.find_object(proj, target)
    old = h._safe_name(obj)
    obj.rename(new_name)
    return {"renamed": True, "from": old, "to": new_name}


@_registry.handler("pou.find")
def op_find(args):
    target = args.get("target")
    if not target:
        raise RuntimeError("pou.find: 'target' is required")
    proj = h.resolve_project(args)
    obj = h.find_object(proj, target)
    return _summarize(obj)


@_registry.handler("pou.create_folder")
def op_create_folder(args):
    """Create a folder under a parent (default: project root). The SP22 API
    requires positional args; passing `name=...` raises TypeError (this is
    the phobicdotno open-bug we want to avoid).

    Note: `create_folder()` on SP22 returns void (None), not the folder
    object — so we re-query the parent's children to find the freshly
    created folder for the response.
    """
    name = args.get("name")
    if not name:
        raise RuntimeError("pou.create_folder: 'name' is required")
    proj = h.resolve_project(args)
    parent = _resolve_parent(proj, args)
    new_obj = parent.create_folder(name)  # positional, NOT keyword
    # SP22 returns None from create_folder. Re-query.
    if new_obj is None:
        try:
            for child in parent.get_children(False):
                if h._safe_name(child) == name and getattr(child, "is_folder", False):
                    new_obj = child
                    break
        except Exception:
            pass
    if new_obj is None:
        return {"created": True, "name": name, "note": "folder created but handle not recoverable"}
    return _summarize(new_obj)


@_registry.handler("pou.create_method")
def op_create_method(args):
    """Create a method on a Function Block parent. `parent` arg must resolve
    to a Function Block POU (methods only make sense there). `return_type`
    is the IEC type name (e.g. 'BOOL', 'INT', 'REAL', or a user-defined
    DUT name). Omit return_type for a 'void' (no return value) method."""
    name = args.get("name")
    parent_target = args.get("parent")
    if not name:
        raise RuntimeError("pou.create_method: 'name' is required")
    if not parent_target:
        raise RuntimeError(
            "pou.create_method: 'parent' is required (must be a Function Block)"
        )
    proj = h.resolve_project(args)
    parent = h.find_object(proj, parent_target)
    return_type = args.get("return_type")
    language = h.language_from_string(args.get("language", "st"))
    # CODESYS create_method signatures vary by SP: try with return_type if
    # given, else without; try language as kwarg if available.
    if return_type:
        try:
            new_obj = parent.create_method(name, return_type, language)
        except Exception:
            new_obj = parent.create_method(name, return_type)
    else:
        try:
            new_obj = parent.create_method(name, language)
        except Exception:
            new_obj = parent.create_method(name)
    summary = _summarize(new_obj)
    declaration = args.get("declaration")
    implementation = args.get("implementation")
    if declaration is not None:
        h.set_text_object_content(getattr(new_obj, "textual_declaration", None), declaration)
        summary["declaration_set"] = True
    if implementation is not None:
        h.set_text_object_content(getattr(new_obj, "textual_implementation", None), implementation)
        summary["implementation_set"] = True
    return summary


@_registry.handler("pou.create_property")
def op_create_property(args):
    """Create a property on a Function Block parent. `return_type` is the
    IEC type the property exposes (e.g. 'INT'). Returns the property
    container; per-accessor (get/set) bodies must be set via subsequent
    pou.set_text calls targeting `<parent>/<name>/Get` or `<parent>/<name>/Set`.
    """
    name = args.get("name")
    parent_target = args.get("parent")
    if not name:
        raise RuntimeError("pou.create_property: 'name' is required")
    if not parent_target:
        raise RuntimeError(
            "pou.create_property: 'parent' is required (must be a Function Block)"
        )
    proj = h.resolve_project(args)
    parent = h.find_object(proj, parent_target)
    return_type = args.get("return_type")
    if not return_type:
        raise RuntimeError("pou.create_property: 'return_type' is required")
    try:
        new_obj = parent.create_property(name, return_type)
    except Exception:
        # Some SPs expect different signatures; try language-bearing form.
        language = h.language_from_string(args.get("language", "st"))
        new_obj = parent.create_property(name, return_type, language)
    return _summarize(new_obj)


# ---------------------------------------------------------------------------
# Variable-level declaration editing
#
# These parse/rewrite the textual_declaration so callers can add a single
# variable or a symbol pragma without re-sending the whole declaration. The
# parser targets the common one-declaration-per-line ST style.
# ---------------------------------------------------------------------------
import re as _re

_VAR_SECTION_RE = _re.compile(
    r"^\s*(VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR_GLOBAL|VAR_TEMP|VAR_STAT|"
    r"VAR_EXTERNAL|VAR)\b", _re.IGNORECASE)
_END_VAR_RE = _re.compile(r"^\s*END_VAR\b", _re.IGNORECASE)
_VAR_DECL_RE = _re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*;")


def _parse_declaration(text):
    """Return a list of {section, name, type, init, pragma} for each declared
    variable. Pragmas (`{attribute ...}`) attach to the next variable."""
    out = []
    current = None
    pending_pragma = None
    for ln in (text or "").split("\n"):
        stripped = ln.strip()
        if _END_VAR_RE.match(ln):
            current = None
            pending_pragma = None
            continue
        msec = _VAR_SECTION_RE.match(ln)
        if msec:
            current = msec.group(1).upper()
            continue
        if current is None:
            continue
        if stripped.startswith("{") and stripped.endswith("}"):
            pending_pragma = stripped
            continue
        m = _VAR_DECL_RE.match(ln)
        if m:
            name = m.group(1)
            rest = m.group(2)
            init = None
            if ":=" in rest:
                typ, init = rest.split(":=", 1)
                typ = typ.strip()
                init = init.strip()
            else:
                typ = rest.strip()
            out.append({"section": current, "name": name, "type": typ,
                        "init": init, "pragma": pending_pragma})
            pending_pragma = None
    return out


def _decl_obj(args):
    target = args.get("target")
    if not target:
        raise RuntimeError("'target' is required")
    proj = h.resolve_project(args)
    obj = h.find_object(proj, target)
    decl_obj = getattr(obj, "textual_declaration", None)
    if decl_obj is None:
        raise RuntimeError("'%s' has no textual declaration" % h._safe_name(obj))
    return obj, decl_obj


@_registry.handler("pou.list_variables")
def op_list_variables(args):
    """Parse the target's declaration into structured variables."""
    obj, decl_obj = _decl_obj(args)
    text = h.get_text_object_content(decl_obj)
    variables = _parse_declaration(text)
    return {"name": h._safe_name(obj), "count": len(variables), "variables": variables}


@_registry.handler("pou.add_variable")
def op_add_variable(args):
    """Insert one variable into a VAR section without rewriting the whole
    declaration. `name` + `type` required; optional `init`, `comment`,
    `section` (default VAR), `pragma`. Creates the section if absent."""
    name = args.get("name")
    var_type = args.get("type")
    if not name or not var_type:
        raise RuntimeError("pou.add_variable: 'name' and 'type' are required")
    obj, decl_obj = _decl_obj(args)
    section = (args.get("section") or "VAR").upper()
    init = args.get("init")
    comment = args.get("comment")
    pragma = args.get("pragma")

    line = "\t" + name + " : " + var_type
    if init is not None and str(init) != "":
        line += " := " + str(init)
    line += ";"
    if comment:
        line += "  // " + str(comment)

    text = h.get_text_object_content(decl_obj) or ""
    lines = text.split("\n")
    insert_block = []
    if pragma:
        insert_block.append("\t" + str(pragma))
    insert_block.append(line)

    # Find the END_VAR that closes the requested section.
    out_lines = []
    inserted = False
    current = None
    for ln in lines:
        msec = _VAR_SECTION_RE.match(ln)
        if msec:
            current = msec.group(1).upper()
        if _END_VAR_RE.match(ln) and current == section and not inserted:
            out_lines.extend(insert_block)
            inserted = True
            current = None
        elif _END_VAR_RE.match(ln):
            current = None
        out_lines.append(ln)

    if not inserted:
        # Section absent — append a fresh block at the end.
        out_lines.append(section)
        out_lines.extend(insert_block)
        out_lines.append("END_VAR")

    h.set_text_object_content(decl_obj, "\n".join(out_lines))
    return {"name": h._safe_name(obj), "added": name, "section": section,
            "created_section": not inserted}


@_registry.handler("pou.add_symbol_pragma")
def op_add_symbol_pragma(args):
    """Mark a declared variable for symbol export by inserting
    `{attribute 'symbol' := '<access>'}` on the line before it. `access` is
    read / write / readwrite / none (default readwrite)."""
    name = args.get("name")
    if not name:
        raise RuntimeError("pou.add_symbol_pragma: 'name' is required")
    access = (args.get("access") or "readwrite").strip().lower()
    if access not in ("read", "write", "readwrite", "none"):
        raise RuntimeError("access must be read/write/readwrite/none")
    obj, decl_obj = _decl_obj(args)
    pragma = "{attribute 'symbol' := '" + access + "'}"

    text = h.get_text_object_content(decl_obj) or ""
    lines = text.split("\n")
    decl_re = _re.compile(r"^(\s*)" + _re.escape(name) + r"\s*:")
    out_lines = []
    done = False
    for ln in lines:
        m = decl_re.match(ln)
        if m and not done:
            out_lines.append(m.group(1) + pragma)
            done = True
        out_lines.append(ln)
    if not done:
        raise RuntimeError("variable %r not found in declaration of '%s'"
                           % (name, h._safe_name(obj)))
    h.set_text_object_content(decl_obj, "\n".join(out_lines))
    return {"name": h._safe_name(obj), "variable": name, "pragma": pragma}
