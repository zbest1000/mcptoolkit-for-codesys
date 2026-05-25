# -*- coding: utf-8 -*-
"""
Task configuration handlers.

Discovered SP22 surface (locale-safe via type GUIDs — this install is German,
"Taskkonfiguration"):
  - Task config object (ae1de277-...) has `create_task(name)`.
  - Task object (98a2708a-...) exposes `interval` (IEC TIME string, settable),
    `priority` (string, settable), and `watchdog` (enabled/time/sensitivity).
  - A task's POU calls are CHILD task-reference objects (413e2a7d-...).

Not exposed by the script API: adding a POU call to a task, and the task
execution type (cyclic/freewheeling/event). Assign POUs to tasks in the IDE or
via PLCopenXML import. So we cover list / set / create.

Ops:
  task.list     list tasks with interval/priority/watchdog + POU calls
  task.set      set a task's interval / priority / watchdog
  task.create   create a new task under the task configuration
"""
import _registry
import _codesys_helpers as h

_TASK_GUID = "98a2708a-9b18-4f31-82ed-a1465b24fa2d"
_TASK_CONFIG_GUID = "ae1de277-a207-4a28-9efb-456c06bd52f3"
_TASK_REF_GUID = "413e2a7d-adb1-4d2c-be29-6ae6e4fab820"


def _find_by_type(proj, type_guid):
    out = []

    def visit(o):
        try:
            children = list(o.get_children(False))
        except Exception:
            children = []
        for ch in children:
            if str(getattr(ch, "type", "")).lower() == type_guid:
                out.append(ch)
            visit(ch)

    visit(proj)
    return out


def _watchdog_summary(task):
    try:
        wd = task.watchdog
    except Exception:
        return None
    out = {}
    for a in ("enabled", "time", "time_unit", "sensitivity"):
        try:
            out[a] = str(getattr(wd, a))
        except Exception:
            pass
    return out


def _task_summary(task):
    calls = []
    try:
        for ch in task.get_children(False):
            if str(getattr(ch, "type", "")).lower() == _TASK_REF_GUID:
                calls.append(h._safe_name(ch))
    except Exception:
        pass
    out = {"name": h._safe_name(task), "pou_calls": calls}
    for a in ("interval", "priority"):
        try:
            out[a] = str(getattr(task, a))
        except Exception:
            out[a] = None
    wd = _watchdog_summary(task)
    if wd is not None:
        out["watchdog"] = wd
    return out


def _resolve_task(proj, args):
    tasks = _find_by_type(proj, _TASK_GUID)
    name = args.get("name") or args.get("task")
    if name:
        for t in tasks:
            if h._safe_name(t) == name:
                return t
        raise RuntimeError("Task not found: " + name)
    if not tasks:
        raise RuntimeError("No task found in project.")
    if len(tasks) > 1:
        names = [h._safe_name(t) for t in tasks]
        raise RuntimeError(
            "Multiple tasks (" + ", ".join(names) + "); pass 'name'."
        )
    return tasks[0]


@_registry.handler("task.list")
def op_list(args):
    proj = h.resolve_project(args)
    tasks = _find_by_type(proj, _TASK_GUID)
    return {"count": len(tasks), "tasks": [_task_summary(t) for t in tasks]}


@_registry.handler("task.set")
def op_set(args):
    proj = h.resolve_project(args)
    task = _resolve_task(proj, args)
    changed = {}
    if "interval" in args and args.get("interval") is not None:
        before = str(getattr(task, "interval", ""))
        task.interval = str(args["interval"])
        changed["interval"] = {"before": before, "after": str(task.interval)}
    if "priority" in args and args.get("priority") is not None:
        before = str(getattr(task, "priority", ""))
        task.priority = str(args["priority"])  # priority is a string property
        changed["priority"] = {"before": before, "after": str(task.priority)}
    if "watchdog_enabled" in args or "watchdog_time" in args:
        try:
            wd = task.watchdog
            if "watchdog_enabled" in args:
                wd.enabled = bool(args["watchdog_enabled"])
            if args.get("watchdog_time") is not None:
                wd.time = str(args["watchdog_time"])
            changed["watchdog"] = _watchdog_summary(task)
        except Exception as exc:  # noqa: BLE001
            changed["watchdog_error"] = str(exc)
    if not changed:
        raise RuntimeError(
            "task.set: provide at least one of interval / priority / "
            "watchdog_enabled / watchdog_time."
        )
    return {"task": h._safe_name(task), "changed": changed}


@_registry.handler("task.create")
def op_create(args):
    proj = h.resolve_project(args)
    name = args.get("name")
    if not name:
        raise RuntimeError("task.create: 'name' is required.")
    cfgs = _find_by_type(proj, _TASK_CONFIG_GUID)
    if not cfgs:
        raise RuntimeError("No task configuration found in project.")
    cfg = cfgs[0]
    task = cfg.create_task(name)
    out = {"created": True, "name": h._safe_name(task)}
    # Optionally set interval/priority on the new task.
    if args.get("interval") is not None:
        try:
            task.interval = str(args["interval"])
            out["interval"] = str(task.interval)
        except Exception as exc:  # noqa: BLE001
            out["interval_error"] = str(exc)
    if args.get("priority") is not None:
        try:
            task.priority = str(args["priority"])
            out["priority"] = str(task.priority)
        except Exception as exc:  # noqa: BLE001
            out["priority_error"] = str(exc)
    return out
