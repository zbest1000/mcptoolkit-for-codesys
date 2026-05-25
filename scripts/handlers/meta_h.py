# -*- coding: utf-8 -*-
"""
Meta / health handlers.

Ops:
  health    a single-call snapshot of the watcher's own state — useful as
            a liveness probe and as a starting point when diagnosing why a
            tool call timed out or returned stale data.
"""
import os
import sys
import time

import _registry
import _codesys_helpers as h


# Track watcher process start time so we can report uptime.
_WATCHER_STARTED_AT = time.time()


@_registry.handler("health")
def op_health(args):
    """Return a watcher-side health snapshot.

    Does NOT make any heavy calls (no build, no project enumeration of
    children). Intended to be cheap (<100ms) and safe to call when the
    rest of the watcher state is unknown.

    Returns:
      uptime_s             seconds since watcher.py started
      ironpython_version   IronPython banner string
      codesys_version      best-effort version probe (often returns "0.0")
      primary_project      path of the currently-primary project, or None
      open_projects_count  how many projects are open
      injected_globals_count  number of CODESYS-injected names visible to
                              helpers (sanity for the register_injected bridge)
      build_message_count  current count of messages in the Build category
                           (lets the caller decide whether to drain them
                           via build.messages without actually calling it)
      pid                  the watcher's OS process id
    """
    pid = os.getpid() if hasattr(os, "getpid") else None
    uptime = round(time.time() - _WATCHER_STARTED_AT, 2)

    # Project state
    primary_path = None
    open_count = 0
    try:
        projs = list(h.projects_obj().all)
        open_count = len(projs)
        try:
            p = h.projects_obj().primary
            if p is not None:
                primary_path = str(getattr(p, "path", "") or "")
        except Exception:
            pass
    except Exception:
        pass

    # Injected globals
    try:
        injected_count = len(h.list_injected())
    except Exception:
        injected_count = 0

    # Build message count (cheap; doesn't run a build)
    build_msg_count = 0
    try:
        import System
        build_guid = System.Guid("97f48d64-a2a3-4856-b640-75c046e37ea9")
        build_msg_count = len(list(h.system_obj().get_message_objects(build_guid)))
    except Exception:
        pass

    # CODESYS version (best-effort; same logic as the ping handler uses)
    cs_version = None
    try:
        cs_version = str(h.system_obj().version)
    except Exception:
        pass

    return {
        "uptime_s": uptime,
        "ironpython_version": sys.version,
        "codesys_version": cs_version,
        "primary_project": primary_path,
        "open_projects_count": open_count,
        "injected_globals_count": injected_count,
        "build_message_count": build_msg_count,
        "pid": pid,
    }
