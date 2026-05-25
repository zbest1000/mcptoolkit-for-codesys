# -*- coding: utf-8 -*-
"""
Library Manager handlers.

Ops:
  library.list_installed    list libraries installed in the system repos
  library.list_project      list library references in the current project
  library.add               add a library reference to the project
  library.remove            remove a library reference from the project

The GLOBAL `librarymanager` injected global is the system-wide LibManager
(repositories, installed library inventory). PROJECT-scoped library
references live under a "Library Manager" tree object inside the project
(part of the Device > PLC Logic > Application subtree on a Standard
project). Each handler picks the right side.
"""
import _registry
import _codesys_helpers as h


def _lib_manager():
    """Return the global LibManager (system-wide). Raises if not injected."""
    return h._g("librarymanager")


# Stable type GUID for the project-scoped Library Manager object as it
# appears in the project tree on SP22. Verified at runtime via
# project.tree on a Standard.project — see CHANGES.md.
_TYPEGUID_LIBMANAGER = "adb5cb65-8e1d-4a00-b70a-375ea27582f3"


def _find_project_libmanager(proj):
    """Locate the project-scoped Library Manager object. Walks the tree by
    type GUID to be name-localization-resilient (the IDE label varies)."""
    out = []

    def visit(o):
        try:
            kids = list(o.get_children(False))
        except Exception:
            kids = []
        for k in kids:
            try:
                t = str(getattr(k, "type", "")).lower()
            except Exception:
                t = ""
            if t == _TYPEGUID_LIBMANAGER:
                out.append(k)
            visit(k)

    visit(proj)
    if not out:
        raise RuntimeError(
            "No Library Manager found in project tree. Most projects have "
            "one under Device > Plc Logic > Application."
        )
    if len(out) > 1:
        # Multi-application project. Caller should pass `path=...` to pick.
        raise RuntimeError(
            "Multiple Library Managers found (" + str(len(out))
            + "). Multi-application projects: pass `path=...` arg to scope."
        )
    return out[0]


def _summarize_lib_ref(ref):
    """Render one library reference for the wire."""
    out = {}
    for attr in ("name", "namespace", "company", "version",
                 "system", "container", "is_placeholder",
                 "id", "title", "resolution"):
        try:
            out[attr] = h._coerce(getattr(ref, attr, None)) if hasattr(h, "_coerce") else str(getattr(ref, attr, None))
        except Exception:
            out[attr] = None
    # Get the resolved library (if not a placeholder)
    try:
        out["effective_version"] = str(getattr(ref, "effective_version", ""))
    except Exception:
        pass
    return out


def _summarize_lib(lib):
    """Render one installed library."""
    out = {}
    for attr in ("display_name", "name", "namespace", "company",
                 "version", "title", "library_path"):
        try:
            out[attr] = str(getattr(lib, attr, ""))
        except Exception:
            out[attr] = ""
    return out


def _editable_repo():
    """Return the first editable library repository, or None if there isn't
    one. Most CODESYS installs only ship a read-only `System` repo until
    you add a User repo via the IDE's Library Repository dialog.
    """
    lm = _lib_manager()
    try:
        for repo in lm.repositories:
            try:
                if bool(getattr(repo, "editable", False)):
                    return repo
            except Exception:
                continue
    except Exception:
        return None
    return None


def _summarize_repo(repo):
    out = {}
    for attr in ("name", "root_folder", "editable"):
        try:
            out[attr] = str(getattr(repo, attr, ""))
        except Exception:
            out[attr] = ""
    return out


@_registry.handler("library.repositories")
def op_repositories(args):
    """List library repositories known to the system. Each entry has
    `name`, `root_folder`, and `editable`. To install a .library file
    you need an editable repository; if none exists, configure one via
    the IDE Library Repository dialog or via APInstaller.
    """
    lm = _lib_manager()
    out = []
    for repo in lm.repositories:
        out.append(_summarize_repo(repo))
    return {"count": len(out), "repositories": out}


