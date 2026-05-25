# -*- coding: utf-8 -*-
"""
Online / runtime handlers — connect to a PLC, download, control, read/write.

Ops:
  online.login            log into the target device (download or online change)
  online.logout
  online.start
  online.stop
  online.reset            cold / warm / origin
  online.state            current app + operation state
  online.read             read one or more IEC expressions
  online.write            write one or more IEC expressions (commit prepared)
  online.force            hold expression at a value (force)
  online.unforce_all
  online.create_boot      build boot application on target
  online.source_download  embed source archive on target

Login modes (map to OnlineChangeOption — verified member names on SP22):
  download / never  -> OnlineChangeOption.Never  (full download, no online change)
  try               -> OnlineChangeOption.Try    (online change if possible, else download)
  force             -> OnlineChangeOption.Force  (force online change)
  keep              -> OnlineChangeOption.Keep    (keep current code on target)
"""
import time
import _registry
import _codesys_helpers as h


# Stable CODESYS type GUID for IEC Application objects. The tree object's
# `.type` is a GUID string, NOT a human name, so substring-matching
# "application" never worked — match the GUID instead.
_APP_TYPE_GUID = "639b491f-5557-464c-af91-1471bac9f549"


def _is_application(obj):
    if _codesys_helpers_type_guid(obj) == _APP_TYPE_GUID:
        return True
    # Fallback: name == "Application" and not a folder.
    try:
        if h._safe_name(obj) == "Application" and not obj.is_folder:
            return True
    except Exception:
        pass
    return False


def _codesys_helpers_type_guid(obj):
    try:
        return str(getattr(obj, "type", "")).lower()
    except Exception:
        return ""


def _resolve_app(args):
    proj = h.resolve_project(args)
    apps = []

    def visit(obj):
        try:
            children = list(obj.get_children(False))
        except Exception:
            children = []
        for ch in children:
            if _is_application(ch):
                apps.append(ch)
            visit(ch)

    visit(proj)
    name = args.get("application")
    if name:
        # Disambiguate by the app name OR the parent device name (handy when
        # several PLCs each have an 'Application').
        for a in apps:
            if h._safe_name(a) == name:
                return a
            try:
                parent = a.parent
                # walk up to the device node
                while parent is not None and not getattr(parent, "is_device", False):
                    parent = getattr(parent, "parent", None)
                if parent is not None and h._safe_name(parent) == name:
                    return a
            except Exception:
                pass
        raise RuntimeError("Application not found: " + name)
    if not apps:
        raise RuntimeError("No Application object found in project tree.")
    if len(apps) > 1:
        names = [h._safe_name(a) for a in apps]
        raise RuntimeError(
            "Multiple applications (" + ", ".join(names)
            + "). Pass 'application' (app name or owning device name) to "
            "disambiguate."
        )
    return apps[0]


def _online_app(args):
    app = _resolve_app(args)
    oapp = h.online_obj().create_online_application(app)
    return app, oapp


def _device_for_app(app):
    """Walk up from an Application to its owning device node, or None."""
    node = app
    for _ in range(20):  # bounded — avoid any pathological cycle
        if node is None:
            return None
        try:
            if getattr(node, "is_device", False):
                return node
        except Exception:
            pass
        node = getattr(node, "parent", None)
    return None


def _apply_credentials(args, app):
    """Set device credentials before a login, if provided.

    On a fresh SP22 runtime the device has no user yet and enforces a password
    policy, so the first connect needs:
      - `set_default_credentials(user, pw)` so login authenticates, AND
      - `set_credentials_for_initial_user(...)` to CREATE that first user
        (when `setup_initial_user` is true, the default for a first download).
    Both are no-ops if the runtime already has matching users.
    """
    user = args.get("username")
    pw = args.get("password")
    if not user or pw is None:
        return {"credentials": "not_provided"}
    online = h.online_obj()
    online.set_default_credentials(user, pw)
    out = {"default_credentials": "set", "username": user}
    if args.get("setup_initial_user", False):
        dev = _device_for_app(app)
        if dev is not None:
            try:
                odev = online.create_online_device(dev)
                odev.set_credentials_for_initial_user(
                    user, pw,
                    bool(args.get("can_change_password", True)),
                    bool(args.get("must_change_password", False)),
                )
                out["initial_user"] = "set"
            except Exception as exc:  # noqa: BLE001
                # Already-provisioned runtimes reject re-creating the user;
                # that's fine — default_credentials still authenticate.
                out["initial_user"] = "skipped: " + str(exc)[:120]
    return out


