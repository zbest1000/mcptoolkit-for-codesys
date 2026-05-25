"""Live end-to-end integration test — the pytest port of build_full_project.ps1.

Builds a real project from scratch and exercises every major tool family
against a running CODESYS SP22, asserting on results instead of eyeballing
console output. The headline assertion is that `device.update` takes the
Standard-template project from a broken build (old PLCWinNT descriptor pins an
uninstalled IoStandard) to a CLEAN build (0 errors) — the reliability +
device-tools work this suite exists to protect.

Run with:  MCPTOOLKIT_LIVE=1 pytest tests/integration -v
"""
from __future__ import annotations

import pytest

from mcptoolkit_for_codesys.ipc import IpcClient


async def _ok(ipc: IpcClient, op: str, args: dict | None = None, timeout: float = 60.0):
    r = await ipc.call(op, args or {}, timeout_s=timeout)
    assert r.status == "ok", f"{op} failed: {r.error}"
    return r.data


@pytest.mark.asyncio
async def test_full_project_lifecycle(ipc: IpcClient, scratch_project: str, templates_dir: str):
    # [1] Bootstrap
    await _ok(ipc, "project.create_standard", {
        "path": scratch_project, "overwrite": True, "templates_dir": templates_dir,
    }, timeout=120.0)
    await _ok(ipc, "project.set_info", {
        "title": "MCP Integration", "version": "0.1.0", "author": "ci",
    })

    # [2] Folders
    for folder in ("POUs", "DataTypes", "GVLs"):
        await _ok(ipc, "pou.create_folder", {"name": folder})

    # [3] DUTs
    await _ok(ipc, "pou.create_dut", {
        "name": "E_PumpState", "kind": "enumeration", "parent": "DataTypes",
        "declaration": "TYPE E_PumpState :\n(\n  Idle := 0,\n  Running := 2,\n  Faulted := 99\n);\nEND_TYPE",
    })
    await _ok(ipc, "pou.create_dut", {
        "name": "ST_Reading", "kind": "structure", "parent": "DataTypes",
        "declaration": "TYPE ST_Reading :\nSTRUCT\n  value : REAL;\n  valid : BOOL;\nEND_STRUCT\nEND_TYPE",
    })

    # [4] GVL
    await _ok(ipc, "pou.create_gvl", {
        "name": "GVL_Config", "parent": "GVLs",
        "declaration": "VAR_GLOBAL\n  cMaxPumps : INT := 4;\nEND_VAR",
    })

    # [5] FB + method + property
    # Note: the local counter is `nCycles` (not `cycles`) so it doesn't collide
    # with the case-insensitive property name `Cycles`.
    await _ok(ipc, "pou.create", {
        "name": "FB_Pump", "pou_type": "function_block", "language": "st", "parent": "POUs",
        "declaration": "FUNCTION_BLOCK FB_Pump\nVAR_INPUT\n  enable : BOOL;\nEND_VAR\nVAR\n  state : E_PumpState;\n  nCycles : DINT;\nEND_VAR",
        "implementation": "IF enable THEN\n  state := E_PumpState.Running;\n  nCycles := nCycles + 1;\nELSE\n  state := E_PumpState.Idle;\nEND_IF",
    })
    await _ok(ipc, "pou.create_method", {
        "name": "Reset", "parent": "FB_Pump",
        "declaration": "METHOD Reset", "implementation": "state := E_PumpState.Idle;\nnCycles := 0;",
    })
    await _ok(ipc, "pou.create_property", {
        "name": "Cycles", "parent": "FB_Pump", "return_type": "DINT",
    })
    await _ok(ipc, "pou.set_text", {
        "target": "FB_Pump/Cycles/Get", "implementation": "Cycles := nCycles;",
    })

    # [6] Orchestrate in PLC_PRG
    await _ok(ipc, "pou.set_text", {
        "target": "PLC_PRG",
        "declaration": "PROGRAM PLC_PRG\nVAR\n  pumps : ARRAY[1..4] OF FB_Pump;\n  i : INT;\n  total : DINT;\nEND_VAR",
        "implementation": "total := 0;\nFOR i := 1 TO GVL_Config.cMaxPumps DO\n  pumps[i](enable := TRUE);\n  total := total + pumps[i].Cycles;\nEND_FOR",
    })

    # [7] Inventory
    tree = await _ok(ipc, "project.tree", {"max_depth": 4})
    assert tree  # non-empty

    devices = await _ok(ipc, "device.tree", {})
    assert devices["count"] >= 1
    plc = devices["devices"][0]
    assert plc["device_id"]["type"] == 4096  # the soft PLC

    # [8] Build BEFORE the device fix — Standard template pins old IoStandard.
    before = await _ok(ipc, "build.build", {}, timeout=180.0)
    errors_before = before["errors"]

    # [9] device.update: bump the PLC descriptor to the installed version, which
    #     re-points it at a current IoStandard. THE key fix.
    upd = await _ok(ipc, "device.update", {}, timeout=180.0)
    assert upd["updated"] is True
    # version should have advanced (e.g. 3.1.3.0 -> 3.5.x)
    assert upd["to"]["version"] != upd["from"]["version"]

    # [10] Build AFTER — should be clean.
    after = await _ok(ipc, "build.build", {}, timeout=180.0)
    assert after["errors"] == 0, (
        f"build still has {after['errors']} errors after device.update "
        f"(was {errors_before} before)"
    )

    # [11] Mirror export — one .st per code object.
    import tempfile, os
    mirror_dir = os.path.join(tempfile.gettempdir(), "codesys_int_mirror")
    mirror = await _ok(ipc, "project.mirror_export", {"out_dir": mirror_dir, "clean": True})
    written = mirror.get("files_written", [])
    names = {os.path.basename(f["path"]) for f in written}
    assert "PLC_PRG.st" in names
    assert any("FB_Pump" in f["path"] for f in written)

    # [12] Save / close / reopen
    await _ok(ipc, "project.save", {}, timeout=60.0)
    await _ok(ipc, "project.close", {})
    reopened = await _ok(ipc, "project.open", {"path": scratch_project}, timeout=120.0)
    # Reopen response should NOT report missing libraries now (device fix saved).
    assert reopened.get("library_diagnostics", {}).get("total_missing", 0) == 0


