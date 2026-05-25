# -*- coding: utf-8 -*-
"""
Device Repository + device-tree handlers.

Ops:
  device.list_installed    search the system device repository
  device.categories        list category descriptors visible in the repo
  device.tree              list device nodes in the project with their versions
  device.add               add a device to the project tree
  device.update            change a device node's version (or type)

Device-tree manipulation IS supported by the SP22 script API — the methods
just don't show up under `dir()` because ScriptObject is a CLR proxy. Probed
at runtime, a device tree node exposes:

  node.add(name, device_id, module)                 # or (name, type, id, version, module)
  node.update(device_id, module)                    # or (type, id, version, module)
  node.get_device_identification() -> IDeviceId     # .type / .id / .version (lowercase)
  node.is_device                                    # bool

A DeviceId's attributes are lowercase: `.type` (int), `.id` (str like
'0000 100B'), `.version` (str like '4.5.1.0'). The repository device objects
expose the same IDeviceId via `.device_id`, so we resolve a target device by
name/type/id/version against the repo, then hand its `device_id` straight to
add/update.
"""
import _registry
import _codesys_helpers as h


def _ver_tuple(v):
    """Parse '4.5.1.0' -> (4,5,1,0) for sortable version comparison.
    DeviceID versions can have hex-ish segments; non-int parts collapse to 0."""
    out = []
    for part in str(v or "").replace(",", ".").split("."):
        part = part.strip()
        try:
            out.append(int(part))
        except ValueError:
            try:
                out.append(int(part, 16))
            except (ValueError, TypeError):
                out.append(0)
    return tuple(out)


def _device_id_dict(did):
    if did is None:
        return None
    return {
        "type": int(getattr(did, "type", -1)),
        "id": str(getattr(did, "id", "")),
        "version": str(getattr(did, "version", "")),
    }


def _device_summary(d):
    info = d.device_info if hasattr(d, "device_info") else None
    name = ""
    vendor = ""
    description = ""
    order_number = ""
    category_ids = []
    families = []
    if info is not None:
        try: name = str(getattr(info, "name", ""))
        except Exception: pass
        try: vendor = str(getattr(info, "vendor", ""))
        except Exception: pass
        try: description = str(getattr(info, "description", ""))
        except Exception: pass
        try: order_number = str(getattr(info, "order_number", ""))
        except Exception: pass
        try:
            cats = getattr(info, "categories", None)
            if cats is not None:
                category_ids = [int(c) for c in list(cats)]
        except Exception:
            pass
        try:
            fams = getattr(info, "families", None)
            if fams is not None:
                families = [str(f) for f in list(fams)]
        except Exception:
            pass
    did = d.device_id if hasattr(d, "device_id") else None
    device_id_str = ""
    type_int = None
    id_str = ""
    version = ""
    if did is not None:
        try: device_id_str = repr(did)[:200]
        except Exception: pass
        # DeviceId attributes are lowercase on SP22 (type/id/version) — the
        # PascalCase variants (.Id/.Version) raise AttributeError, which is why
        # this used to report blanks.
        try: type_int = int(getattr(did, "type", -1))
        except Exception: pass
        try: id_str = str(getattr(did, "id", ""))
        except Exception: pass
        try: version = str(getattr(did, "version", ""))
        except Exception: pass
    return {
        "name": name,
        "vendor": vendor,
        "description": description,
        "order_number": order_number,
        "category_ids": category_ids,
        "families": families,
        "device_id": {
            "type": type_int,
            "id": id_str,
            "version": version,
            "repr": device_id_str,
        },
    }


def _resolve_category(cat_id):
    """Look up a category's display info by integer id. Returns dict or None."""
    try:
        cat = h._g("device_repository").get_device_category(int(cat_id))
    except Exception:
        return None
    if cat is None:
        return None
    out = {"id": int(cat_id)}
    for attr in ("name", "description"):
        try:
            out[attr] = str(getattr(cat, attr, ""))
        except Exception:
            out[attr] = ""
    return out