def _login_mode(s):
    """Map a friendly mode string to OnlineChangeOption.

    SP22 members are Never / Try / Force / Keep (verified at runtime) — the
    old code mapped to `login_on_download_no_change*` names that don't exist,
    so every login raised. Default 'never' = full download, no online change.
    """
    OLM = h._g("OnlineChangeOption")
    m = (s or "never").strip().lower()
    mapping = {
        # full download, no online change (the safe default for first download)
        "never": getattr(OLM, "Never", None),
        "download": getattr(OLM, "Never", None),
        "login_on_download_no_change_no_oc": getattr(OLM, "Never", None),
        "login_on_download_no_change": getattr(OLM, "Never", None),
        # online change if possible, else download
        "try": getattr(OLM, "Try", None),
        "online_change": getattr(OLM, "Try", None),
        "login_with_online_change": getattr(OLM, "Try", None),
        # force online change
        "force": getattr(OLM, "Force", None),
        # keep current code on the target
        "keep": getattr(OLM, "Keep", None),
    }
    v = mapping.get(m)
    if v is None:
        raise RuntimeError(
            "Unknown login mode " + repr(m)
            + ". Use one of: never/download, try/online_change, force, keep."
        )
    return v


@_registry.handler("online.set_credentials")
def op_set_credentials(args):
    """Provision device-user credentials for the target runtime.

    Required before the first login to an SP22 soft PLC, which ships with no
    user and enforces a password policy (8+ chars, mixed case, digit, special).
    Pass `username` + `password`; `setup_initial_user` (default true) also
    creates that user on the runtime if it has none yet."""
    app = _resolve_app(args)
    result = _apply_credentials(args, app)
    return result


@_registry.handler("online.login")
def op_login(args):
    app, oapp = _online_app(args)
    cred = _apply_credentials(args, app)
    mode = _login_mode(args.get("mode"))
    delete_foreign = bool(args.get("delete_foreign_apps", True))
    try:
        oapp.login(mode, delete_foreign)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        low = msg.lower()
        creds_given = bool(args.get("username"))
        # Auth/policy failures should steer the caller to ASK THE USER for
        # credentials rather than invent any. We never auto-create a user here.
        if any(k in low for k in ("password", "credential", "user name",
                                  "username", "authentication", "not authorized",
                                  "login failed")):
            hint = (
                " — the runtime requires authentication. Ask the USER for the "
                "device username and password and pass them as `username`/"
                "`password`. Do NOT invent credentials. To CREATE a new device "
                "user on a fresh runtime, the user must explicitly opt in via "
                "`setup_initial_user: true` with a password they choose "
                "(policy: 8+ chars, upper+lower+digit+special)."
            )
            if creds_given:
                hint = (
                    " — the supplied credentials were rejected (wrong user/"
                    "password, or password fails the runtime policy). Ask the "
                    "USER to confirm the correct device credentials."
                )
            raise RuntimeError("Login failed" + hint + " [detail: " + msg[:300] + "]")
        raise
    return {
        "logged_in": True,
        "state": str(getattr(oapp, "application_state", "")),
        "credentials": cred,
    }


@_registry.handler("online.logout")
def op_logout(args):
    _, oapp = _online_app(args)
    oapp.logout()
    return {"logged_in": False}


@_registry.handler("online.start")
def op_start(args):
    _, oapp = _online_app(args)
    oapp.start()
    return {"started": True, "state": str(getattr(oapp, "application_state", ""))}


@_registry.handler("online.stop")
def op_stop(args):
    _, oapp = _online_app(args)
    oapp.stop()
    return {"stopped": True, "state": str(getattr(oapp, "application_state", ""))}


@_registry.handler("online.reset")
def op_reset(args):
    _, oapp = _online_app(args)
    kind = (args.get("kind") or "warm").strip().lower()
    force_kill = bool(args.get("force_kill", True))
    # SP22 ResetOption uses PascalCase members (Cold/Warm/Original) — verified
    # at runtime. reset() signature is reset(reset_option, force_kill) — the
    # second arg was missing before, which raised TypeError on every call.
    try:
        RT = h._g("ResetOption")
        mapping = {
            "warm": getattr(RT, "Warm", None),
            "cold": getattr(RT, "Cold", None),
            "origin": getattr(RT, "Original", None),
            "original": getattr(RT, "Original", None),
        }
        opt = mapping.get(kind)
        if opt is None:
            raise RuntimeError("Unknown reset kind: " + repr(kind))
        oapp.reset(opt, force_kill)
    except RuntimeError:
        # If ResetOption isn't exposed (older SPs), try the convenience methods.
        if kind == "warm" and hasattr(oapp, "reset_warm"):
            oapp.reset_warm()
        elif kind == "cold" and hasattr(oapp, "reset_cold"):
            oapp.reset_cold()
        elif kind in ("origin", "original") and hasattr(oapp, "reset_origin"):
            oapp.reset_origin()
        else:
            raise
    return {"reset": kind, "state": str(getattr(oapp, "application_state", ""))}