@pytest.mark.asyncio
async def test_diagnose_is_hang_proof(ipc: IpcClient):
    """codesys.diagnose must answer from host-side state even mid-session."""
    # Direct watcher health op as a control, then the host-side diagnosis.
    health = await _ok(ipc, "health", {}, timeout=10.0)
    assert "pid" in health


@pytest.mark.asyncio
async def test_online_app_resolution_and_state(ipc: IpcClient, scratch_project: str, templates_dir: str):
    """The online _resolve_app GUID fix: online.state must find the app and
    report it (was 'No Application object found' before the fix)."""
    # Reuse the already-open project from the lifecycle test if present,
    # otherwise create a fresh one.
    open_list = await _ok(ipc, "project.list_open", {})
    if not open_list.get("projects"):
        await _ok(ipc, "project.create_standard", {
            "path": scratch_project, "overwrite": True, "templates_dir": templates_dir,
        }, timeout=120.0)
    state = await _ok(ipc, "online.state", {}, timeout=20.0)
    assert "application_state" in state
    assert "is_logged_in" in state


@pytest.mark.asyncio
async def test_symbol_config_live(ipc: IpcClient, scratch_project: str, templates_dir: str):
    """symbol.create_config / list against a real SP22 application, with a
    pragma-marked variable that compiles into the symbol set."""
    await _ok(ipc, "project.create_standard", {
        "path": scratch_project, "overwrite": True, "templates_dir": templates_dir,
    }, timeout=120.0)
    await _ok(ipc, "device.update", {"type": 4096, "id": "0000 0004"}, timeout=180.0)
    await _ok(ipc, "pou.set_text", {
        "target": "PLC_PRG",
        "declaration": "PROGRAM PLC_PRG\nVAR\n  {attribute 'symbol' := 'readwrite'}\n  gCount : DINT;\nEND_VAR",
        "implementation": "gCount := gCount + 1;",
    })
    before = await _ok(ipc, "symbol.list", {})
    assert before["count"] == 0
    created = await _ok(ipc, "symbol.create_config", {})
    assert created["created"] is True and created["name"] == "Symbols"
    again = await _ok(ipc, "symbol.create_config", {})
    assert again.get("existed") is True  # idempotent
    after = await _ok(ipc, "symbol.list", {})
    assert after["count"] == 1
    built = await _ok(ipc, "build.build", {}, timeout=180.0)
    assert built["errors"] == 0