@_registry.handler("library.install")
def op_install(args):
    """Install a .library or .compiled-library file into a system repository.

    Args:
      path        absolute path to the .library / .compiled-library file
      repository  optional name of the target repository; defaults to the
                  first editable one. Pass an explicit name when multiple
                  editable repos exist.
      overwrite   default False; set True to replace an existing entry.
    """
    import os
    path = args.get("path")
    if not path:
        raise RuntimeError("library.install: 'path' is required")
    if not os.path.exists(path):
        raise RuntimeError("library.install: file does not exist: " + path)
    overwrite = bool(args.get("overwrite", False))
    target_name = args.get("repository")

    lm = _lib_manager()
    repo = None
    if target_name:
        for r in lm.repositories:
            try:
                if str(getattr(r, "name", "")) == target_name:
                    repo = r; break
            except Exception:
                continue
        if repo is None:
            available = []
            for r in lm.repositories:
                try: available.append(str(r.name))
                except Exception: pass
            raise RuntimeError(
                "library.install: repository {!r} not found. Available: {}".format(
                    target_name, ", ".join(available) if available else "(none)"
                )
            )
        if not bool(getattr(repo, "editable", False)):
            raise RuntimeError(
                "library.install: repository {!r} is not editable. Use a different "
                "repository or create an editable User repo via the IDE.".format(target_name)
            )
    else:
        repo = _editable_repo()
        if repo is None:
            raise RuntimeError(
                "library.install: no editable library repository on this install. "
                "Add a User repository via the IDE's Library Repository dialog, "
                "then retry. (Most fresh CODESYS installs ship with only the "
                "read-only System repository.)"
            )

    try:
        result = lm.install_library(path, repo, overwrite)
    except Exception as e:
        raise RuntimeError("library.install: install_library failed: " + str(e))
    return {
        "installed": True,
        "path": path,
        "repository": _summarize_repo(repo),
        "overwrite": overwrite,
        "result_repr": repr(result)[:160] if result is not None else None,
    }


@_registry.handler("library.resolve_missing")
def op_resolve_missing(args):
    """Trigger the IDE's 'Download missing libraries...' workflow.

    Invokes `system.commands` GUID 0ae00f1a-9483-402b-96bd-54b568fd2520
    with prompts suppressed. CAVEAT: the underlying command opens a
    dialog in normal use; with prompts suppressed CODESYS will fall
    back to its non-interactive defaults — which may either succeed
    (if the missing libraries can be auto-located in a known store) or
    silently do nothing. After calling, query `library.list_project` to
    see whether unresolved refs are now resolved.

    Args:
      forward_prompts   default False; set True to surface prompt
                        callbacks to the LLM client (advanced).
    """
    forward = bool(args.get("forward_prompts", False))
    # Find the command
    target = None
    for c in system_obj().commands:  # noqa: F821 — system is injected
        try:
            if str(c.guid) == "0ae00f1a-9483-402b-96bd-54b568fd2520":
                target = c
                break
        except Exception:
            continue
    if target is None:
        raise RuntimeError(
            "library.resolve_missing: 'Download missing libraries...' command "
            "not found in system.commands (GUID 0ae00f1a-9483-402b-96bd-54b568fd2520)."
        )

    # Configure prompt handling
    system_obj_ref = system_obj()  # alias
    original_handling = None
    try:
        original_handling = system_obj_ref.script_prompt_handling
    except Exception:
        pass
    try:
        try:
            ScriptPromptHandling = h._g("ScriptPromptHandling")
            if forward:
                system_obj_ref.script_prompt_handling = ScriptPromptHandling.ForwardUnknownPrompts
            else:
                system_obj_ref.script_prompt_handling = ScriptPromptHandling.SuppressPrompts
        except Exception:
            pass
        result = target.execute()
    finally:
        try:
            if original_handling is not None:
                system_obj_ref.script_prompt_handling = original_handling
        except Exception:
            pass
    return {
        "executed": True,
        "command_guid": "0ae00f1a-9483-402b-96bd-54b568fd2520",
        "result_repr": repr(result)[:160] if result is not None else None,
        "note": (
            "Query library.list_project to verify resolution. With prompts "
            "suppressed the IDE uses non-interactive defaults; if libraries "
            "are still unresolved, install them manually via APInstaller or "
            "the IDE Library Repository dialog."
        ),
    }


def system_obj():
    """Thin wrapper because we don't import h.system_obj at module top."""
    return h.system_obj()


# Library Manager message category GUID — where the IDE writes "Could not
# open library '...' has not been installed" errors. Stable on SP22.
_LIBMAN_CATEGORY_GUID_STR = "56a60174-4139-411b-86c7-df0da1cfc088"


def _libman_guid():
    try:
        import System
        return System.Guid(_LIBMAN_CATEGORY_GUID_STR)
    except Exception:
        return None