@_registry.handler("device.list_installed")
def op_list_installed(args):
    """Enumerate devices in the system device repository.

    Filters (all optional, all case-insensitive substring matches unless
    noted):
      name        match against device_info.name
      vendor      match against device_info.vendor
      description match against device_info.description
      family      match against any of device_info.families
      category    integer category id (exact). Use device.categories to
                  enumerate them.
      keywords    list of strings; ALL must match (against name + vendor +
                  description joined). Easier than chaining multiple
                  patterns when you don't care which field matches.
      limit       max results (default 50, cap 500)
      resolve_categories  default True; include resolved category info
                          ({id, name, description}) in each result.

    Returns: {count, truncated, devices: [...]}.
    """
    name_pat = (args.get("name") or "").strip().lower()
    vendor_pat = (args.get("vendor") or "").strip().lower()
    desc_pat = (args.get("description") or "").strip().lower()
    family_pat = (args.get("family") or "").strip().lower()
    category_id = args.get("category")
    keywords = [str(k).strip().lower() for k in (args.get("keywords") or [])]
    keywords = [k for k in keywords if k]
    limit = min(int(args.get("limit") or 50), 500)
    resolve_cats = bool(args.get("resolve_categories", True))

    repo = h._g("device_repository")
    out = []
    cat_cache = {}

    for d in repo.get_all_devices():
        s = _device_summary(d)
        if name_pat and name_pat not in s["name"].lower():
            continue
        if vendor_pat and vendor_pat not in s["vendor"].lower():
            continue
        if desc_pat and desc_pat not in s["description"].lower():
            continue
        if family_pat:
            joined = " ".join(s["families"]).lower()
            if family_pat not in joined:
                continue
        if category_id is not None:
            try:
                if int(category_id) not in s["category_ids"]:
                    continue
            except (TypeError, ValueError):
                pass
        if keywords:
            hay = " ".join([s["name"], s["vendor"], s["description"]]).lower()
            if not all(k in hay for k in keywords):
                continue

        if resolve_cats and s["category_ids"]:
            resolved = []
            for cid in s["category_ids"]:
                if cid not in cat_cache:
                    cat_cache[cid] = _resolve_category(cid)
                if cat_cache[cid] is not None:
                    resolved.append(cat_cache[cid])
            s["categories"] = resolved

        out.append(s)
        if len(out) >= limit:
            return {"count": len(out), "truncated": True, "devices": out}

    return {"count": len(out), "truncated": False, "devices": out}


@_registry.handler("device.categories")
def op_categories(args):
    """List all device categories referenced by installed devices.

    The repo doesn't expose a direct `get_all_categories()` method, so
    we walk the device list, gather unique category IDs, and resolve
    each through `device_repository.get_device_category(id)`. Cached
    after the first call within a session if the watcher is reused.
    """
    repo = h._g("device_repository")
    seen = set()
    for d in repo.get_all_devices():
        try:
            cats = getattr(d.device_info, "categories", None)
            if cats is None:
                continue
            for c in cats:
                try:
                    seen.add(int(c))
                except (TypeError, ValueError):
                    pass
        except Exception:
            continue
    out = []
    for cid in sorted(seen):
        info = _resolve_category(cid)
        if info is not None:
            out.append(info)
    return {"count": len(out), "categories": out}


# ---------------------------------------------------------------------------
# Device-tree manipulation: tree / add / update
# ---------------------------------------------------------------------------


def _is_device_node(obj):
    try:
        return bool(getattr(obj, "is_device", False))
    except Exception:
        return False


def _node_device_id(obj):
    """Return the IDeviceId object for a device node, or None."""
    try:
        if hasattr(obj, "get_device_identification"):
            return obj.get_device_identification()
    except Exception:
        pass
    return None


def _walk_device_nodes(proj):
    """Yield (path, node, device_id_dict) for every device node in the tree."""
    out = []

    def visit(obj, prefix):
        try:
            children = list(obj.get_children(False))
        except Exception:
            children = []
        for ch in children:
            name = h._safe_name(ch)
            path = prefix + "/" + name if prefix else name
            if _is_device_node(ch):
                out.append((path, ch, _device_id_dict(_node_device_id(ch))))
            visit(ch, path)

    visit(proj, "")
    return out


