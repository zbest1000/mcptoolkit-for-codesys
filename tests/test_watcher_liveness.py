"""Tests for watcher liveness detection, PID adoption, and hung recovery.

These are the reliability primitives that let the host tell a healthy watcher
from a dead or wedged one, and adopt a watcher left running by a previous host
process instead of spawning a duplicate CODESYS. Pure host-side — no CODESYS.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from mcptoolkit_for_codesys import watcher_manager as wm
from mcptoolkit_for_codesys.watcher_manager import (
    CodesysInstall,
    WatcherProcess,
    _pid_alive,
)


def _install() -> CodesysInstall:
    return CodesysInstall(
        key="test",
        version="Patch 1",
        install_dir=Path(r"C:\CODESYS"),
        exe=Path(r"C:\CODESYS\Common\CODESYS.exe"),
        profile=Path(r"C:\CODESYS\Profiles\x.profile.xml"),
        sp=22,
        patch=1,
    )


class TestPidAlive:
    def test_current_process_is_alive(self):
        assert _pid_alive(os.getpid()) is True

    def test_zero_and_negative_are_dead(self):
        assert _pid_alive(0) is False
        assert _pid_alive(-1) is False

    def test_almost_certainly_unused_pid_is_dead(self):
        # Very high PID that's overwhelmingly unlikely to be assigned.
        assert _pid_alive(2_000_000_000) is False


class TestLiveness:
    def _wp(self, workdir: Path, pid: int) -> WatcherProcess:
        return WatcherProcess(install=_install(), workdir=workdir, pid=pid, adopted=True)

    def test_dead_when_pid_gone(self, tmp_path: Path):
        wp = self._wp(tmp_path, 2_000_000_000)
        assert wp.liveness() == "dead"

    def test_healthy_when_alive_and_no_heartbeat(self, tmp_path: Path):
        # Startup window: process alive, heartbeat not written yet.
        wp = self._wp(tmp_path, os.getpid())
        assert wp.liveness() == "healthy"

    def test_healthy_when_idle_heartbeat_fresh(self, tmp_path: Path):
        wp = self._wp(tmp_path, os.getpid())
        wp.heartbeat_file.write_text(
            json.dumps({"pid": os.getpid(), "ts": time.time(), "state": "idle"})
        )
        assert wp.liveness() == "healthy"

    def test_hung_when_idle_heartbeat_stale(self, tmp_path: Path):
        wp = self._wp(tmp_path, os.getpid())
        old = time.time() - (wm.HEARTBEAT_IDLE_STALE_S + 10)
        wp.heartbeat_file.write_text(
            json.dumps({"pid": os.getpid(), "ts": old, "state": "idle"})
        )
        assert wp.liveness() == "hung"

    def test_healthy_when_busy_within_deadline(self, tmp_path: Path):
        wp = self._wp(tmp_path, os.getpid())
        # Busy on a 120s op that started 5s ago — well within deadline+grace.
        wp.heartbeat_file.write_text(json.dumps({
            "pid": os.getpid(),
            "ts": time.time(),
            "state": "busy",
            "op": "build.build",
            "deadline_s": 120.0,
            "op_started_ts": time.time() - 5,
        }))
        assert wp.liveness() == "healthy"

    def test_hung_when_busy_past_deadline_plus_grace(self, tmp_path: Path):
        wp = self._wp(tmp_path, os.getpid())
        started = time.time() - (10 + wm.HEARTBEAT_BUSY_GRACE_S + 5)
        wp.heartbeat_file.write_text(json.dumps({
            "pid": os.getpid(),
            "ts": started,
            "state": "busy",
            "op": "online.login",
            "deadline_s": 10.0,
            "op_started_ts": started,
        }))
        assert wp.liveness() == "hung"


class TestAdoption:
    def _manager(self, workdir: Path) -> wm.WatcherManager:
        # watcher_script path doesn't need to exist for _try_adopt.
        return wm.WatcherManager(
            workdir=workdir,
            watcher_script=workdir / "watcher.py",
            install=_install(),
        )

    def test_none_without_ready_marker(self, tmp_path: Path):
        mgr = self._manager(tmp_path)
        assert mgr._find_existing_watcher() is None

    def test_none_when_pid_dead(self, tmp_path: Path):
        (tmp_path / "watcher.ready").write_text(
            json.dumps({"pid": 2_000_000_000, "ts": time.time(), "ops": ["ping"]})
        )
        mgr = self._manager(tmp_path)
        assert mgr._find_existing_watcher() is None

    def test_finds_live_healthy_watcher(self, tmp_path: Path):
        pid = os.getpid()
        (tmp_path / "watcher.ready").write_text(
            json.dumps({"pid": pid, "ts": time.time(), "ops": ["ping"]})
        )
        (tmp_path / "watcher.heartbeat").write_text(
            json.dumps({"pid": pid, "ts": time.time(), "state": "idle"})
        )
        mgr = self._manager(tmp_path)
        wp = mgr._find_existing_watcher()
        assert wp is not None
        assert wp.adopted is True
        assert wp.pid == pid
        assert wp.liveness() == "healthy"

    def test_finds_hung_watcher_so_start_can_kill_it(self, tmp_path: Path):
        # New semantics: _find_existing_watcher returns the live watcher even
        # when hung, so start() can KILL it rather than spawn a duplicate
        # alongside it (the two-watchers-on-one-workdir race).
        pid = os.getpid()
        old = time.time() - (wm.HEARTBEAT_IDLE_STALE_S + 10)
        (tmp_path / "watcher.ready").write_text(
            json.dumps({"pid": pid, "ts": old, "ops": ["ping"]})
        )
        (tmp_path / "watcher.heartbeat").write_text(
            json.dumps({"pid": pid, "ts": old, "state": "idle"})
        )
        mgr = self._manager(tmp_path)
        wp = mgr._find_existing_watcher()
        assert wp is not None
        assert wp.liveness() == "hung"
