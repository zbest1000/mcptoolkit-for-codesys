# -*- coding: utf-8 -*-
"""
Project lifecycle handlers.

Ops:
  project.open       open a .project file
  project.create     create a new empty .project
  project.save       save the targeted project
  project.save_as    save to a new path
  project.save_archive  save a .projectarchive
  project.close      close the targeted project
  project.list_open  list all open projects
  project.info       metadata about a project
  project.tree       list tree objects (depth-limited)
"""
import os
import _registry
import _codesys_helpers as h


def _project_summary(p):
    return {
        "path": getattr(p, "path", None),
        "primary": (p == h.projects_obj().primary),
        "dirty": bool(getattr(p, "dirty", False)),
        "library": bool(getattr(p, "library", False)),
        "title": getattr(p, "title", None),
    }


@_registry.handler("project.open")
def op_open(args):
    """Open a project. On SP22 the API rejects keyword args for
    `update_storage_format` and `password` (the IronPython proxy
    apparently maps to a CLR overload that requires positional args).
    Try the most-explicit form first, fall back to simpler shapes."""
    path = args.get("path")
    if not path:
        raise RuntimeError("project.open: 'path' is required")
    if not os.path.exists(path):
        raise RuntimeError("project.open: path does not exist: " + path)
    update = bool(args.get("update_storage_format", False))
    password = args.get("password")
    last_err = None
    attempts = []
    if password:
        attempts.append(lambda: h.projects_obj().open(path, update, password))
        attempts.append(lambda: h.projects_obj().open(path, update_storage_format=update, password=password))
    attempts.append(lambda: h.projects_obj().open(path, update))
    attempts.append(lambda: h.projects_obj().open(path, update_storage_format=update))
    attempts.append(lambda: h.projects_obj().open(path))
    proj = None
    for call in attempts:
        try:
            proj = call()
            break
        except TypeError as e:
            last_err = e
            continue
    if proj is None:
        raise RuntimeError("project.open: every signature variant failed; last error: " + str(last_err))

    out = {"opened": True, "project": _project_summary(proj)}

    # Surface library-resolution diagnostics inline IF there are problems —
    # otherwise stay quiet to keep the response lean. Bypass with
    # `diagnose_libraries=false` to suppress entirely (e.g. when the caller
    # plans to handle diagnosis themselves).
    if args.get("diagnose_libraries") is not False:
        try:
            import handlers.library_h as _libh
            diag = _libh.op_diagnose({})
            if diag.get("total_missing", 0) > 0:
                out["library_diagnostics"] = {
                    "total_missing": diag["total_missing"],
                    "missing": diag["missing"],
                    "advice": diag.get("advice", ""),
                }
        except Exception:
            pass  # never fail open() on a diagnostic side path

    return out


@_registry.handler("project.create")
def op_create(args):
    path = args.get("path")
    if not path:
        raise RuntimeError("project.create: 'path' is required")
    overwrite = bool(args.get("overwrite", False))
    if os.path.exists(path) and not overwrite:
        raise RuntimeError("project.create: path exists (set overwrite=true to replace): " + path)
    if os.path.exists(path) and overwrite:
        os.remove(path)
    proj = h.projects_obj().create(path)
    return {"created": True, "project": _project_summary(proj)}


def _install_templates_dir(args):
    """Locate <install>/CODESYS/Templates.

    Preferred path: host injects `templates_dir` into the command args (it
    knows from `ctx.install.install_dir`). The watcher can't derive it from
    `system.executable_filename` alone — on SP22 that returns just
    'CODESYS.exe' with no path.

    Fallback: walk a few well-known locations.
    """
    candidate = args.get("templates_dir")
    if candidate and os.path.isdir(candidate):
        return candidate

    fallbacks = [
        r"C:\Program Files\CODESYS 3.5.22.10\CODESYS\Templates",
        r"C:\Program Files (x86)\CODESYS 3.5.22.10\CODESYS\Templates",
    ]
    for c in fallbacks:
        if os.path.isdir(c):
            return c
    raise RuntimeError(
        "could not locate <install>/CODESYS/Templates; pass templates_dir explicitly "
        "via the host (ctx.install.install_dir)."
    )