@_registry.handler("online.state")
def op_state(args):
    _, oapp = _online_app(args)
    return {
        "application_state": str(getattr(oapp, "application_state", "")),
        "operation_state": str(getattr(oapp, "operation_state", "")),
        "is_logged_in": bool(getattr(oapp, "is_logged_in", False)),
    }


@_registry.handler("online.read")
def op_read(args):
    _, oapp = _online_app(args)
    exprs = args.get("expressions")
    if not exprs:
        single = args.get("expression")
        if not single:
            raise RuntimeError("online.read: provide 'expression' or 'expressions'")
        return {"values": {single: _value_to_jsonable(oapp.read_value(single))}}
    values = oapp.read_values(list(exprs))
    out = {}
    for expr, val in zip(exprs, values):
        out[expr] = _value_to_jsonable(val)
    return {"values": out}


@_registry.handler("online.write")
def op_write(args):
    _, oapp = _online_app(args)
    writes = args.get("writes")
    if not writes:
        expr = args.get("expression")
        val = args.get("value")
        if expr is None or val is None:
            raise RuntimeError("online.write: provide 'writes' or both 'expression' and 'value'")
        writes = {expr: val}
    for expr, val in writes.items():
        oapp.set_prepared_value(expr, str(val))
    oapp.write_prepared_values()
    return {"written": list(writes.keys())}


@_registry.handler("online.force")
def op_force(args):
    _, oapp = _online_app(args)
    forces = args.get("forces")
    if not forces:
        expr = args.get("expression")
        val = args.get("value")
        if expr is None or val is None:
            raise RuntimeError("online.force: provide 'forces' or both 'expression' and 'value'")
        forces = {expr: val}
    for expr, val in forces.items():
        oapp.set_prepared_value(expr, str(val))
    oapp.force_prepared_values()
    return {"forced": list(forces.keys())}


@_registry.handler("online.snapshot")
def op_snapshot(args):
    """Read several IEC expressions at one instant and stamp the result with a
    timestamp + application state — a monitoring snapshot. For structs/arrays,
    list each member/element expression (e.g. 'PLC_PRG.st.member', 'arr[1]');
    SP22 read returns one string per expression."""
    _, oapp = _online_app(args)
    exprs = args.get("expressions")
    if not exprs:
        single = args.get("expression")
        if not single:
            raise RuntimeError("online.snapshot: provide 'expressions' (list) or 'expression'")
        exprs = [single]
    exprs = list(exprs)
    values = oapp.read_values(exprs)
    out = {}
    for expr, val in zip(exprs, values):
        out[expr] = _value_to_jsonable(val)
    return {
        "timestamp": time.time(),
        "application_state": str(getattr(oapp, "application_state", "")),
        "values": out,
    }


@_registry.handler("online.forced")
def op_forced(args):
    """List the expressions currently FORCED and PREPARED on the target —
    read-only. Useful for safety/audit before a run (forces override program
    logic on outputs)."""
    _, oapp = _online_app(args)

    def _lst(getter_name):
        try:
            return [str(x) for x in getattr(oapp, getter_name)()]
        except Exception:
            return []

    return {
        "forced": _lst("get_forced_expressions"),
        "prepared": _lst("get_prepared_expressions"),
    }


@_registry.handler("online.unforce_all")
def op_unforce(args):
    _, oapp = _online_app(args)
    oapp.unforce_all_values()
    return {"unforced_all": True}


@_registry.handler("online.create_boot")
def op_create_boot(args):
    _, oapp = _online_app(args)
    oapp.create_boot_application()
    return {"boot_created": True}


@_registry.handler("online.source_download")
def op_source_download(args):
    _, oapp = _online_app(args)
    oapp.source_download()
    return {"source_downloaded": True}


def _value_to_jsonable(v):
    """Coerce IronPython-side values to something json.dumps can take."""
    if v is None:
        return None
    if isinstance(v, (int, float, bool)):
        return v
    try:
        return str(v)
    except Exception:
        return repr(v)