def _resolve_repo_devices(name=None, type_=None, id_=None, version=None):
    """Find matching devices in the repository.

    Returns a list of dicts: {device_obj, device_id_obj, name, type, id, version}.
    `name` is a case-insensitive substring; type/id/version are exact when given.
    """
    repo = h._g("device_repository")
    matches = []
    nl = (name or "").strip().lower()
    for d in repo.get_all_devices():
        did = getattr(d, "device_id", None)
        if did is None:
            continue
        info = getattr(d, "device_info", None)
        dname = ""
        if info is not None:
            try:
                dname = str(getattr(info, "name", ""))
            except Exception:
                dname = ""
        dtype = int(getattr(did, "type", -1))
        d_id = str(getattr(did, "id", ""))
        dver = str(getattr(did, "version", ""))
        if nl and nl not in dname.lower():
            continue
        if type_ is not None and int(type_) != dtype:
            continue
        if id_ is not None and str(id_) != d_id:
            continue
        if version is not None and str(version) != dver:
            continue
        matches.append({
            "device_obj": d,
            "device_id_obj": did,
            "name": dname,
            "type": dtype,
            "id": d_id,
            "version": dver,
        })
    return matches


def _pick_highest(matches):
    """From repo matches, return the one with the highest version."""
    if not matches:
        return None
    return sorted(matches, key=lambda m: _ver_tuple(m["version"]))[-1]


def _iter_device_params(dev):
    """Yield (connector_id, parameter) for every host parameter of a device,
    across all its connectors. Device config + I/O channels both live here."""
    try:
        connectors = list(dev.connectors)
    except Exception:
        return
    for cn in connectors:
        cid = ""
        try:
            cid = str(cn.connector_id)
        except Exception:
            pass
        try:
            params = list(cn.host_parameters)
        except Exception:
            params = []
        for p in params:
            yield cid, p


def _param_summary(cid, p):
    def g(attr, default=""):
        try:
            return getattr(p, attr)
        except Exception:
            return default
    out = {
        "connector_id": cid,
        "name": str(g("name")),
        "value": str(g("value")),
        "iec_type": str(g("iec_type")),
        "is_mappable_io": bool(g("is_mappable_io", False)),
    }
    # Channel/mapping metadata only matters for mappable I/O.
    if out["is_mappable_io"]:
        out["channel_type"] = str(g("channel_type"))
        iom = g("io_mapping", None)
        out["mapped"] = iom is not None
    desc = str(g("description"))
    if desc:
        out["description"] = desc[:160]
    return out


def _resolve_device(proj, args):
    """Resolve a device node by `target`/`device` path, else the sole root PLC."""
    path = args.get("target") or args.get("device")
    if path:
        node = h.find_object(proj, path)
        if not _is_device_node(node):
            raise RuntimeError("'%s' is not a device node." % h._safe_name(node))
        return node
    return _resolve_target_node(proj, args)


@_registry.handler("device.parameters")
def op_parameters(args):
    """List a device's parameters across its connectors.

    Covers both configuration parameters (IP address, device name, comms
    settings) and I/O channels (where `is_mappable_io` is true). Filters:
      name          case-insensitive substring on parameter name
      mappable_only default False; only channels that can be I/O-mapped
      limit         cap (default 200)
    """
    proj = h.resolve_project(args)
    dev = _resolve_device(proj, args)
    name_pat = (args.get("name") or "").strip().lower()
    mappable_only = bool(args.get("mappable_only", False))
    limit = min(int(args.get("limit") or 200), 1000)
    out = []
    for cid, p in _iter_device_params(dev):
        s = _param_summary(cid, p)
        if name_pat and name_pat not in s["name"].lower():
            continue
        if mappable_only and not s["is_mappable_io"]:
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return {"device": h._safe_name(dev), "count": len(out), "parameters": out}