@_registry.handler("project.create_standard")
def op_create_standard(args):
    """Create a project from the CODESYS Standard.project template (Device +
    PLC Logic + Application + MainTask + PLC_PRG). This is the same template
    the IDE's New Project wizard uses; we copy it to the target path and open
    it. Required to make `build.build` testable from an MCP-only workflow.

    Args:
      path (str, required): destination .project path.
      overwrite (bool, default False): replace existing file at `path`.
      template (str, default "Standard"): template basename to use; resolved
        against `<install>/CODESYS/Templates/<template>.project`. Pass
        "Empty" to start from the bare template (Project Settings only).
    """
    import shutil
    path = args.get("path")
    if not path:
        raise RuntimeError("project.create_standard: 'path' is required")
    overwrite = bool(args.get("overwrite", False))
    template = (args.get("template") or "Standard").strip()
    if not template.endswith(".project"):
        template = template + ".project"

    if os.path.exists(path) and not overwrite:
        raise RuntimeError(
            "project.create_standard: path exists (set overwrite=true to replace): " + path
        )

    templates_dir = _install_templates_dir(args)
    src = os.path.join(templates_dir, template)
    if not os.path.exists(src):
        available = []
        try:
            for f in os.listdir(templates_dir):
                if f.endswith(".project"):
                    available.append(f)
        except OSError:
            pass
        raise RuntimeError(
            "Template not found: " + src
            + ". Available: " + (", ".join(sorted(available)) if available else "(none)")
        )

    # If a primary project is open, close it first — IDE only allows one at a
    # time at the top of the stack via projects.open().
    cur = h.projects_obj().primary
    if cur is not None:
        try:
            cur.close()
        except Exception:
            pass  # closing may fail on dirty projects; opening below will retry

    target_dir = os.path.dirname(path)
    if target_dir and not os.path.exists(target_dir):
        os.makedirs(target_dir)
    if os.path.exists(path) and overwrite:
        os.remove(path)
    shutil.copyfile(src, path)

    proj = h.projects_obj().open(path)
    return {
        "created": True,
        "template": template,
        "source": src,
        "project": _project_summary(proj),
    }


@_registry.handler("project.save")
def op_save(args):
    proj = h.resolve_project(args)
    proj.save()
    return {"saved": True, "project": _project_summary(proj)}


@_registry.handler("project.save_as")
def op_save_as(args):
    new_path = args.get("new_path")
    if not new_path:
        raise RuntimeError("project.save_as: 'new_path' is required")
    proj = h.resolve_project(args)
    proj.save_as(new_path)
    return {"saved_as": new_path, "project": _project_summary(proj)}


@_registry.handler("project.save_archive")
def op_save_archive(args):
    out_path = args.get("path")
    if not out_path:
        raise RuntimeError("project.save_archive: 'path' is required")
    proj = h.resolve_project(args)
    # Best-effort: pass through any categories arg as-is. Default to library+device+compileinfo.
    categories = args.get("categories")
    if categories:
        proj.save_archive(out_path, categories)
    else:
        proj.save_archive(out_path)
    return {"archived": True, "path": out_path}


@_registry.handler("project.close")
def op_close(args):
    proj = h.resolve_project(args)
    path = getattr(proj, "path", None)
    proj.close()
    return {"closed": True, "path": path}


@_registry.handler("project.list_open")
def op_list_open(args):
    """List open projects. By default filters out compiled-library projects
    (they show up in projects.all because the IDE treats them as Project
    instances too, but they're not user projects). Pass
    `include_libraries=true` to see everything."""
    include_libs = bool(args.get("include_libraries", False))
    out = []
    for p in h.projects_obj().all:
        summary = _project_summary(p)
        if not include_libs and summary.get("library"):
            continue
        out.append(summary)
    return {"projects": out, "filtered_libraries": (not include_libs)}


@_registry.handler("project.info")
def op_info(args):
    proj = h.resolve_project(args)
    out = _project_summary(proj)
    try:
        if getattr(proj, "has_project_info", False):
            pi = proj.get_project_info()
            out["info"] = {
                "company": getattr(pi, "company", None),
                "title": getattr(pi, "title", None),
                "version": getattr(pi, "version", None),
                "author": getattr(pi, "author", None),
                "description": getattr(pi, "description", None),
            }
    except Exception:
        pass
    return out


