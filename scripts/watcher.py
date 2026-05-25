# -*- coding: utf-8 -*-
"""
CODESYS-side watcher. Runs INSIDE CODESYS.exe via:

    CODESYS.exe --profile="CODESYS V3.5 SP22" --runscript=watcher.py
                --scriptargs="<workdir>"

IronPython 2.7. The CODESYS Scripting API is exposed as globals
(`projects`, `system`, `online`, `device_repository`, ...). We access them
through `_codesys_helpers` so they're easy to mock for offline syntax checks.

Design rules (do not break):
  - Run on the primary UI thread only. Yield via `system.delay(ms)`.
  - Do NOT use System.Threading, ManualResetEvent, or any background thread.
  - Do NOT call `system.execute_on_primary_thread` — removed in SP21 P5.
  - Catch KeyboardInterrupt SEPARATELY from Exception; in IronPython 2.7
    KeyboardInterrupt is NOT an Exception subclass.
  - Never `print`; the IDE redirects stdout unpredictably. Use system.write_message.
"""
from __future__ import print_function

import os
import sys
import json
import time
import traceback

# Make sibling modules importable. CODESYS does add the script dir to sys.path
# but be defensive.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Make CODESYS-injected globals (`projects`, `system`, `online`, ...) visible
# to the helper module that all handlers go through. CODESYS injects them
# into THIS module's globals; imported modules don't see them unless we
# explicitly share the dict.
import _codesys_helpers as _codesys_helpers  # noqa: E402
_codesys_helpers.register_injected(globals())

import _registry  # noqa: E402


# ----------------------------------------------------------------------------
# Severity / logging
# ----------------------------------------------------------------------------

try:
    Severity  # noqa: F821
except NameError:
    class _Sev(object):
        Information = 0
        Warning = 1
        Error = 2
    Severity = _Sev()


# Structured JSON-line log. Each call to log_info/warn/error emits ONE line
# to <workdir>/log/watcher.log AND to the IDE Messages window. The file
# rotates at ~5MB by simple rename — readers should tail watcher.log first
# and watcher.log.1 second if they need history.
_LOG_FILE_PATH = None  # set by _init_logging once workdir is known
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_PID = None


def _init_logging(workdir):
    """Open the structured log file under <workdir>/log/. Idempotent."""
    global _LOG_FILE_PATH, _LOG_PID
    try:
        log_dir = os.path.join(workdir, "log")
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        _LOG_FILE_PATH = os.path.join(log_dir, "watcher.log")
        _LOG_PID = os.getpid() if hasattr(os, "getpid") else 0
    except Exception:
        _LOG_FILE_PATH = None


# ----------------------------------------------------------------------------
# Heartbeat — the host's liveness signal.
#
# A single-threaded primary-thread watcher cannot update anything while it's
# stuck inside a blocking call (a modal dialog, a hung build). That's exactly
# what makes a heartbeat useful: a stale heartbeat means "hung", a fresh one
# means "alive". The host (watcher_manager) reads <workdir>/watcher.heartbeat
# to decide whether to adopt, wait, or kill+respawn.
#
# We record what the watcher is doing (state/op/deadline) so the host can tell
# a legitimately-busy watcher (long build within its deadline) apart from a
# truly-hung one (busy past its deadline, or idle but not ticking).
# ----------------------------------------------------------------------------

_HEARTBEAT_PATH = None
_HEARTBEAT_MIN_INTERVAL_S = 2.0  # throttle idle writes to avoid disk thrash
_last_heartbeat_ts = 0.0


def _init_heartbeat(workdir):
    global _HEARTBEAT_PATH
    _HEARTBEAT_PATH = os.path.join(workdir, "watcher.heartbeat")


def _write_heartbeat(state, op=None, deadline_s=None, op_started_ts=None, force=False):
    """Write the heartbeat file (best-effort, never raises).

    `state` is "idle" or "busy". When busy, `op`/`deadline_s`/`op_started_ts`
    let the host respect the in-flight op's own deadline before declaring a
    hang. Idle writes are throttled; busy/idle transitions force a write.
    """
    global _last_heartbeat_ts
    if _HEARTBEAT_PATH is None:
        return
    now = time.time()
    if not force and (now - _last_heartbeat_ts) < _HEARTBEAT_MIN_INTERVAL_S:
        return
    _last_heartbeat_ts = now
    record = {
        "pid": _LOG_PID,
        "ts": now,
        "state": state,
        "op": op,
        "deadline_s": deadline_s,
        "op_started_ts": op_started_ts,
    }
    tmp = _HEARTBEAT_PATH + ".tmp"
    try:
        f = open(tmp, "w")
        try:
            f.write(json.dumps(record))
        finally:
            f.close()
        if os.path.exists(_HEARTBEAT_PATH):
            os.remove(_HEARTBEAT_PATH)
        os.rename(tmp, _HEARTBEAT_PATH)
    except (IOError, OSError, ValueError, TypeError):
        pass