@_registry.handler("device.set_parameter")
def op_set_parameter(args):
    """Set a device parameter's value by name.

    Value is the parameter's textual representation, e.g. IPAddress
    `"[192, 168, 0, 10]"`, DeviceName `"'plc1'"`, WebServer `"true"`. Use
    device.parameters to see current values and the exact format. Returns
    before/after.
    """
    proj = h.resolve_project(args)
    dev = _resolve_device(proj, args)
    name = args.get("name")
    if not name:
        raise RuntimeError("device.set_parameter: 'name' is required.")
    if "value" not in args:
        raise RuntimeError("device.set_parameter: 'value' is required.")
    new_value = args.get("value")
    target_cid = args.get("connector")
    for cid, p in _iter_device_params(dev):
        try:
            pname = str(p.name)
        except Exception:
            continue
        if pname != name:
            continue
        if target_cid is not None and cid != str(target_cid):
            continue
        before = str(getattr(p, "value", ""))
        p.value = new_value
        after = str(getattr(p, "value", ""))
        return {
            "device": h._safe_name(dev),
            "connector_id": cid,
            "name": name,
            "before": before,
            "after": after,
            "changed": before != after,
        }
    raise RuntimeError(
        "parameter %r not found on device '%s' (see device.parameters)"
        % (name, h._safe_name(dev))
    )


@_registry.handler("device.tree")
def op_tree(args):
    """List every device node in the project with its current type/id/version.

    The starting point for any device fix: it shows what's plugged where and
    at which version, so the caller knows what `device.update` should target.
    """
    proj = h.resolve_project(args)
    nodes = _walk_device_nodes(proj)
    out = []
    for path, _node, did in nodes:
        out.append({"path": path, "device_id": did})
    return {"count": len(out), "devices": out}


def _resolve_target_node(proj, args):
    """Resolve the node a device op targets.

    `parent`/`target` (path) wins. The sentinels "/", "root", and "project"
    mean the project root itself (where top-level PLCs are added). Otherwise
    default to the sole device node at the project root (the PLC) when there's
    exactly one; else raise so the caller disambiguates.
    """
    path = args.get("target") or args.get("parent")
    if path is not None and str(path).strip().lower() in ("/", "root", "project"):
        return proj
    if path:
        return h.find_object(proj, path)
    # Default: single root-level device node.
    roots = []
    try:
        for c in proj.get_children(False):
            if _is_device_node(c):
                roots.append(c)
    except Exception:
        pass
    if len(roots) == 1:
        return roots[0]
    if not roots:
        raise RuntimeError(
            "No device node found at project root. Pass 'parent'/'target' "
            "with a tree path (see device.tree)."
        )
    names = [h._safe_name(r) for r in roots]
    raise RuntimeError(
        "Multiple root device nodes (" + ", ".join(names)
        + "); pass 'parent'/'target' to disambiguate."
    )