def _walk(obj, depth, max_depth, out):
    name = h._safe_name(obj)
    entry = {
        "name": name,
        "type": str(getattr(obj, "type", "")),
        "guid": str(getattr(obj, "guid", "")),
        "is_folder": bool(getattr(obj, "is_folder", False)),
    }
    out.append(entry)
    if depth >= max_depth:
        return
    try:
        children = list(obj.get_children(False))
    except Exception:
        children = []
    if children:
        entry["children"] = []
        sub_out = entry["children"]
        for ch in children:
            _walk(ch, depth + 1, max_depth, sub_out)


@_registry.handler("project.tree")
def op_tree(args):
    proj = h.resolve_project(args)
    max_depth = int(args.get("max_depth", 3))
    out = []
    for ch in proj.get_children(False):
        _walk(ch, 1, max_depth, out)
    return {"tree": out, "max_depth": max_depth}


# ---------------------------------------------------------------------------
# Project metadata edit
# ---------------------------------------------------------------------------


@_registry.handler("project.set_info")
def op_set_info(args):
    """Edit Project Information (title, author, version, description,
    company). Each is optional — only fields supplied are updated.

    The IDE keeps these in a Project Information POU (well, settings-like
    object). The script API exposes them via the ScriptProject.set_project_info
    method on SP22.
    """
    proj = h.resolve_project(args)
    updates = {}
    for field in ("title", "version", "author", "company", "description"):
        if field in args and args[field] is not None:
            updates[field] = args[field]
    if not updates:
        raise RuntimeError(
            "project.set_info: provide at least one of "
            "title / version / author / company / description"
        )

    # Read existing info to merge with updates (script API typically wants the
    # full record on set).
    try:
        pi = proj.get_project_info() if getattr(proj, "has_project_info", False) else None
    except Exception:
        pi = None

    current = {
        "title": getattr(pi, "title", "") or "" if pi else "",
        "version": getattr(pi, "version", "") or "" if pi else "",
        "author": getattr(pi, "author", "") or "" if pi else "",
        "company": getattr(pi, "company", "") or "" if pi else "",
        "description": getattr(pi, "description", "") or "" if pi else "",
    }
    merged = dict(current)
    merged.update(updates)

    # Try the obvious shapes — different SP versions vary.
    set_fn = None
    for attr in ("set_project_info", "update_project_info", "set_info"):
        f = getattr(proj, attr, None)
        if callable(f):
            set_fn = f
            break
    if set_fn is None:
        # Fallback: assign directly to .project_info attribute fields.
        try:
            for k, v in merged.items():
                if pi is not None and hasattr(pi, k):
                    setattr(pi, k, v)
            return {"updated": True, "info": merged, "method": "direct_attr_set"}
        except Exception as e:
            raise RuntimeError(
                "project.set_info: no set_project_info-style method and "
                "direct attribute set failed: " + str(e)
            )

    # Try call shapes.
    last_err = None
    for call in (
        lambda: set_fn(merged.get("title", ""), merged.get("version", ""),
                       merged.get("author", ""), merged.get("company", ""),
                       merged.get("description", "")),
        lambda: set_fn(**merged),
        lambda: set_fn(merged),
    ):
        try:
            call()
            return {"updated": True, "info": merged, "method": "set_project_info"}
        except Exception as e:
            last_err = e
    raise RuntimeError("project.set_info: all set shapes failed; last: " + str(last_err))


# ---------------------------------------------------------------------------
# Mirror export — write project source to a filesystem tree for git diff
# ---------------------------------------------------------------------------


def _mirror_safe_segment(name):
    """Make a tree-node name safe for filesystem use. Replaces path
    separators and characters Windows hates."""
    bad = '<>:"/\\|?*\x00'
    out = []
    for ch in name:
        if ch in bad or ord(ch) < 32:
            out.append("_")
        else:
            out.append(ch)
    # Strip trailing dots/spaces (Windows quirk).
    return ("".join(out)).rstrip(". ") or "_unnamed"