def _maybe_rotate():
    if _LOG_FILE_PATH is None:
        return
    try:
        if os.path.exists(_LOG_FILE_PATH) and os.path.getsize(_LOG_FILE_PATH) > _LOG_MAX_BYTES:
            backup = _LOG_FILE_PATH + ".1"
            try:
                if os.path.exists(backup):
                    os.remove(backup)
            except (IOError, OSError):
                pass
            try:
                os.rename(_LOG_FILE_PATH, backup)
            except (IOError, OSError):
                pass
    except (IOError, OSError):
        pass


def _write_log_line(level, msg, extra=None):
    """Append one JSON-encoded log record. Best-effort — never raises."""
    if _LOG_FILE_PATH is None:
        return
    record = {
        "ts": time.time(),
        "level": level,
        "pid": _LOG_PID,
        "msg": str(msg),
    }
    if extra:
        try:
            record["extra"] = extra
        except Exception:
            pass
    try:
        _maybe_rotate()
        f = open(_LOG_FILE_PATH, "a")
        try:
            f.write(json.dumps(record) + "\n")
        finally:
            f.close()
    except (IOError, OSError, ValueError, TypeError):
        # Don't let log failures take the watcher down.
        pass


def log_info(msg, extra=None):
    """Emit to the IDE Messages window AND the structured log."""
    try:
        system.write_message(Severity.Information, "[mcp] " + str(msg))  # noqa: F821
    except Exception:
        pass
    _write_log_line("info", msg, extra)


def log_warn(msg, extra=None):
    try:
        system.write_message(Severity.Warning, "[mcp] " + str(msg))  # noqa: F821
    except Exception:
        pass
    _write_log_line("warn", msg, extra)


def log_error(msg, extra=None):
    try:
        system.write_message(Severity.Error, "[mcp] " + str(msg))  # noqa: F821
    except Exception:
        pass
    _write_log_line("error", msg, extra)


# ----------------------------------------------------------------------------
# IPC plumbing
# ----------------------------------------------------------------------------


def _read_workdir_from_args():
    candidates = [a for a in sys.argv if a and not a.endswith("watcher.py")]
    for c in candidates:
        if os.path.isabs(c) and not c.endswith(".py"):
            return c
    raise SystemExit("watcher: could not determine workdir from " + repr(sys.argv))


def _atomic_write(path, payload):
    tmp = path + ".tmp"
    f = open(tmp, "w")
    try:
        f.write(payload)
    finally:
        f.close()
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def _safe_read_json(path):
    try:
        f = open(path, "r")
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


# ----------------------------------------------------------------------------
# Inline handlers (ping, shutdown). Topical handlers come from handlers/*.
# ----------------------------------------------------------------------------


_DIAG_ENUMS = (
    "PouType", "DutType", "ImplementationLanguages", "ScriptImplementationLanguage",
    "OnlineChangeOption", "ResetOption", "Severity",
    "SymbolAccess", "SymbolConfigContentFeatureFlags", "AccessRight",
    "ApplicationState", "OperatingState", "ArchiveCategories",
)


def _enum_members():
    """Diagnostic: list public attribute names of every known-injected enum."""
    out = {}
    for n in _DIAG_ENUMS:
        try:
            obj = _codesys_helpers._g(n)
        except Exception:
            continue
        members = sorted(a for a in dir(obj) if not a.startswith("_"))
        out[n] = members
    return out


@_registry.handler("ping")
def _ping(args):
    info = {
        "pong": True,
        "echo": args.get("echo"),
        "ironpython_version": sys.version,
        "codesys_version": _safe_codesys_version(),
        "registered_ops": _registry.names(),
    }
    # Diagnostic surface — only emit when explicitly requested. Avoids
    # ~10KB of per-ping payload that an LLM would otherwise have to
    # re-process on every check.
    if args.get("verbose"):
        info["injected_globals"] = _codesys_helpers.list_injected()
        info["enum_members"] = _enum_members()
    return info


