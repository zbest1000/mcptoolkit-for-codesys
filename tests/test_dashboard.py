"""Tests for the read-only observability dashboard's status builder.

build_status() must produce a coherent snapshot from whatever's in the
workdir — including the degenerate cases (no watcher, dead pid, stale
heartbeat) — without ever raising. Pure host-side; no CODESYS, no HTTP.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from mcptoolkit_for_codesys import watcher_manager as wm
from mcptoolkit_for_codesys.dashboard import _classify_liveness, build_status


class TestClassifyLiveness:
    def test_dead_when_no_pid(self):
        assert _classify_liveness(None, None) == "dead"

    def test_dead_when_pid_gone(self):
        assert _classify_liveness(2_000_000_000, {"ts": time.time(), "state": "idle"}) == "dead"

    def test_healthy_when_alive_no_heartbeat(self):
        assert _classify_liveness(os.getpid(), None) == "healthy"

    def test_hung_when_idle_stale(self):
        old = time.time() - (wm.HEARTBEAT_IDLE_STALE_S + 5)
        assert _classify_liveness(os.getpid(), {"ts": old, "state": "idle"}) == "hung"

    def test_healthy_when_busy_within_deadline(self):
        now = time.time()
        hb = {"ts": now, "state": "busy", "op_started_ts": now - 5, "deadline_s": 120}
        assert _classify_liveness(os.getpid(), hb) == "healthy"


class TestBuildStatus:
    def test_empty_workdir_is_safe(self, tmp_path: Path):
        s = build_status(tmp_path)
        assert s["liveness"] == "dead"
        assert s["pid"] is None
        assert s["command_queue_depth"] == 0
        assert s["log_tail"] == []

    def test_reports_queue_and_heartbeat(self, tmp_path: Path):
        (tmp_path / "commands").mkdir()
        (tmp_path / "results").mkdir()
        (tmp_path / "commands" / "a.json").write_text("{}")
        (tmp_path / "watcher.ready").write_text(
            json.dumps({"pid": os.getpid(), "ts": time.time(), "ops": ["ping", "health"]})
        )
        (tmp_path / "watcher.heartbeat").write_text(
            json.dumps({"pid": os.getpid(), "ts": time.time(), "state": "idle"})
        )
        s = build_status(tmp_path)
        assert s["pid"] == os.getpid()
        assert s["ops_count"] == 2
        assert s["command_queue_depth"] == 1
        assert s["liveness"] == "healthy"

    def test_parses_log_tail(self, tmp_path: Path):
        logdir = tmp_path / "log"
        logdir.mkdir()
        (logdir / "watcher.log").write_text(
            json.dumps({"ts": time.time(), "level": "info", "msg": "hello"}) + "\n"
            + "not-json-line\n"
        )
        s = build_status(tmp_path)
        assert any(r.get("msg") == "hello" for r in s["log_tail"])
        # non-JSON line is preserved as raw
        assert any(r.get("level") == "raw" for r in s["log_tail"])