@_registry.handler("device.add")
def op_add(args):
    """Add a device to the project tree.

    Args:
      name        instance name for the new node (required).
      parent      tree path of the node to add under (default: the single
                  root device / PLC). Fieldbus devices must go under a
                  compatible bus node — see device.tree for the structure.
      device_name repo device name (substring) to resolve the device id, OR
      type/id/version  explicit DeviceId triple (from device.list_installed).
      module      optional module string (default '').

    Resolution: if device_name is given, the highest-version repo match is
    used unless `version` is also specified. Returns the added node's
    device_id as confirmation.
    """
    proj = h.resolve_project(args)
    instance_name = args.get("name")
    if not instance_name:
        raise RuntimeError("device.add: 'name' (instance name) is required.")
    module = args.get("module") or ""

    type_ = args.get("type")
    d_id = args.get("id")
    version = args.get("version")
    device_name = args.get("device_name")

    if type_ is not None and d_id is not None:
        # Explicit triple — version may still be None (means 'any/highest' if
        # we resolve, but add needs a concrete version, so resolve it).
        matches = _resolve_repo_devices(type_=type_, id_=d_id, version=version)
        if not matches:
            raise RuntimeError(
                "No repo device for type=%s id=%s version=%s" % (type_, d_id, version)
            )
        chosen = matches[0] if version is not None else _pick_highest(matches)
    elif device_name:
        matches = _resolve_repo_devices(name=device_name, version=version)
        if not matches:
            raise RuntimeError("No repo device matching name=%r" % device_name)
        chosen = matches[0] if version is not None else _pick_highest(matches)
    else:
        raise RuntimeError(
            "device.add: provide 'device_name' or ('type' and 'id')."
        )

    parent = _resolve_target_node(proj, args)
    # Use the explicit-triple overload — most robust across SPs.
    try:
        parent.add(instance_name, int(chosen["type"]), str(chosen["id"]),
                   str(chosen["version"]), module)
    except Exception as exc:
        msg = str(exc)
        if "cannot be added" in msg.lower() or "inserted here" in msg.lower():
            # CODESYS enforces device-model compatibility: a fieldbus device
            # (EtherNet/IP scanner, Modbus master) must go under a compatible
            # bus/adapter node, not an arbitrary parent. Give the LLM the next
            # move instead of a bare CLR exception.
            raise RuntimeError(
                "CODESYS rejected adding '%s' (type=%s) under '%s': %s. That "
                "parent doesn't accept this device type. Fieldbus devices need "
                "a compatible parent (e.g. an EtherNet/IP scanner goes under an "
                "Ethernet adapter, which goes under a PLC that exposes a "
                "fieldbus slot). Call device.tree to inspect the structure and "
                "pass a valid 'parent'. Note: the legacy PLCWinNT soft PLC "
                "(device id '0000 0001') does not expose fieldbus slots; the "
                "'CODESYS Control Win V3' device does."
                % (chosen["name"], chosen["type"], h._safe_name(parent), msg)
            )
        raise
    return {
        "added": True,
        "name": instance_name,
        "parent": h._safe_name(parent),
        "device": {
            "name": chosen["name"],
            "type": chosen["type"],
            "id": chosen["id"],
            "version": chosen["version"],
        },
    }


@_registry.handler("device.update")
def op_update(args):
    """Change a device node's version (or swap its type/id).

    The fix for device-descriptor library pins: an old device version (e.g.
    PLCWinNT 3.1.3.0) drags in an old library reference (IoStandard 3.1.3.1)
    that may not be installed. Updating the device to a newer installed version
    re-points it at current libraries.

    Args:
      target      tree path of the device node (default: the single root PLC).
      version     desired version (default: highest installed for the same
                  type+id).
      type/id     optional — swap to a different device entirely.
      module      optional module string (default '').
    """
    proj = h.resolve_project(args)
    node = _resolve_target_node(proj, args)
    if not _is_device_node(node):
        raise RuntimeError(
            "Target '%s' is not a device node." % h._safe_name(node)
        )
    module = args.get("module") or ""
    cur = _device_id_dict(_node_device_id(node))

    want_type = args.get("type", cur["type"] if cur else None)
    want_id = args.get("id", cur["id"] if cur else None)
    want_version = args.get("version")  # None => highest available

    matches = _resolve_repo_devices(type_=want_type, id_=want_id, version=want_version)
    if not matches:
        # Surface what versions ARE available to guide the caller.
        avail = _resolve_repo_devices(type_=want_type, id_=want_id)
        raise RuntimeError(
            "No repo device for type=%s id=%s version=%s. Available versions: %s"
            % (want_type, want_id, want_version,
               sorted(set(m["version"] for m in avail)))
        )
    chosen = matches[0] if want_version is not None else _pick_highest(matches)

    node.update(int(chosen["type"]), str(chosen["id"]),
                str(chosen["version"]), module)
    return {
        "updated": True,
        "target": h._safe_name(node),
        "from": cur,
        "to": {
            "type": chosen["type"],
            "id": chosen["id"],
            "version": chosen["version"],
        },
    }