def _safe_codesys_version():
    """Best-effort CODESYS version probe. SP22 doesn't expose `system.version`
    as a plain attribute; instead we try a few known accessors and fall back
    to None. Any of these may legitimately be absent on a given SP."""
    candidates = (
        # Attribute paths to try, in order. Each tuple is (root, attrs).
        ("system", ("version",)),
        ("system", ("get_version",)),  # method
        ("system", ("product_info",)),
        ("system", ("Version",)),
        ("Version", ()),  # the standalone Version object some SPs inject
    )
    for root_name, attrs in candidates:
        try:
            obj = _codesys_helpers._g(root_name)
        except Exception:
            continue
        try:
            for a in attrs:
                obj = getattr(obj, a)
            # If it's callable, call it (no-args).
            if callable(obj):
                obj = obj()
            if obj is None:
                continue
            return str(obj)
        except Exception:
            continue
    return None


# Diagnostic ops (_introspect, _eval) are gated behind the MCPTOOLKIT_DEV
# env var OR a `<workdir>/dev.flag` sentinel file in the workdir. Either
# mechanism enables them. They're powerful (especially _eval, which evals
# arbitrary IronPython in the watcher's namespace) and were only useful
# during the Phase 1-3 discovery work. Default off; production
# deployments shouldn't have them registered.
def _dev_ops_enabled():
    if os.environ.get("MCPTOOLKIT_DEV") == "1":
        return True
    workdir = _read_workdir_from_args()
    return os.path.exists(os.path.join(workdir, "dev.flag"))

_DEV_OPS_ENABLED = _dev_ops_enabled()


def _maybe_dev_handler(op_name):
    """Decorator: register the handler only if dev ops are enabled."""
    def _wrap(fn):
        if _DEV_OPS_ENABLED:
            _registry.handler(op_name)(fn)
        return fn
    return _wrap


@_maybe_dev_handler("_introspect")
def _introspect(args):
    """Diagnostic: dir() of one or more injected globals. Returns
    {name: {type: <type_name>, members: [public attrs]}} or {name: {error: ...}}.

    Used during development to discover the actual SP22 API surface so we
    can map handler helpers to real method names. Not exposed as an MCP
    tool — called via direct file IPC during fix cycles. Set
    MCPTOOLKIT_DEV=1 in Claude Desktop's `env` block to enable.
    """
    names = args.get("names") or []
    out = {}
    for n in names:
        try:
            obj = _codesys_helpers._g(n)
        except Exception as e:
            out[n] = {"error": str(e)}
            continue
        try:
            type_name = type(obj).__name__
        except Exception:
            type_name = "?"
        try:
            members = sorted(a for a in dir(obj) if not a.startswith("_"))
        except Exception as e:
            members = ["<dir failed: " + str(e) + ">"]
        out[n] = {"type": type_name, "members": members}
    return out


@_maybe_dev_handler("_eval")
def _eval_op(args):
    """Diagnostic: evaluate a Python expression OR exec a script inside the
    watcher's namespace (which has all CODESYS-injected globals available).

    Accepts either `{"expr": "..."}` or `{"script": "..."}`. For `expr`,
    returns the evaluated value (passed through coerce_for_json). For
    `script`, the script may set a `_result` variable that we return; also
    captures stdout-equivalent into a `_log` list available to the script.

    NOT exposed as an MCP tool — only callable via direct file IPC during
    development. Set MCPTOOLKIT_DEV=1 in Claude Desktop's `env` block
    to enable. Powerful enough to read/write arbitrary watcher state
    so default off.
    """
    # Build the eval namespace: all injected globals plus a few helpers.
    ns = {}
    for k, v in _codesys_helpers._INJECTED.items():
        ns[k] = v
    ns["__builtins__"] = __builtins__
    log_buf = []
    ns["_log"] = log_buf
    ns["_result"] = None
    ns["coerce_for_json"] = _codesys_helpers.coerce_for_json

    expr = args.get("expr")
    if expr is not None:
        try:
            value = eval(expr, ns)
            return {
                "ok": True,
                "value": _codesys_helpers.coerce_for_json(value),
                "log": list(log_buf),
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "kind": type(e).__name__,
                "traceback": traceback.format_exc(),
                "log": list(log_buf),
            }

    script = args.get("script")
    if script is not None:
        try:
            exec(script, ns)
            return {
                "ok": True,
                "result": _codesys_helpers.coerce_for_json(ns.get("_result")),
                "log": list(log_buf),
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "kind": type(e).__name__,
                "traceback": traceback.format_exc(),
                "log": list(log_buf),
            }
    return {"ok": False, "error": "expected 'expr' or 'script' in args"}