def _mirror_walk(obj, dirpath, out_dirs_created, out_files_written, dryrun=False):
    """Recursively dump textual children. Folders become subdirectories;
    code-bearing objects become a .st file (or .decl + .impl pair)."""
    try:
        children = list(obj.get_children(False))
    except Exception:
        children = []
    for ch in children:
        name = _mirror_safe_segment(h._safe_name(ch))
        is_folder = bool(getattr(ch, "is_folder", False))
        decl_attr = getattr(ch, "textual_declaration", None)
        impl_attr = getattr(ch, "textual_implementation", None)

        if is_folder:
            subdir = os.path.join(dirpath, name)
            if not dryrun and not os.path.isdir(subdir):
                os.makedirs(subdir)
                out_dirs_created.append(subdir)
            _mirror_walk(ch, subdir, out_dirs_created, out_files_written, dryrun)
            continue

        # Code-bearing object: dump declaration and implementation if present.
        decl_text = None
        impl_text = None
        if decl_attr is not None:
            try:
                decl_text = h.get_text_object_content(decl_attr)
            except Exception:
                decl_text = None
        if impl_attr is not None:
            try:
                impl_text = h.get_text_object_content(impl_attr)
            except Exception:
                impl_text = None

        if decl_text is None and impl_text is None:
            # Non-textual leaf (devices, library refs, etc.). Recurse — some
            # of these have children (e.g. methods/properties under FBs).
            _mirror_walk(ch, dirpath, out_dirs_created, out_files_written, dryrun)
            continue

        # For objects that ALSO have children (Function Block with methods),
        # write the FB's own .st AND walk into a subdir for its members.
        sub_kids = []
        try:
            sub_kids = list(ch.get_children(False))
        except Exception:
            pass

        if sub_kids:
            subdir = os.path.join(dirpath, name)
            if not dryrun and not os.path.isdir(subdir):
                os.makedirs(subdir)
                out_dirs_created.append(subdir)
            _mirror_walk(ch, subdir, out_dirs_created, out_files_written, dryrun)
            base = os.path.join(subdir, "__self__")
        else:
            base = os.path.join(dirpath, name)

        # Write a single combined .st file with declaration + a separator +
        # implementation. Easier to git-diff than two files.
        body = []
        if decl_text:
            body.append("(* === DECLARATION === *)")
            body.append(decl_text)
        if impl_text is not None:
            if body:
                body.append("")
            body.append("(* === IMPLEMENTATION === *)")
            body.append(impl_text)
        content = "\n".join(body).rstrip() + "\n"
        target = base + ".st"
        if not dryrun:
            try:
                f = open(target, "w")
                try:
                    f.write(content)
                finally:
                    f.close()
            except (IOError, OSError):
                continue
        out_files_written.append({"path": target, "bytes": len(content)})


@_registry.handler("project.export_xml")
def op_export_xml(args):
    """Export tree objects to a PLCopenXML file.

    Use with `project.import_xml` to build re-usable snippet libraries
    (devices, application skeletons, library reference sets, etc.).
    Export from a working project once, save the .xml next to your CI,
    then `import_xml` it into target projects.

    Args:
      path        absolute output path (required)
      objects     optional list of object names/paths to export. Empty
                  list or omitted = export the whole project.
      recursive   default True; include children of selected objects.
    """
    out_path = args.get("path")
    if not out_path:
        raise RuntimeError("project.export_xml: 'path' is required")
    proj = h.resolve_project(args)
    object_paths = args.get("objects") or []
    recursive = bool(args.get("recursive", True))

    if object_paths:
        objects = [h.find_object(proj, p) for p in object_paths]
    else:
        objects = list(proj.get_children(False))

    last_err = None
    out = None
    for call in (
        lambda: proj.export_xml(objects, out_path, recursive),
        lambda: proj.export_xml(objects, out_path),
        lambda: proj.export_xml(out_path, objects),
        lambda: proj.export_xml(out_path),
    ):
        try:
            out = call()
            break
        except TypeError as e:
            last_err = e
            continue
        except Exception as e:
            raise RuntimeError("project.export_xml: export failed: " + str(e))
    if out is None and last_err is not None:
        raise RuntimeError(
            "project.export_xml: no compatible signature; last: " + str(last_err)
        )

    import os as _os
    try:
        size = _os.path.getsize(out_path)
    except OSError:
        size = -1
    return {
        "exported": True,
        "path": out_path,
        "object_count": len(objects),
        "recursive": recursive,
        "size_bytes": size,
    }