# Regex (string-based; IronPython 2.7 has re).
import re as _re
_LIB_NOT_INSTALLED_RE = _re.compile(
    r"Could not open library\s+'([^']+)'\. ?\(Reason: The library\s+'([^']+)'\s+has not been installed to the system\.?\)"
)
_LIB_GENERIC_NOT_INSTALLED_RE = _re.compile(
    r"library\s+'([^']+)'\s+has not been installed"
)


def _parse_libref_display_name(s):
    """Parse 'Name, version (Vendor)' / 'Name, * (System)' into pieces.

    Returns dict {raw, name, version, vendor} with version='*' for wildcards.
    """
    out = {"raw": s, "name": s, "version": "", "vendor": ""}
    if not s:
        return out
    # Strip trailing vendor like " (System)"
    m = _re.match(r"^(.*?)\s*\((.*?)\)\s*$", s)
    if m:
        head, vendor = m.group(1), m.group(2)
        out["vendor"] = vendor.strip()
    else:
        head = s
    # Split name + version on the FIRST comma
    if "," in head:
        name, version = head.split(",", 1)
        out["name"] = name.strip()
        out["version"] = version.strip()
    else:
        out["name"] = head.strip()
    return out


@_registry.handler("library.diagnose")
def op_diagnose(args):
    """Identify unresolved / missing library references in the project.

    Scans the IDE's Library Manager message category for the canonical
    "Could not open library '...' has not been installed to the system"
    errors that surface when a project references libraries the local
    install lacks. Cross-references each error against the project's
    `lm.references` list so the caller knows exactly which named refs
    are unresolved.

    Returns:
      missing: [{name, version, vendor, message, in_references}]
      references: full list with `is_placeholder`, `is_managed`,
                  `name`, `namespace`, `optional`.
      total_references, total_missing, libman_messages.
    """
    proj = h.resolve_project(args)
    lm_obj = _find_project_libmanager(proj)

    # Pull every ref + its basic metadata.
    refs_dump = []
    try:
        for r in lm_obj.references:
            info = {
                "name": "",
                "namespace": "",
                "is_placeholder": False,
                "is_managed": False,
                "optional": False,
            }
            for attr, default in (("name", ""), ("namespace", ""),
                                  ("is_placeholder", False),
                                  ("is_managed", False),
                                  ("optional", False)):
                try:
                    info[attr] = getattr(r, attr, default)
                    # Coerce bools / strings via str() if needed
                    if isinstance(default, bool):
                        info[attr] = bool(info[attr])
                    else:
                        info[attr] = str(info[attr])
                except Exception:
                    info[attr] = default
            refs_dump.append(info)
    except Exception as e:
        refs_dump = [{"_error": str(e)}]

    # Pull all messages from the Library Manager category.
    libman_msgs = []
    g = _libman_guid()
    if g is not None:
        try:
            for m in h.system_obj().get_message_objects(g):
                msg = {
                    "severity": str(getattr(m, "severity", "")),
                    "text": str(getattr(m, "text", "")),
                }
                libman_msgs.append(msg)
        except Exception as e:
            libman_msgs.append({"_error": str(e)})

    # Parse messages to extract missing-library names.
    missing = []
    seen_names = set()
    for msg in libman_msgs:
        text = msg.get("text", "")
        m = _LIB_NOT_INSTALLED_RE.search(text)
        if m is None:
            m = _LIB_GENERIC_NOT_INSTALLED_RE.search(text)
        if m is None:
            continue
        lib_name = m.group(1)
        if lib_name in seen_names:
            continue
        seen_names.add(lib_name)
        parsed = _parse_libref_display_name(lib_name)
        # Match against the project's reference list
        in_refs = False
        matched_namespace = None
        for r in refs_dump:
            if r.get("name") == lib_name:
                in_refs = True
                matched_namespace = r.get("namespace")
                break
            # Looser match by namespace
            if parsed["name"] and r.get("namespace") == parsed["name"]:
                in_refs = True
                matched_namespace = r.get("namespace")
                break
        missing.append({
            "name": parsed["name"],
            "version": parsed["version"],
            "vendor": parsed["vendor"],
            "raw": parsed["raw"],
            "message": text[:300],
            "in_references": in_refs,
            "matched_namespace": matched_namespace,
        })

    return {
        "references": refs_dump,
        "missing": missing,
        "total_references": len(refs_dump),
        "total_missing": len(missing),
        "libman_messages": libman_msgs[:30],
        "advice": (
            "For each entry in 'missing': install via "
            "`codesys.library.install` (if you have the .library file), "
            "`codesys.system.install_package` (if you have the .package), "
            "or `codesys.library.resolve_missing` (online fetch). "
            "Then call this op again to confirm the missing list is empty."
        ) if missing else "All library references appear resolved.",
    }


