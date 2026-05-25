"""End-to-end IPC test against a Python "fake watcher" running in a thread.

The real watcher is IronPython 2.7 inside CODESYS — too heavyweight to test
in CI. This file simulates the watcher side just enough to round-trip
commands through `IpcClient`. It catches regressions in:
  - the `commands/<id>.json` + `*.tmp` + rename atomicity contract
  - the Result Pydantic parse path
  - the timeout-on-no-result path
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from threading import Event, Thread

import pytest

from mcptoolkit_for_codesys.ipc import IpcClient, IpcTimeout


def _fake_watcher_loop(workdir: Path, stop: Event, handler):
    """Mini watcher: poll commands/, call handler(cmd_dict), write results/."""
    commands_dir = workdir / "commands"
    results_dir = workdir / "results"
    commands_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    while not stop.is_set():
        try:
            names = sorted(p for p in os.listdir(str(commands_dir))
                           if p.endswith(".json"))
        except OSError:
            names = []
        for n in names:
            path = commands_dir / n
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                continue
            try:
                os.remove(str(path))
            except OSError:
                continue
            try:
                cmd = json.loads(raw)
            except ValueError:
                continue
            result = handler(cmd)
            rpath = results_dir / "{}.json".format(cmd.get("id", "unknown"))
            tmp = rpath.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(result), encoding="utf-8")
            os.replace(str(tmp), str(rpath))
        time.sleep(0.01)


class FakeWatcher:
    """Thread-managed fake watcher with pluggable per-call response."""

    def __init__(self, workdir: Path, handler):
        self.workdir = workdir
        self.handler = handler
        self.stop_event = Event()
        self.thread = Thread(
            target=_fake_watcher_loop,
            args=(workdir, self.stop_event, handler),
            daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_event.set()
        self.thread.join(timeout=2)


def _ok_echo(cmd):
    return {
        "id": cmd["id"],
        "status": "ok",
        "data": {"echo": cmd.get("args", {}).get("echo", "")},
        "error": None,
        "error_kind": None,
        "elapsed_ms": 1,
    }


@pytest.mark.asyncio
async def test_basic_roundtrip(tmp_path: Path):
    with FakeWatcher(tmp_path, _ok_echo):
        client = IpcClient(workdir=tmp_path)
        result = await client.call("ping", {"echo": "hello"}, timeout_s=5.0)
        assert result.status == "ok"
        assert result.data["echo"] == "hello"


@pytest.mark.asyncio
async def test_ids_are_unique_across_calls(tmp_path: Path):
    seen = []

    def handler(cmd):
        seen.append(cmd["id"])
        return _ok_echo(cmd)

    with FakeWatcher(tmp_path, handler):
        client = IpcClient(workdir=tmp_path)
        await client.call("a", {}, timeout_s=5.0)
        await client.call("b", {}, timeout_s=5.0)
        await client.call("c", {}, timeout_s=5.0)
        assert len(set(seen)) == 3, "expected 3 distinct correlation IDs"


@pytest.mark.asyncio
async def test_timeout_when_no_response(tmp_path: Path):
    # No fake watcher running — command will sit in commands/ forever.
    (tmp_path / "commands").mkdir()
    (tmp_path / "results").mkdir()
    client = IpcClient(workdir=tmp_path, poll_interval_s=0.01)
    with pytest.raises(IpcTimeout):
        await client.call("ping", {}, timeout_s=0.2)


@pytest.mark.asyncio
async def test_result_consumed_after_read(tmp_path: Path):
    """The host deletes the result file once read so a re-issued cmd id
    wouldn't get a stale response. Verify the cleanup."""
    with FakeWatcher(tmp_path, _ok_echo):
        client = IpcClient(workdir=tmp_path)
        result = await client.call("ping", {}, timeout_s=5.0)
        # After call returns, the result file should be gone.
        results_dir = tmp_path / "results"
        rfile = results_dir / "{}.json".format(result.id)
        assert not rfile.exists()


@pytest.mark.asyncio
async def test_error_response_parses(tmp_path: Path):
    def err_handler(cmd):
        return {
            "id": cmd["id"],
            "status": "error",
            "data": {},
            "error": "Traceback ...\nKeyError: 'x'\n",
            "error_kind": "HandlerException",
            "elapsed_ms": 0,
        }
    with FakeWatcher(tmp_path, err_handler):
        client = IpcClient(workdir=tmp_path)
        result = await client.call("ping", {}, timeout_s=5.0)
        assert result.status == "error"
        assert result.error_kind == "HandlerException"
        assert "KeyError" in result.error


@pytest.mark.asyncio
async def test_concurrent_calls_dont_cross_responses(tmp_path: Path):
    """Two awaitables racing through the same IpcClient must each get their
    own response — not the other's."""
    def handler(cmd):
        # Echo the args so we can assert which call got what.
        return {
            "id": cmd["id"],
            "status": "ok",
            "data": {"args": cmd.get("args", {})},
            "error": None,
            "error_kind": None,
            "elapsed_ms": 1,
        }
    with FakeWatcher(tmp_path, handler):
        client = IpcClient(workdir=tmp_path)
        r_a, r_b = await asyncio.gather(
            client.call("a", {"tag": "A"}, timeout_s=5.0),
            client.call("b", {"tag": "B"}, timeout_s=5.0),
        )
        assert r_a.data["args"]["tag"] == "A"
        assert r_b.data["args"]["tag"] == "B"