_SHUTDOWN_REQUESTED = False


@_registry.handler("shutdown")
def _shutdown(args):
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True
    return {"shutting_down": True}


def _install_prompt_suppression():
    """Stop modal dialogs from freezing the primary-thread loop.

    By default `system.prompt_handling` is `None`, meaning CODESYS shows simple
    yes/no/ok prompts as MODAL DIALOGS — which wedge our single-threaded loop
    (no thread is left to click them). Setting `ForwardSimplePrompts` makes the
    script engine auto-answer simple prompts with their default instead of
    blocking. The `Log*` flags record what was prompted so a surprising
    auto-answer is at least visible in the IDE Messages window + our log.

    Verified on SP22: `PromptHandling` is a global flags enum; assigning to
    `system.prompt_handling` takes effect immediately and persists. Best-effort
    — never fatal to startup.
    """
    try:
        system = _codesys_helpers._g("system")  # noqa: F841
        ph = _codesys_helpers._g("PromptHandling")
    except Exception:
        log_warn("prompt suppression unavailable (PromptHandling/system missing)")
        return
    try:
        system.prompt_handling = (
            ph.ForwardSimplePrompts | ph.LogSimplePrompts | ph.LogMessageKeys
        )
        log_info("prompt_handling set to " + str(system.prompt_handling))
    except Exception:
        log_error("failed to set prompt_handling:\n" + traceback.format_exc())


# ----------------------------------------------------------------------------
# Import topical handler modules (each registers via @_registry.handler).
# Wrapped in try/except so missing handlers don't take the whole watcher down.
# ----------------------------------------------------------------------------

def _load_handler_modules():
    modules = [
        "handlers.meta_h",
        "handlers.project_h",
        "handlers.build_h",
        "handlers.pou_h",
        "handlers.online_h",
        "handlers.library_h",
        "handlers.device_h",
        "handlers.symbol_h",
        "handlers.task_h",
    ]
    for m in modules:
        try:
            __import__(m)
            log_info("loaded " + m)
        except Exception:
            log_error("failed to load " + m + ":\n" + traceback.format_exc())


# ----------------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------------


