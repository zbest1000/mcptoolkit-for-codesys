"""Pytest fixtures for LIVE CODESYS integration tests.

These tests drive a real CODESYS SP22 instance and are therefore OFF by
default — the normal `pytest` run (and CI) stays CODESYS-free. Opt in with:

    MCPTOOLKIT_LIVE=1 pytest tests/integration

The `watcher` fixture spawns CODESYS once per session (or adopts an already-
running watcher, which makes local re-runs fast). Individual tests get an
`ipc` client and an isolated scratch project path.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from mcptoolkit_for_codesys.ipc import IpcClient
from mcptoolkit_for_codesys.watcher_manager import (
    WatcherManager,
    find_watcher_script,
    pick_install,
)

LIVE = os.environ.get("MCPTOOLKIT_LIVE") == "1"

# Don't even COLLECT the live integration tests unless opted in. A module-level
# `pytestmark` in a conftest does NOT propagate to sibling test files, so we
# gate at collection time instead — keeping the default `pytest` run (and CI)
# fast and CODESYS-free with zero skip noise.
if not LIVE:
    collect_ignore_glob = ["test_*.py"]


def _default_workdir() -> Path:
    env = os.environ.get("MCPTOOLKIT_WORKDIR")
    if env:
        return Path(env)
    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or "."
    return Path(tmp) / "mcptoolkit-for-codesys"


@pytest.fixture(scope="session")
def workdir() -> Path:
    return _default_workdir()


@pytest.fixture(scope="session")
def watcher(workdir: Path):
    """Ensure a healthy watcher is running for the session.

    Spawns CODESYS (or adopts a running watcher). Does NOT tear it down — a
    left-running watcher is adopted on the next run, which keeps the
    spawn-cost (~60-90s) off most invocations. Use `codesys.shutdown` or kill
    the process manually to stop it.
    """
    sp = int(os.environ.get("MCPTOOLKIT_SP", "22"))
    install = pick_install(prefer_sp=sp)
    mgr = WatcherManager(
        workdir=workdir,
        watcher_script=find_watcher_script(),
        install=install,
        show_ide=os.environ.get("MCPTOOLKIT_HEADLESS") != "1",
        startup_timeout_s=float(os.environ.get("MCPTOOLKIT_STARTUP_TIMEOUT", "180")),
    )
    asyncio.run(mgr.ensure_started())
    yield mgr
    # The dialog guard (started per-test below) runs in each test's loop; the
    # session manager itself is left running for adoption on the next run.


@pytest.fixture
def ipc(workdir: Path, watcher) -> IpcClient:
    return IpcClient(workdir=workdir)


@pytest.fixture(autouse=True)
async def dialog_guard(watcher):
    """Auto-confirm the watcher's safe modal dialogs (storage-format upgrade,
    save prompts) for the duration of each test, in the test's event loop.
    Without this, ops like device.update on an old-format project wedge waiting
    for a Yes/No click that no human is there to make."""
    watcher.start_dialog_guard(interval_s=1.0)
    yield
    await watcher.stop_dialog_guard()


@pytest.fixture(scope="session")
def templates_dir() -> str:
    return os.environ.get(
        "MCPTOOLKIT_TEMPLATES",
        r"C:\Program Files\CODESYS 3.5.22.10\CODESYS\Templates",
    )


@pytest.fixture
def scratch_project(tmp_path_factory) -> str:
    """A unique .project path under the OS temp dir. Cleaned up by the OS;
    we also unlink before use so a stale file never blocks create_standard."""
    d = tmp_path_factory.mktemp("codesys_int")
    p = d / "integration.project"
    if p.exists():
        p.unlink()
    return str(p)