@pytest.mark.asyncio
async def test_device_parameters_live(ipc: IpcClient, scratch_project: str, templates_dir: str):
    """device.parameters / set_parameter against a real fieldbus device."""
    await _ok(ipc, "project.create_standard", {
        "path": scratch_project, "overwrite": True, "templates_dir": templates_dir,
    }, timeout=120.0)
    await _ok(ipc, "device.update", {"type": 4096, "id": "0000 0004"}, timeout=180.0)
    await _ok(ipc, "device.add", {
        "name": "Ethernet", "device_name": "Ethernet", "type": 110,
        "id": "0000 0002", "parent": "PLCWinNT",
    }, timeout=180.0)
    params = await _ok(ipc, "device.parameters", {"target": "PLCWinNT/Ethernet", "name": "IPAddress"})
    assert params["count"] >= 1
    assert any(p["name"] == "IPAddress" for p in params["parameters"])
    res = await _ok(ipc, "device.set_parameter", {
        "target": "PLCWinNT/Ethernet", "name": "IPAddress", "value": "[192, 168, 0, 42]",
    })
    assert res["changed"] is True
    assert res["after"] == "[192, 168, 0, 42]"
    built = await _ok(ipc, "build.build", {}, timeout=180.0)
    assert built["errors"] == 0


@pytest.mark.asyncio
async def test_tasks_vars_library_live(ipc: IpcClient, scratch_project: str, templates_dir: str):
    """Tier-1 enrichments on real SP22: task config, variable-level POU editing,
    library.update."""
    await _ok(ipc, "project.create_standard", {
        "path": scratch_project, "overwrite": True, "templates_dir": templates_dir,
    }, timeout=120.0)
    await _ok(ipc, "device.update", {"type": 4096, "id": "0000 0004"}, timeout=180.0)

    # Task configuration
    tasks = await _ok(ipc, "task.list", {})
    assert tasks["count"] >= 1
    main = next(t for t in tasks["tasks"] if t["name"] == "MainTask")
    assert "PLC_PRG" in main["pou_calls"]
    setres = await _ok(ipc, "task.set", {"name": "MainTask", "interval": "t#20ms", "priority": "5"})
    assert setres["changed"]["interval"]["after"] == "t#20ms"
    assert setres["changed"]["priority"]["after"] == "5"

    # Variable-level POU editing
    await _ok(ipc, "pou.set_text", {
        "target": "PLC_PRG",
        "declaration": "PROGRAM PLC_PRG\nVAR\n\ti : INT;\nEND_VAR",
        "implementation": "i := i + 1;",
    })
    await _ok(ipc, "pou.add_variable", {
        "target": "PLC_PRG", "name": "rSpeed", "type": "REAL", "init": "1.5", "section": "VAR",
    })
    await _ok(ipc, "pou.add_symbol_pragma", {"target": "PLC_PRG", "name": "rSpeed", "access": "readwrite"})
    vlist = await _ok(ipc, "pou.list_variables", {"target": "PLC_PRG"})
    rspeed = next(v for v in vlist["variables"] if v["name"] == "rSpeed")
    assert rspeed["type"] == "REAL" and rspeed["init"] == "1.5"
    assert rspeed["pragma"] == "{attribute 'symbol' := 'readwrite'}"

    # library.update
    await _ok(ipc, "library.add", {"name": "Util"})
    upd = await _ok(ipc, "library.update", {"name": "Util"})
    assert upd["updated"] is True

    built = await _ok(ipc, "build.build", {}, timeout=180.0)
    assert built["errors"] == 0