def _dispatch(cmd):
    cmd_id = cmd.get("id", "?")
    op = cmd.get("op", "")
    args = cmd.get("args") or {}
    started = time.time()

    fn = _registry.find(op)
    if fn is None:
        return {
            "id": cmd_id,
            "status": "error",
            "data": {},
            "error": "Unknown op: " + repr(op),
            "error_kind": "UnknownOp",
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    try:
        data = fn(args)
        return {
            "id": cmd_id,
            "status": "ok",
            "data": data if isinstance(data, dict) else {"value": data},
            "error": None,
            "error_kind": None,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    except Exception:
        tb = traceback.format_exc()
        log_error("op " + op + " raised:\n" + tb)
        return {
            "id": cmd_id,
            "status": "error",
            "data": {},
            "error": tb,
            "error_kind": "HandlerException",
            "elapsed_ms": int((time.time() - started) * 1000),
        }


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------

_POLL_DELAY_MS = 50
_IDLE_DELAY_MS = 150
_SENTINEL_NAME = "STOP"


def _list_pending(commands_dir):
    try:
        names_ = os.listdir(commands_dir)
    except (IOError, OSError):
        return []
    out = [os.path.join(commands_dir, n) for n in names_ if n.endswith(".json")]
    out.sort()
    return out


# Command ids become result FILENAMES, so they must be filename-safe. Legit
# ids are uuid4 hex (optionally a short ASCII prefix + hyphen). Anything with a
# path separator, dot, drive letter, etc. is rejected so a crafted command
# can't make us write `results/<id>.json` OUTSIDE the results dir (path
# traversal -> arbitrary JSON file write as the IDE process).
_RESULT_ID_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _safe_result_filename(cmd_id):
    s = str(cmd_id)
    if not s or len(s) > 128:
        return None
    # No separators, dots, colons — only the conservative id charset.
    if os.path.basename(s) != s:
        return None
    for c in s:
        if c not in _RESULT_ID_ALLOWED:
            return None
    return s + ".json"


def _process_one(path, results_dir):
    cmd = _safe_read_json(path)
    if cmd is None:
        return
    try:
        os.remove(path)
    except (IOError, OSError):
        return
    op_name = cmd.get("op", "?")
    cmd_id = cmd.get("id", "?")
    # Validate the id BEFORE it's used as a result filename. A bad id can't be
    # answered (the caller used uuid4), so refusing to write is correct.
    result_name = _safe_result_filename(cmd_id)
    if result_name is None:
        log_error("refusing command with unsafe id: " + repr(cmd_id)[:80])
        return
    # Mark busy BEFORE dispatch so a handler that hangs (modal dialog, stuck
    # build) leaves a heartbeat the host can recognize: busy + op + deadline.
    _write_heartbeat(
        "busy",
        op=op_name,
        deadline_s=cmd.get("deadline_s"),
        op_started_ts=time.time(),
        force=True,
    )
    result = _dispatch(cmd)
    # Coerce IronPython `long` / CLR types before encoding — Python's json
    # can't serialize System.Int64 / IronPython long. Preempts the
    # phobicdotno compile_messages bug.
    safe_result = _codesys_helpers.coerce_for_json(result)
    result_path = os.path.join(results_dir, result_name)
    status = (result or {}).get("status", "unknown")
    elapsed = (result or {}).get("elapsed_ms", 0)
    extra = {"op": op_name, "id": cmd_id, "elapsed_ms": elapsed, "status": status}
    if status == "ok":
        # Don't write_message on every successful op — too chatty for the
        # IDE Messages window. Just record to structured log.
        _write_log_line("info", "op " + op_name + " ok in " + str(elapsed) + "ms", extra)
    else:
        log_warn("op " + op_name + " " + status + " in " + str(elapsed) + "ms", extra)
    try:
        _atomic_write(result_path, json.dumps(safe_result))
    except (IOError, OSError):
        log_error("failed to write result for id=" + cmd_id)
    except (TypeError, ValueError) as exc:
        log_error("json encode failed for id=" + cmd_id + ": " + str(exc))
        # Last-ditch: write a synthetic error envelope so the caller doesn't
        # hang waiting for a result file that never appears.
        fallback = {
            "id": cmd_id,
            "status": "error",
            "data": {},
            "error": "result was not JSON-serializable: " + str(exc),
            "error_kind": "JSONEncodeError",
            "elapsed_ms": 0,
        }
        try:
            _atomic_write(result_path, json.dumps(fallback))
        except Exception:
            pass


def main():
    workdir = _read_workdir_from_args()
    commands_dir = os.path.join(workdir, "commands")
    results_dir = os.path.join(workdir, "results")
    sentinel = os.path.join(workdir, _SENTINEL_NAME)

    for d in (commands_dir, results_dir):
        if not os.path.isdir(d):
            os.makedirs(d)

    _init_logging(workdir)
    _init_heartbeat(workdir)
    log_info("watcher starting; workdir=" + workdir)
    _install_prompt_suppression()
    _load_handler_modules()
    log_info("ops registered: " + ", ".join(_registry.names()))

    try:
        _atomic_write(
            os.path.join(workdir, "watcher.ready"),
            json.dumps({
                "pid": os.getpid() if hasattr(os, "getpid") else None,
                "ts": time.time(),
                "ops": _registry.names(),
            }),
        )
    except Exception:
        pass

    _write_heartbeat("idle", force=True)

    while not _SHUTDOWN_REQUESTED:
        try:
            if os.path.exists(sentinel):
                log_info("STOP sentinel observed; exiting loop.")
                try:
                    os.remove(sentinel)
                except (IOError, OSError):
                    pass
                break

            pending = _list_pending(commands_dir)
            if pending:
                for p in pending:
                    _process_one(p, results_dir)
                # Back to idle once the batch drains; force so the host sees
                # the busy->idle transition immediately after a long op.
                _write_heartbeat("idle", force=True)
                system.delay(_POLL_DELAY_MS)  # noqa: F821
            else:
                _write_heartbeat("idle")  # throttled
                system.delay(_IDLE_DELAY_MS)  # noqa: F821
        except KeyboardInterrupt:
            log_warn("KeyboardInterrupt — exiting watcher.")
            break
        except Exception:
            log_error("watcher loop exception:\n" + traceback.format_exc())
            system.delay(500)  # noqa: F821

    log_info("watcher exiting cleanly.")


main()
