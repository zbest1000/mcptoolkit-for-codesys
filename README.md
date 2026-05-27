<div align="center">

# mcptoolkit-for-codesys

**Drive the CODESYS V3.5 SP22 PLC IDE from Claude — a Model Context Protocol server.**

[![tests](https://github.com/zbest1000/mcptoolkit-for-codesys/actions/workflows/test.yml/badge.svg)](https://github.com/zbest1000/mcptoolkit-for-codesys/actions/workflows/test.yml)
[![release](https://img.shields.io/github/v/release/zbest1000/mcptoolkit-for-codesys)](https://github.com/zbest1000/mcptoolkit-for-codesys/releases)
[![license](https://img.shields.io/github/license/zbest1000/mcptoolkit-for-codesys)](LICENSE)
![python](https://img.shields.io/badge/python-3.11%2B-blue)

</div>

A Model Context Protocol server that drives the **CODESYS V3.5 SP22** IDE through
its Python ScriptEngine. It lets Claude (or any MCP client) open projects, create
POUs/DUTs/GVLs with methods and properties, build, add and version devices, manage
libraries, and log into a live PLC to read, write, and force variables.

> **New to this?** Start with the **[Setup Guide](SETUP.md)** — a plain-English,
> step-by-step install that assumes no programming experience.
>
> **Want to understand how it works?** The **[`docs/`](docs/)** folder explains
> each part in plain language: [concepts](docs/concepts.md), the
> [watch page](docs/dashboard.md), [reliability](docs/reliability.md),
> [configuration](docs/configuration.md), and [security](docs/security.md).

**Status — v0.2.2, verified end-to-end on a real CODESYS V3.5 SP22 Patch 1
instance.** Everything from creating a project through the full online cycle
(login → run → read live values → write → stop → logout) works against a running
soft PLC, driven through the server's **82 tools**. It is covered by **199 host
unit tests plus 6 live integration tests** — you can run the live ones yourself
with `MCPTOOLKIT_LIVE=1 pytest tests/integration`. It should also work on SP19 and
up wherever the scripting API matches, though only SP22 has been exercised. The
full history is in [`CHANGES.md`](CHANGES.md).

## Contents

- [Feature highlights](#feature-highlights)
- [Why another CODESYS MCP](#why-another-codesys-mcp)
- [How it works](#how-it-works)
- [Install](#install)
- [Configure Claude Desktop](#configure-claude-desktop)
- [Example: a first session](#example-a-first-session)
- [Remote access over SSH](#remote-access-over-ssh)
- [Environment variables](#environment-variables)
- [Reliability & modal-dialog defense](#reliability--modal-dialog-defense)
- [Observability](#observability)
- [Tools](#tools)
- [Version control](#version-control)
- [What's verified](#whats-verified)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Security model](#security-model)
- [Architecture notes & gotchas](#architecture-notes--gotchas)
- [Acknowledgements](#acknowledgements)
- [License](#license)

## Feature highlights

- **Full IEC authoring** — projects, folders, POUs (Program/FB/Function in any
  IEC language), DUTs (struct/enum/union/alias), GVLs, FB methods + properties,
  declaration/implementation round-trip, variable-level editing, tree
  introspection, PLCopenXML import/export, and a git-friendly mirror export.
- **Build pipeline** with structured messages (severity/source/position) and a
  `build.validate` lint report that names root causes.
- **Task configuration** — list/set interval, priority, watchdog; create tasks.
- **Device tools** — search the 3000+ device repository, add devices
  (PLC → fieldbus chains, e.g. EtherNet/IP), and `device.update` to fix
  device-descriptor library pins.
- **Library management** — add/remove references, diagnose missing libraries,
  search disk, and an `install_missing` orchestrator that auto-fixes version
  pins; install packages via APInstaller.
- **Online/runtime** — login (user-supplied or env-var credentials), download,
  start/stop/reset, read/write/force live variables, timestamped monitoring
  snapshots. Physical-impact ops are `confirm`-gated.
- **Reliability** — heartbeat + liveness detection, hung-watcher auto-recovery,
  process adoption (no duplicate IDEs), and a background modal-dialog guard.
- **Observability** — structured JSON logs + an optional read-only web
  dashboard, plus a hang-proof `codesys.diagnose` tool.

## Why another CODESYS MCP

Earlier community CODESYS MCP projects inspired this one (see
[Acknowledgements](#acknowledgements)), but it is a from-scratch implementation
built around a few deliberate engineering choices that keep it stable on current
service packs:

- **A single-threaded loop on the IDE's primary thread** — no `clr.AddReference`
  for threading and no `execute_on_primary_thread` (removed in SP21.5). It is the
  only model that stays reliable on recent service packs.
- **Atomic file-based IPC** rather than a socket — requests and results are
  exchanged as files (written `*.tmp` then renamed), so it survives a frozen IDE
  and never reads a half-written message.
- **Pydantic-validated wire contracts** on the host side, so a malformed call
  fails cleanly before it ever reaches CODESYS.
- **Self-registering handler modules** — each tool group is one small Python file
  that registers itself, which keeps the 82-tool surface easy to extend.

Breadth (82 tools across 10 areas), dependable unattended operation (see
[Reliability](#reliability--modal-dialog-defense)), and a documented safety model
(see [Security model](#security-model)) round it out — each covered in its own
section below rather than repeated here.

## How it works

```
 Claude ─── stdio ───▶ mcptoolkit_for_codesys.server (CPython 3.11+)
                              │
                              │ writes commands/<id>.json
                              ▼
                       <workdir>/
                              ▲
                              │ writes results/<id>.json
                              │
                       CODESYS.exe --runscript=watcher.py  (IronPython 2.7)
                              │
                              ▼
                       CODESYS Scripting API
                       (projects, online, system, ...)
```

**Two programs, two Pythons.** CODESYS can only be scripted from *inside* its own
embedded Python (IronPython 2.7), which is too old to speak modern MCP. So a
modern-Python **server** handles Claude, and a small **watcher** running inside
CODESYS does the actual work. The server supervises one long-lived `CODESYS.exe`
running `watcher.py` on the IDE's primary UI thread (it yields via
`system.delay()` and never touches `System.Threading` — the only model that stays
stable on SP21.5+).

**The `<workdir>` is the channel, not a scratch folder.** The two halves can't
call each other directly, so they exchange messages as files in one shared
folder: the server drops a request in `commands/`, the watcher writes the answer
to `results/`. The same folder also holds `watcher.ready` (a PID marker), a
`watcher.heartbeat`, a `STOP` sentinel, the staged `watcher.py`, and the log.
Three things follow:

- **Both halves must point at the same workdir**, or they can't talk to each
  other. (This is why a remote SSH setup pins `--workdir` on both sides.)
- **It is the trust boundary.** File IPC has no authentication — whoever can write
  into `commands/` can drive CODESYS. Keep it a private, per-user folder; never a
  shared or network path (see [Security model](#security-model)).
- **One watcher per workdir.** Two IDEs sharing one folder would grab each other's
  messages and corrupt results, so a second server *adopts* a running watcher
  rather than spawning a rival (see [Reliability](#reliability--modal-dialog-defense)).

Why files instead of a socket? IronPython 2.7's networking is fragile and the IDE
can freeze on a dialog; files survive both, and each write is atomic (`*.tmp` +
rename) so a half-written message is never read.

**Startup is lazy.** CODESYS takes 30–90 s to open, so the server spawns it on the
*first tool call*, not at launch — otherwise Claude's ~60 s connect timeout would
trip every time. The first tool call is slow; the rest are fast.

## Install

Requires:

- Windows + CODESYS V3.5 SP22 installed via APInstaller (any SP19+ should also
  work; only SP22 is the stated test target).
- Python 3.11+ on the host.

```
cd "C:\path\to\mcptoolkit-for-codesys"
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e .
```

For development (run the test suite):

```
pip install -e .[dev]
pytest
```

The host test suite (**199 tests**) covers: Pydantic schemas, install discovery
from APInstaller JSON, the full tool registry shape, end-to-end IPC against a
fake watcher, input validation, watcher liveness/adoption, error envelopes,
the dashboard status builder, and the online safety controls (confirm-gate +
env-var credentials). The watcher itself is IronPython 2.7 inside CODESYS and
isn't unit-tested — it's exercised by the **live integration suite** under
`tests/integration/` (gated by `MCPTOOLKIT_LIVE=1`):

```
MCPTOOLKIT_LIVE=1 pytest tests/integration -v
```

That suite spawns/adopts a watcher, builds a full project, asserts
`device.update` takes the build 17 → 0 errors, and round-trips the online
surface. It's off by default so the normal `pytest` run stays CODESYS-free.

## Configure Claude Desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "codesys": {
      "command": "C:\\path\\to\\mcptoolkit-for-codesys\\.venv\\Scripts\\mcptoolkit-for-codesys.exe",
      "args": ["--sp", "22"],
      "env": {
        "MCPTOOLKIT_WORKDIR": "C:\\Users\\you\\AppData\\Local\\mcptoolkit-for-codesys"
      }
    }
  }
}
```

**Command-line arguments** (pass these in the `args` list):

| Argument | Default | Purpose |
|---|---|---|
| `--workdir <dir>` | `%TEMP%\mcptoolkit-for-codesys` | The IPC mailbox folder (`commands/` + `results/`). |
| `--sp <n>` | highest installed | Pin a CODESYS service pack, e.g. `--sp 22`. |
| `--headless` | off (IDE visible) | Launch CODESYS with `--noUI`. |
| `--log-level <lvl>` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |

**Args vs. environment variables — which wins.** Every flag above also has an
`MCPTOOLKIT_*` env-var equivalent, and you can set it in the config's `args` list
*or* its `env` block. They aren't redundant — they're layered:

```
command-line flag   >   environment variable   >   built-in default
```

Use an **argument** for an explicit, per-launch setting; use an **environment
variable** for an ambient one you set once. A few settings are **env-only on
purpose**: `CODESYS_EXE` / `CODESYS_PROFILE` (this machine's install paths — set
once, not per launch), the `MCPTOOLKIT_DEV` debug gate, and credential
*references* (`username_env` / `password_env`) that keep secrets out of `args`,
which are recorded in the conversation transcript. Full reference:
[Environment variables](#environment-variables) and
[docs/configuration.md](docs/configuration.md).

The first call from Claude spawns the IDE (visible by default; pass
`--headless` to launch with `--noUI`).

**Running Claude and CODESYS on different machines?** You can drive a CODESYS IDE
on another PC over SSH — and still watch the IDE on that PC's screen while Claude
operates it. See [Remote access over SSH](#remote-access-over-ssh) below.

## Example: a first session

You drive everything through Claude in plain language — you never call tools
directly. For example:

> **You:** Create a standard CODESYS project at `C:\work\demo.project`, add a
> function block `Motor` with a `Start` method, build it, and tell me whether it
> compiles.

Claude calls, in order:

1. `codesys.project.create_standard` — copies the shipped Standard template
   (Device + Application + MainTask + PLC_PRG) to that path and opens it.
2. `codesys.pou.create` — adds the `Motor` function block.
3. `codesys.pou.create_method` — adds `Start` on `Motor`.
4. `codesys.build.build` — compiles and returns structured errors/warnings.

…then summarizes the result for you. The **first** call opens CODESYS
(~60–90 s); the rest are quick, and you can watch each step happen in the IDE
window. More things you can just ask for:

- *"Open `C:\plant\line2.project` and list any missing libraries."* →
  `project.open` (which auto-attaches a diagnosis) + `library.diagnose`.
- *"Log into the PLC, start it, and read `PLC_PRG.iCounter`."* → `online.login`
  → `online.start` (Claude must pass `confirm: true` — it actuates equipment) →
  `online.read`.
- *"Export the project as a git-friendly source tree under `.\src`."* →
  `project.mirror_export`.

The full list of 82 tools is in [Tools](#tools) below.

## Remote access over SSH

You can run **Claude on one PC and CODESYS on another** on the same network.
MCP's stdio transport means the client launches the server as a subprocess — so
set that command to `ssh`, and it runs the server on the remote machine and pipes
the JSON-RPC stream back:

```json
{
  "mcpServers": {
    "codesys": {
      "command": "ssh",
      "args": ["<user>@<codesys-pc>", "C:\\mcp\\codesys-mcp-stdio.cmd"]
    }
  }
}
```

The server, launcher, and config are otherwise unchanged — SSH just carries the
same stdio stream, and adds the authentication + encryption the local file IPC
deliberately lacks. Three things to know:

- **Seeing the IDE.** A process launched by SSH runs in a non-interactive Windows
  session, so a CODESYS it *spawns* is invisible on the remote screen. To watch
  the IDE there, start it yourself first with the `start-codesys-visible` helper;
  the server then **adopts** that running instance instead of spawning a hidden
  one. (Don't need to see it? Add `--headless`.)
- **Authentication.** Use an SSH **key** — the headless subprocess can't answer a
  password prompt. And no `-t`/`-tt` in the `args`: a forced TTY corrupts the
  JSON-RPC stream.
- **Security.** This exposes live PLC control to a remote machine, so keep it on a
  private network or VPN, behind key auth.

Editable launcher templates are in
[`examples/remote-ssh/`](examples/remote-ssh/), and the full walkthrough —
enabling OpenSSH, key setup, the day-to-day flow, and troubleshooting — is in
**[REMOTE.md](REMOTE.md)**.

## Environment variables

Set these in the config's `env` block or your shell. A command-line flag
overrides its env-var equivalent (see the precedence note above).

| Variable | Purpose |
|---|---|
| `MCPTOOLKIT_WORKDIR` | Where `commands/` + `results/` live. Default: `%TEMP%/mcptoolkit-for-codesys`. |
| `CODESYS_EXE` | Override discovery and use this `CODESYS.exe`. |
| `CODESYS_PROFILE` | Required when `CODESYS_EXE` is set; path to a `.profile.xml`. |
| `MCPTOOLKIT_SP` | Prefer this CODESYS SP (e.g. `22`). |
| `MCPTOOLKIT_HEADLESS` | `1` to launch with `--noUI`. |
| `MCPTOOLKIT_LOG` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |
| `MCPTOOLKIT_DEV` | Set to `1` to register the `_introspect` and `_eval` diagnostic ops in the watcher. These are NOT exposed as MCP tools — they're callable via direct file IPC into `<workdir>/commands/` — but `_eval` evaluates arbitrary IronPython in the watcher's namespace, so it's off by default. Useful for development/debugging API drift. Alternative: drop a sentinel file at `<workdir>/dev.flag` (same effect, no env-var needed). |

## Reliability & modal-dialog defense

The watcher runs single-threaded on the CODESYS UI thread, so a modal
dialog freezes it. Three layers keep the server drivable unattended:

1. **Prevention** — the watcher sets `system.prompt_handling =
   ForwardSimplePrompts | LogSimplePrompts | LogMessageKeys` at startup, so
   simple prompts are auto-answered rather than shown as modals.
2. **Heartbeat + liveness** — the watcher writes `<workdir>/watcher.heartbeat`
   each loop tick (idle) and at each op boundary (busy + op + deadline). The
   host classifies it healthy / **hung** (idle-stale >30s, or busy past the
   op's deadline + grace) / **dead** (PID gone). A hung watcher is killed and
   respawned on the next call; a restarted host **adopts** an already-running
   watcher instead of spawning a duplicate.
3. **Dialog guard** — a host-side background task auto-confirms the watcher's
   own safe dialogs (storage-format upgrade, save prompts) by clicking
   Yes/OK/Continue. It never clicks No/Cancel; an unrecognized dialog is left
   alone and surfaced.

`codesys.diagnose` is the **hang-proof** health check: it reads only host-side
state (PID, heartbeat, the IDE's visible windows) and returns instantly even
when every other tool is timing out — naming the modal that's blocking the IDE.

## Observability

The watcher writes structured JSON-line logs to
`<workdir>/log/watcher.log`. Each dispatched op produces one record with
timestamp, level, pid, message, and an `extra` block carrying the op
name, correlation id, elapsed_ms, and status. The log rotates at ~5MB
(`watcher.log.1` is the previous segment).

Run the optional **dashboard** for a live view (read-only, stdlib, no extra
deps):

```
mcptoolkit-for-codesys-dashboard --workdir <dir> [--port 8765]
```

It tails the workdir at `http://127.0.0.1:8765`: liveness pill, heartbeat
(idle/busy + current op), command/result queue depth, recent log lines, and
any blocking modal dialog (title + message + buttons).

Pair with `codesys.health` / `codesys.diagnose` for fast liveness probes:

- tools timing out → `codesys.diagnose` names any blocking dialog and reports
  liveness (a hung watcher auto-respawns on the next call).
- queue depth growing → commands aren't being drained; the watcher is stuck.
- `injected_globals_count` of 0 → the `register_injected` bridge
  failed; nothing watcher-side works.

## Tools

82 tools across 10 areas. Each is namespaced `codesys.<area>.<verb>`. Tool
descriptions (returned by `list_tools`) carry the per-argument detail; this is
the map.

<details>
<summary><b>Browse all 82 tools by area</b></summary>
<br>

### Meta
- `codesys.ping` — round-trip the watcher; returns IronPython version + registered ops.
- `codesys.info` — which CODESYS install the host is driving.
- `codesys.health` — host + watcher liveness snapshot. CODESYS PID, watcher.ready age, queue depth on the host side; uptime, primary project, build-message count, injected-globals sanity on the watcher side. First call to make when something looks wedged.
- `codesys.diagnose` — hang-proof health check. Reads only host-side state (PID, heartbeat, the IDE's visible windows); never calls the watcher, so it answers instantly even when other tools time out. Names any modal dialog blocking the IDE and reports liveness (healthy/hung/dead).

### Project
- `codesys.project.open` / `create` / `create_standard` / `save` / `save_as` /
  `save_archive` / `close`
  - `create_standard` copies the CODESYS-shipped `Templates/Standard.project`
    (Device + Application + MainTask + PLC_PRG) to your path and opens it. Use
    this instead of `create` for anything that needs to compile.
- `codesys.project.list_open` / `info` / `tree`
- `codesys.project.set_info` — update title / version / author /
  company / description. Merge-semantic; only supplied fields change.
- `codesys.project.mirror_export` — write a git-friendly source
  dump (`<name>.st` per code-bearing object, `__self__.st` for FB
  bodies, Get/Set as separate files under property dirs). Pair with
  `set_info { version: "x.y.z" }` for a CI bump-and-export pipeline.
- `codesys.project.diff` — diff two `mirror_export` snapshots into a
  structured change report (added/removed/changed + per-file line deltas).
  Pure host-side; `include_diff=true` for unified-diff text.

### POU / DUT / GVL
- `codesys.pou.create` — Program / Function Block / Function, any IEC language.
- `codesys.pou.create_dut` — Struct / Enum / Union / Alias / EnumerationWithTextList.
- `codesys.pou.create_gvl`
- `codesys.pou.create_folder` — organize POUs/DUTs/GVLs into a folder tree.
- `codesys.pou.create_method` — methods on a Function Block parent.
- `codesys.pou.create_property` — properties on a Function Block parent;
  Get/Set accessors are auto-created and addressable via
  `<parent>/<name>/Get` and `<parent>/<name>/Set`.
- `codesys.pou.set_text` / `get_text` — declaration + implementation.
- `codesys.pou.list_variables` — parse a declaration into structured
  variables (section/name/type/init/pragma).
- `codesys.pou.add_variable` — insert one variable into a VAR section
  (creates the section if absent) without rewriting the whole declaration.
- `codesys.pou.add_symbol_pragma` — mark a variable for symbol export
  with `{attribute 'symbol' := '<access>'}`.
- `codesys.pou.delete` / `rename` / `find`

**Path syntax** for `target=...` arguments: a bare name (recursive search
across the project, with task-reference shadows filtered out), a
`first/anywhere/then/relative` path (first component anywhere in the tree,
rest walked as direct children), or `/leading/slash/forces/root/relative`.

### Build
- `codesys.build.build` / `rebuild` / `clean` / `clean_all` / `generate_code` / `messages`
- `codesys.build.validate` — build + lint report: a `verdict`, errors
  grouped by source/number, detected `missing_libraries`, and `flags` for
  common root causes (device_not_installed, missing_library, task_limit) each
  naming the fix tool. One call instead of build + manual triage.
- `codesys.build.force_recompile` (power-user escape hatch) —
  kill+respawn CODESYS, reopen the project, then build. Useful if you
  suspect in-memory state is stuck. Costs ~30-60s; not needed in
  normal use since regular `build.build` runs cleanly now.

### Online / runtime
- `codesys.online.login` — `mode` maps to OnlineChangeOption:
  `never`/`download` (full download), `try`/`online_change`, `force`, `keep`.
  Requires the device's connection (gateway + node) configured and a runtime
  reachable. Credentials are **user-supplied** via `username`/`password` (or
  `username_env`/`password_env` to keep secrets out of the tool-call log) —
  never auto-generated. `setup_initial_user` (default off) creates a new device
  user only when explicitly requested.
- `codesys.online.set_credentials` — register/​provision device-user credentials
  separately from login. Same user-supplied rule.
- `codesys.online.logout` / `state`
- `codesys.online.start` — **`confirm: true` required** (equipment may run).
- `codesys.online.stop`
- `codesys.online.reset` — `kind` warm/cold/origin + `force_kill`;
  **`confirm: true` required** (clears retained state/outputs).
- `codesys.online.read` — read one/many IEC expressions from the live device.
- `codesys.online.write` — write live values; **`confirm: true` required**.
- `codesys.online.force` / `unforce_all` — `force` requires **`confirm: true`**.
- `codesys.online.snapshot` — timestamped batch read of several
  expressions (monitoring). Read structs/arrays member-by-member
  (`st.member`, `arr[1]`) — SP22 can't read a whole struct/array in one go.
- `codesys.online.forced` — list currently forced/prepared expressions
  (read-only safety check).
- `codesys.online.create_boot` / `source_download`

> **Comm path is a one-time IDE step.** The gateway/target-node binding isn't
> exposed by the SP22 script API, so the first time you connect a device, set
> it once in the IDE (double-click the PLC → Communication Settings → Scan
> Network → Set active path) and save. After that it persists in the project
> and the online tools drive everything.

### Library Manager
- `codesys.library.list_installed` — enumerate libraries in the system repos.
  Filter with `pattern`, cap with `limit`.
- `codesys.library.list_project` — references in the current project.
- `codesys.library.add` — add a reference. Pass `placeholder=<name>` to
  add as a placeholder pointing at the named library (placeholder
  resolution happens at compile time).
- `codesys.library.remove` — remove a reference by name.
- `codesys.library.update` — bump a referenced library to the latest
  installed version (or pin a specific one with `to`).

### Devices / PLCopenXML
- `codesys.device.list_installed` — search the system device repository
  (3000+ devices) by name / vendor / description / family / category /
  keywords. Returns the DeviceID (type/id/version).
- `codesys.device.categories` — enumerate device categories.
- `codesys.device.tree` — list device nodes in the project with their
  current type/id/version. The starting point for add/update.
- `codesys.device.add` — add a device to the tree. Resolve by
  `device_name` (highest installed version) or explicit `type`+`id`(+`version`);
  `parent` selects the node (`/` for a top-level PLC). Verified building a
  PLC → Ethernet → EtherNet/IP Scanner chain. Fieldbus devices need a
  compatible parent (CODESYS enforces this; the tool surfaces a clear hint).
- `codesys.device.update` — change a device node's version. The fix for
  device-descriptor library pins: updating an old PLC descriptor (e.g.
  PLCWinNT 3.1.3.0, which drags in an uninstalled IoStandard 3.1.3.1) to the
  installed version re-points it at current libraries — turning a broken build
  clean.
- `codesys.device.parameters` — list a device's parameters across its
  connectors: config (IPAddress, SubnetMask, DeviceName, …) and I/O channels
  (`is_mappable_io`, `channel_type`, `mapped`). Filter by `name` or
  `mappable_only`.
- `codesys.device.set_parameter` — set a parameter value by name (e.g.
  `IPAddress` → `"[192, 168, 0, 10]"`, `DeviceName` → `"'plc1'"`). Returns
  before/after. **I/O note:** mappable channels appear in `device.parameters`,
  but channel→variable binding only materializes once the device's process-data
  connections are configured (an EtherNet/IP assembly, a Modbus map, …); a bare
  adapter has no channels to bind. Configuring those connections is device-
  specific and not exposed by the SP22 script API — set them in the IDE or via
  a PLCopenXML import of a pre-configured device.
- `codesys.project.import_xml` — import a PLCopenXML file (devices,
  POU trees, GVLs, whole subsystems). The clean alternative to the
  interactive `Add Device...` wizard. Pass `parent=<path>` to scope.
- `codesys.project.export_xml` — export tree objects to PLCopenXML.
  Pairs with `import_xml`: export once from a working project, replay
  in your CI.

### Symbol configuration
- `codesys.symbol.create_config` — add a Symbol Configuration under the
  application (`export_comments`, `support_opc_ua` flags). Idempotent.
- `codesys.symbol.list` — list the project's symbol configurations.
- **Selecting symbols:** SP22 has no per-symbol scripting API. Mark variables
  for export with the declaration pragma `{attribute 'symbol' := 'readwrite'}`
  (or `'read'`/`'write'`/`'none'`), written via `pou.set_text`; the
  configuration collects them at build.

### Task configuration
- `codesys.task.list` — tasks with `interval`/`priority`/`watchdog` and the POU
  calls assigned to each.
- `codesys.task.set` — set a task's `interval` (IEC TIME), `priority`, and
  watchdog.
- `codesys.task.create` — create a new task (optionally with interval/priority).
- Note: assigning a POU to a task and the execution type
  (cyclic/freewheeling/event) aren't scriptable on SP22 — set those in the IDE
  or via PLCopenXML.

### System / installer
- `codesys.system.list_installations` — wraps `APInstaller.CLI
  --getInstallations`. Re-discover installs after a package add.
- `codesys.system.install_package` — install a `.package` /
  `.cdsv3pkg` via `APInstaller.CLI --install`. Restart CODESYS via
  `build.force_recompile` for new content to surface.

### Library installation + auto-remediation
- `codesys.library.repositories` — list known library repositories.
- `codesys.library.create_repository` — add an editable User repo so
  `library.install` has somewhere to write. One-time setup.
- `codesys.library.install` — install a `.library` /
  `.compiled-library` into an editable repository.
- `codesys.library.find_on_disk` — search the filesystem for matching
  `.library` files. Pure host-side; useful before invoking install.
- `codesys.library.install_missing` — **end-to-end orchestrator**.
  Diagnose → search disk → install matching files → auto-fix version
  pins → re-diagnose. When the project pins a version of an
  already-installed library that no longer exists on disk (the
  `IoStandard 3.1.3.1` case), it removes the broken reference and
  re-adds at the library's bare name so CODESYS resolves to the
  highest installed version. Saves the project. Classifies each
  entry as `installed` / `version_fixed` / `version_mismatch_on_disk` /
  `not_found` / `install_failed` / `fix_failed_at_remove` /
  `fix_failed_at_add`. Set `auto_fix_version=false` to keep the legacy
  report-only behavior.
- `codesys.library.resolve_missing` — invoke IDE's "Download missing
  libraries..." workflow with prompts suppressed.
- `codesys.library.diagnose` — scan the project for unresolved
  references and surface them with name/version/vendor parsed from
  the IDE's "library not installed" errors. Auto-attached to the
  `project.open` response when missing refs are detected; call
  directly anytime to re-check after an install.

**Typical missing-library flow** (LLM perspective):
1. `project.open` returns `library_diagnostics` listing missing refs.
2. `library.install_missing` runs the whole find+install+fix loop.
3. If a library is installed but at a different version, the orchestrator
   auto-applies the fix (remove broken pin, re-add at name, save) and
   reports `version_fixed` with the now-resolving `target_version`.
4. If any entries come back `not_found`, the LLM uses
   `system.install_package` (with a vendor-supplied `.package`) or
   `library.resolve_missing` (online IDE fetch).
5. **Caveat**: some CODESYS device descriptors (e.g. PLCWinNT V3)
   declare specific library version dependencies in their `.devdesc`
   files. The Library Manager auto-fix does NOT touch those — they
   surface as build errors sourced from the device name, not the
   Library Manager. A device-side fix is on the roadmap.

</details>

## Version control

CODESYS's Git/SVN integration is a UI add-on and is **not exposed by the script
API** (no VCS commands among the 900+ system commands). The supported path is
the **mirror export + your own VCS**: `project.mirror_export` writes one diffable
`.st` per code object (folder hierarchy preserved, FB bodies as `__self__.st`,
property Get/Set as separate files), which you commit with plain git/svn.

```
# after editing the project via the MCP tools:
codesys.project.set_info { version: "1.2.0" }      # stamp the version
codesys.project.mirror_export { out_dir: "<repo>/src", clean: true }
# then, externally:
git -C <repo> add -A && git -C <repo> commit -m "release 1.2.0"
```

Verified: a `mirror_export` → `git init`/`add`/`commit` round-trip tracks the
exported `.st` files as ordinary diffable source.

## What's verified

<details>
<summary><b>Expand the full end-to-end verification log</b></summary>
<br>

End-to-end against CODESYS V3.5 SP22 Patch 1 (`C:\Program Files\CODESYS 3.5.22.10`):

- Spine: spawn, watcher.ready handshake, MCP `initialize` + `tools/list`, lazy
  spawn (no more 60s init timeout), script staging from UNC source to a local
  workdir.
- `codesys.ping` — round-trips with `pong: true`, ~5–18ms post-spawn. Pass
  `verbose: true` to get the diagnostic block (injected globals + enum member
  names per known enum).
- `codesys.info` — host install summary, including patch number read from
  `Setup.Version.Patch` (the previous code read from `Generation`, always 0).
- `codesys.project.create_standard` — copies `Templates/Standard.project`,
  opens it, returns the project summary. Replaces the bare `project.create`
  for any compile-bearing workflow.
- `codesys.project.tree` — walks the project to a configurable depth and
  serializes name/guid/type/is_folder/children per node.
- `codesys.pou.create` (Program/FB/Function) + `pou.create_folder` /
  `pou.create_method` / `pou.create_property` — all create the right tree
  nodes with the right type GUIDs. Properties auto-generate Get/Set
  accessors.
- `codesys.pou.set_text` / `get_text` — round-trips declaration and
  implementation; the disambiguator now picks the textual POU over the
  task-reference shadow when names collide.
- `codesys.build.build` — runs the compile and returns
  `{errors, warnings, messages}`. Each message is structured with
  `severity` (error/warning/info/status), `text`, `prefix`, `number`
  (error code), `source` (object name like `"PLC_PRG"`), and `position`
  (e.g. `"Line 1, Column 1 (Impl)"`). On the demo machine, a freshly-
  created Standard.project with broken ST in `PLC_PRG` correctly reports
  31 errors + 1 warning including the parser errors at the right
  position; fixing the ST drops the count to 16 (the remaining are
  device-package errors — see Known Limitations).

- `codesys.device.*` — `device.tree` lists nodes with versions; `device.add`
  built a full `PLCWinNT (x64) → Ethernet → EtherNet/IP Scanner` chain;
  `device.update` swapped/updated the PLC descriptor and took a Standard
  project's build from 16/17 errors → **0** (the IoStandard pin fix).
- `codesys.library.*` — `add`/`list_project` add and report references;
  `diagnose` parses missing-library errors; `install_missing` auto-fixes
  version pins; `repositories`/`create_repository`/`install` cover packaging.

Verified end-to-end against the soft PLC (CODESYS Control Win V3 x64):

- Online tools (`codesys.online.*`) — full cycle proven: `login` (with
  user-supplied credentials) → `start` → `read` a live-incrementing counter →
  `write` a variable that changed PLC behavior → `stop` → `logout`. Required a
  one-time interactive comm-path setup in the IDE (gateway/target binding isn't
  scriptable in the SP22 API); after that it's saved in the project and the
  tools drive everything. The earlier `OnlineChangeOption` enum drift and the
  app-resolution-by-GUID bug are fixed.

Reliability, verified by repeated restart cycles + the live integration suite:

- **Process adoption** — a restarted host adopts the running watcher instead of
  spawning a duplicate (no two-IDEs-on-one-workdir races).
- **Hung-watcher recovery** — a stale heartbeat is detected and the watcher is
  killed + respawned on the next call.
- **Dialog guard** — auto-confirmed the storage-format-upgrade modal that
  `device.update` triggers, so the op completes unattended.

</details>

## Known limitations

- **Standard.project ships an old device descriptor.** The CODESYS Standard
  template instantiates `PLCWinNT` (CODESYS Control Win V3) at an old version
  that pins an uninstalled `IoStandard 3.1.3.1`, so a fresh build reports
  `"Device not installed to the system"` + a cascade of `"Unknown type:
  'IoConfigTaskMap'"` etc. (~16 errors). **Fixed in-session** by
  `codesys.device.update` (bump/swap the PLC to the installed version, which
  re-points it at the current IoStandard) — build goes to 0. Only the legacy
  PLCWinNT (device id `0000 0001`) lacks fieldbus slots; the x64 variant
  (`0000 0004`) accepts the EtherNet/IP chain.
- **Comm path isn't scriptable.** Gateway/target-node binding for online login
  must be set once in the IDE per project (then it persists). See the Online
  tools note.
- _legacy device-package note (superseded by `device.update`):_ if a device
  package is genuinely not installed at all, install it via
  `codesys.system.install_package` (APInstaller) — same errors appear opening
  the project in the GUI on a clean machine.
- **`pou.create_folder` summary edge case**: `parent.create_folder(name)`
  on SP22 returns None instead of the folder object. The handler now
  re-queries the parent's children to recover the folder for the
  response payload, but on the off-chance the re-query fails the result
  is `{created: True, name, note: "handle not recoverable"}`.
- **Packaged-app workdir redirection**: Claude Desktop ships as a
  Microsoft Store packaged app. The `MCPTOOLKIT_WORKDIR` env var value
  gets app-container redirected to
  `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\mcptoolkit-for-codesys\`
  instead of the literal path you set. Functionally fine — both the
  Python server and CODESYS see the same redirected location — but if
  you go looking for `commands/results/` files in the configured path,
  they won't be there.
- **`_eval` and `_introspect` watcher ops** are development diagnostics, not
  exposed as MCP tools and **OFF by default** — they register only when
  `MCPTOOLKIT_DEV=1` or a `<workdir>/dev.flag` exists. They execute arbitrary
  IronPython in the IDE process; see the Security model section. Keep them
  disabled outside development.

## Roadmap

Done: device add/update + parameters, symbol configuration, task configuration
(list/set/create), variable-level POU editing, library manager +
auto-remediation + `library.update`, structured `project.diff`, `build.validate`
lint, PLCopenXML import/export, the full online surface incl. monitoring
snapshots, reliability (adoption/recovery/dialog guard), host fake-watcher
tests, the live integration suite, and the mirror-export → VCS workflow.
Remaining / wishlist:

- **`online.*` dry-run** — preview what a write/force/reset would do before the
  confirm.
- **Task POU assignment** — assigning a POU to a task (the slot exists; only
  listing it is scriptable today).

Not feasible in the SP22 script API (documented, not TODO): native Git/SVN VCS
hooks (use the mirror-export workflow), comm-path binding, per-channel I/O
binding without configured process-data, online method calls, whole-struct/array
online reads (read members), and the task execution type (cyclic/freewheeling).

## Security model

This server drives an industrial PLC IDE and can command a live runtime, so
treat it accordingly:

- **The `<workdir>` is the trust boundary.** The host↔watcher channel is
  file-based JSON IPC with **no authentication by design** — anything that can
  write a file into `<workdir>/commands/` can invoke any registered op and
  thereby drive CODESYS. The control that protects this is **filesystem ACLs**:
  keep the workdir on a per-user, non-shared path (the packaged Claude Desktop
  default under `LocalCache` is already user-ACL'd). Never point the workdir at
  a world-writable or network-shared directory.
- **`_eval` / `_introspect` are arbitrary-code dev gates, OFF by default.**
  They execute arbitrary IronPython inside the IDE process and are enabled only
  when `MCPTOOLKIT_DEV=1` **or** a `dev.flag` file exists in the workdir
  (evaluated once at startup). Don't ship `dev.flag`; leave them off in any
  shared or production context.
- **Credentials are user-supplied, never invented.** `online.login` /
  `online.set_credentials` take `username`/`password` from the caller — the
  server never generates, defaults, or hardcodes them, and `setup_initial_user`
  (creating a device account) is opt-in. Prefer `username_env`/`password_env`
  (names of host environment variables the server reads) so the raw secret
  never appears in the tool-call arguments (which are logged in the transcript).
  Note the password still transits `<workdir>/commands/<id>.json` transiently
  (deleted before use) — another reason to ACL the workdir.
- **Physical-impact online ops require `confirm: true`.** `online.start`,
  `reset`, `write`, and `force` actuate real equipment, so they're gated at the
  MCP boundary and return `ConfirmationRequired` unless explicitly confirmed.
- **The dialog guard won't auto-confirm destructive prompts.** It clicks
  Yes/OK on the watcher's own benign modals (storage-format upgrade, save) but
  skips any dialog whose text looks destructive (delete/overwrite/erase/…),
  surfacing those via `codesys.diagnose` instead.
- **Dashboard is read-only and localhost-bound.** `mcptoolkit-for-codesys-dashboard`
  defaults to `127.0.0.1` and exposes log/heartbeat/dialog state; it warns
  loudly if bound to a routable interface (don't, without a firewall).
- **Command ids are filename-validated** before becoming result filenames, so
  a crafted command can't path-traverse out of `results/`.

Subprocess calls (CODESYS spawn, APInstaller, package install) use argument
lists, never `shell=True`. Path/POU/template inputs go through `_validation.py`
(absolute-path, extension, null-byte, `..` rejection) on the host before
reaching the watcher.

## Architecture notes & gotchas

- `KeyboardInterrupt` is NOT an `Exception` subclass in IronPython 2.7 —
  catch it separately. The watcher already does.
- Do not call `system.execute_on_primary_thread` — removed in SP21 P5.
- Do not import `clr` to schedule background work; the watcher must stay on
  the UI thread.
- Each MCP tool call is serialized through the watcher. Concurrent tool calls
  from Claude will be processed in order. This is intentional: the CODESYS
  model is not thread-safe.

## Acknowledgements

- **CODESYS GmbH** — for the CODESYS Development System and its Python
  ScriptEngine, which this server drives. CODESYS® is a registered trademark of
  CODESYS GmbH; this project is independent and not affiliated with or endorsed
  by CODESYS GmbH.
- **Anthropic** — for the [Model Context Protocol](https://modelcontextprotocol.io)
  and the `mcp` Python SDK.
- Prior CODESYS MCP projects that inspired this work (no code reused):
  [`johannesPettersson80/codesys-mcp-toolkit`](https://github.com/johannesPettersson80/codesys-mcp-toolkit)
  and [`phobicdotno/Codesys-MCP-SP21-plus`](https://github.com/phobicdotno/Codesys-MCP-SP21-plus).

## License

MIT — see [`LICENSE`](LICENSE). Declared in `pyproject.toml` via
`license = { file = "LICENSE" }`.