@_registry.handler("library.create_repository")
def op_create_repository(args):
    """Create an editable library repository (User repo).

    Required when you want to `library.install` a .library file but
    the system only has the read-only System repo configured. After
    creating, the new repo appears in `library.repositories` as
    `editable: True`.
    """
    import os
    name = args.get("name")
    folder = args.get("folder")
    index = int(args.get("index", 0))
    if not name:
        raise RuntimeError("library.create_repository: 'name' is required")
    if not folder:
        raise RuntimeError("library.create_repository: 'folder' is required")
    if not os.path.isabs(folder):
        raise RuntimeError("library.create_repository: 'folder' must be absolute")
    if not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except OSError as e:
            raise RuntimeError(
                "library.create_repository: could not create folder " + folder + ": " + str(e)
            )

    lm = _lib_manager()
    try:
        # SP22 signature: insert_repository(rootfolder, name, index)
        lm.insert_repository(folder, name, index)
    except Exception as e:
        raise RuntimeError("library.create_repository: insert_repository failed: " + str(e))

    # Find and return the new repo info
    new_repo = None
    for r in lm.repositories:
        try:
            if str(getattr(r, "name", "")) == name:
                new_repo = r
                break
        except Exception:
            continue
    return {
        "created": True,
        "name": name,
        "folder": folder,
        "index": index,
        "info": _summarize_repo(new_repo) if new_repo else None,
    }


@_registry.handler("library.list_installed")
def op_list_installed(args):
    """Enumerate libraries installed in the system repositories.

    Optional args:
      pattern    substring filter applied to display_name / namespace
      limit      cap result count (default 200)
    """
    pattern = (args.get("pattern") or "").strip().lower()
    limit = int(args.get("limit") or 200)
    lm = _lib_manager()
    try:
        libs = list(lm.get_all_libraries())
    except Exception as e:
        raise RuntimeError("librarymanager.get_all_libraries() failed: " + str(e))
    out = []
    for lib in libs:
        summary = _summarize_lib(lib)
        if pattern:
            hay = " ".join(summary.get(k, "") for k in
                           ("display_name", "name", "namespace")).lower()
            if pattern not in hay:
                continue
        out.append(summary)
        if len(out) >= limit:
            break
    return {"installed": out, "count": len(out), "truncated": len(out) >= limit}


@_registry.handler("library.list_project")
def op_list_project(args):
    """Enumerate library references in the project."""
    proj = h.resolve_project(args)
    lm_obj = _find_project_libmanager(proj)
    # The references live on the Library Manager object. Different SP
    # versions expose them via different attribute names.
    refs = None
    for attr in ("references", "get_references", "lib_refs", "libraries"):
        a = getattr(lm_obj, attr, None)
        if a is None:
            continue
        try:
            refs = list(a() if callable(a) else a)
            break
        except Exception:
            continue
    if refs is None:
        raise RuntimeError(
            "Could not enumerate libraries on this Library Manager object. "
            "Tried attrs: references, get_references, lib_refs, libraries."
        )
    out = [_summarize_lib_ref(r) for r in refs]
    return {
        "library_manager_guid": str(getattr(lm_obj, "guid", "")),
        "library_manager_name": h._safe_name(lm_obj),
        "references": out,
        "count": len(out),
    }