@_registry.handler("project.import_xml")
def op_import_xml(args):
    """Import objects from PLCopenXML.

    The canonical way to add devices, complex POU trees, GVLs, etc.
    programmatically without invoking interactive IDE wizards. Most
    serious CODESYS workflows ship a small library of pre-baked
    PLCopenXML snippets and feed them through here.

    Args:
      path             absolute path to a .xml file (required)
      parent           optional path/name of the tree object to import
                       into. If omitted, imports at the project root.
      reporter         optional. Pass "silent" (default) to discard
                       progress callbacks; the IDE's interactive importer
                       is suppressed.
    """
    path = args.get("path")
    if not path:
        raise RuntimeError("project.import_xml: 'path' is required")
    if not os.path.exists(path):
        raise RuntimeError("project.import_xml: file does not exist: " + path)
    proj = h.resolve_project(args)
    parent_target = args.get("parent")
    parent = h.find_object(proj, parent_target) if parent_target else proj

    # SP22 has 4 import_xml overloads (verified via CLR reflection):
    #   (IImportReporter, String)
    #   (IImportReporter, String, Boolean)
    #   (ConflictResolve, String, Boolean)
    #   (String, Boolean)                  ← the simple one
    # Use signature 4 for the basic call (no conflict policy, no reporter).
    recursive = bool(args.get("recursive", True))
    try:
        out = parent.import_xml(path, recursive)
    except Exception as e:
        # Try the ConflictResolve variant with a sensible default.
        try:
            CR = h._g("ConflictResolve")
            # Common members on this enum (verified at runtime via probes):
            # Use, Copy, Skip, Replace, Replace_All_Existing, Use_All_Existing.
            policy = (
                getattr(CR, "Copy", None)
                or getattr(CR, "Use", None)
                or getattr(CR, "Replace", None)
            )
            if policy is None:
                raise RuntimeError(
                    "import_xml(path, recursive) failed and ConflictResolve enum "
                    "has no Copy/Use/Replace member: " + str(e)
                )
            out = parent.import_xml(policy, path, recursive)
        except Exception as e2:
            raise RuntimeError(
                "project.import_xml: import failed. Simple-form err: " + str(e)
                + "; conflict-resolve fallback err: " + str(e2)
            )
    return {
        "imported": True,
        "path": path,
        "parent": h._safe_name(parent),
        "result": _safe_str(out),
    }


def _safe_str(v):
    try:
        return str(v) if v is not None else None
    except Exception:
        return repr(v)


@_registry.handler("project.mirror_export")
def op_mirror_export(args):
    """Walk the project tree and emit a filesystem mirror of every textual
    object's source. Designed for git workflows — diff/review/merge the
    actual ST code instead of the binary .project file.

    Args:
      out_dir   absolute path of the output directory (required). Created
                if it doesn't exist; existing files are overwritten.
      clean     default False. If True, the output dir is wiped before
                exporting. Use carefully — won't touch anything outside
                `out_dir` but WILL delete its current contents.
      dryrun    default False. If True, returns the planned file list
                without writing anything.

    Returns:
      out_dir, files_written: [{path, bytes}], dirs_created: [path]
    """
    out_dir = args.get("out_dir")
    if not out_dir:
        raise RuntimeError("project.mirror_export: 'out_dir' is required")
    out_dir = os.path.abspath(out_dir)
    clean = bool(args.get("clean", False))
    dryrun = bool(args.get("dryrun", False))

    proj = h.resolve_project(args)

    if not dryrun:
        if clean and os.path.isdir(out_dir):
            import shutil as _shutil
            _shutil.rmtree(out_dir)
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)

    dirs_created = []
    files_written = []
    _mirror_walk(proj, out_dir, dirs_created, files_written, dryrun=dryrun)
    return {
        "out_dir": out_dir,
        "files_written": files_written,
        "file_count": len(files_written),
        "dirs_created": dirs_created,
        "total_bytes": sum(f.get("bytes", 0) for f in files_written),
        "dryrun": dryrun,
    }