@_registry.handler("library.add")
def op_add(args):
    """Add a library reference to the project.

    SP22 API (verified at runtime):
      - `lm.add_library(name)` — exactly 1 string arg, no version pin. The
        IDE resolves to the highest installed version matching the name.
      - `lm.add_placeholder(placeholder_name, library_name)` — register a
        placeholder that resolves to a specific library at compile time.

    Args:
      name              library name to add (required)
      placeholder       optional. If provided, adds a placeholder
                        `placeholder` -> `name` instead of a direct ref.
                        Use when a project may run against different
                        compatible libraries.
      allow_unresolved  default False; require library to resolve in the
                        system repos before adding. Setting True skips
                        the safety check.
    """
    name = args.get("name")
    if not name:
        raise RuntimeError("library.add: 'name' is required")
    placeholder = args.get("placeholder")
    # NOTE: `allow_unresolved` is accepted for API consistency but is now a
    # no-op. The pre-flight `find_library` check was misleading on SP22:
    # find_library expects a FULL display name like
    # "Util, 3.5.17.0 (3S - Smart Software Solutions GmbH)", not the short
    # namespace string callers actually want to pass. Letting `add_library`
    # do its own resolution and surfacing its native error is more useful.
    _ = bool(args.get("allow_unresolved", False))

    proj = h.resolve_project(args)
    lm_obj = _find_project_libmanager(proj)

    if placeholder:
        try:
            lm_obj.add_placeholder(placeholder, name)
            mode = "placeholder"
            added_as = placeholder
        except Exception as e:
            raise RuntimeError(
                "library.add: add_placeholder({!r}, {!r}) failed: {}".format(
                    placeholder, name, str(e)
                )
            )
    else:
        try:
            lm_obj.add_library(name)
            mode = "library"
            added_as = name
        except Exception as e:
            raise RuntimeError(
                "library.add: add_library({!r}) failed: {}".format(name, str(e))
            )

    return {
        "added": True,
        "mode": mode,
        "name": name,
        "added_as": added_as,
        "library_manager_guid": str(getattr(lm_obj, "guid", "")),
    }


@_registry.handler("library.remove")
def op_remove(args):
    """Remove a library reference from the project.

    SP22 API: `lm.remove_library(name)` takes a STRING (the reference's
    name as it appears in `references`), NOT the reference object.
    """
    name = args.get("name")
    if not name:
        raise RuntimeError("library.remove: 'name' is required")
    proj = h.resolve_project(args)
    lm_obj = _find_project_libmanager(proj)

    # Confirm the ref exists before removing — gives a better error than
    # CODESYS's terse "not found" message.
    refs = list(lm_obj.references)
    candidates = []
    for r in refs:
        rname = str(getattr(r, "name", ""))
        rns = str(getattr(r, "namespace", ""))
        if rname == name or rns == name or rname.startswith(name + ","):
            candidates.append(rname)

    if not candidates:
        existing = [str(r.name) for r in refs]
        raise RuntimeError(
            "Library reference not found: '" + name + "'. Existing refs: "
            + (", ".join(existing) if existing else "(none)")
        )

    # Try the exact name first, then each candidate. Multiple matches are
    # rare but possible (e.g., a direct ref AND its placeholder).
    last_err = None
    removed = []
    for target_name in [name] + [c for c in candidates if c != name]:
        try:
            lm_obj.remove_library(target_name)
            removed.append(target_name)
            break  # remove just one at a time, even if name matched multiple
        except Exception as e:
            last_err = e
            continue
    if not removed:
        raise RuntimeError(
            "library.remove: remove_library failed for all candidates "
            + str(candidates) + "; last error: " + str(last_err)
        )
    return {"removed": True, "name": removed[0]}


@_registry.handler("library.update")
def op_update(args):
    """Bump a project library reference to the latest installed version.

    Removes the current reference and re-adds it by bare namespace, which
    CODESYS resolves to the highest installed version. Pass `to` to pin a
    specific version string instead. Returns before/after reference names.
    """
    name = args.get("name")
    if not name:
        raise RuntimeError("library.update: 'name' is required")
    proj = h.resolve_project(args)
    lm_obj = _find_project_libmanager(proj)

    match = None
    for r in list(lm_obj.references):
        rname = str(getattr(r, "name", ""))
        rns = str(getattr(r, "namespace", ""))
        if rname == name or rns == name or rname.startswith(name + ","):
            match = r
            break
    if match is None:
        existing = [str(r.name) for r in lm_obj.references]
        raise RuntimeError(
            "Library reference not found: '" + name + "'. Existing: "
            + (", ".join(existing) if existing else "(none)")
        )

    before_name = str(getattr(match, "name", ""))
    namespace = str(getattr(match, "namespace", "")) or name
    add_name = args.get("to") or namespace

    lm_obj.remove_library(before_name)
    lm_obj.add_library(add_name)

    after = None
    for r in list(lm_obj.references):
        rname = str(getattr(r, "name", ""))
        if (str(getattr(r, "namespace", "")) == namespace
                or rname.startswith(namespace + ",")
                or rname == add_name):
            after = rname
            break
    return {"updated": True, "namespace": namespace,
            "before": before_name, "after": after}
