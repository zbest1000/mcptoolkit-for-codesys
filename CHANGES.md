# Changes

## [Unreleased]

- Docs: a plain-English `docs/` set (concepts, the dashboard watch page,
  reliability, configuration, security), a remote-over-SSH guide (`REMOTE.md`)
  with launcher templates under `examples/remote-ssh/`, and a `RELEASING.md`
  process guide.
- CI: a `release.yml` workflow that builds and publishes the wheel + sdist when a
  `v*` tag is pushed.

## 0.2.1

- **MCP tool annotations.** Every tool now reports `readOnlyHint` /
  `destructiveHint` / `openWorldHint` so a client can badge read-only tools and
  flag or confirm the destructive ones. Classification lives in one reviewable
  place (`tools/__init__.py`) and is guarded by tests: 28 read-only, 5
  destructive (the four `confirm:true` PLC ops + `pou.delete`), 17 open-world.
- **Adopted watchers are no longer stopped on disconnect.** `WatcherManager.stop`
  now detaches from a watcher it didn't spawn (one left running, or started in
  the user's desktop) instead of killing it — so a second client disconnecting
  never closes an IDE someone else is using. Watchers this server spawned are
  still stopped normally.
- 199 host unit tests (added `test_tool_annotations.py`).

## 0.2.0 — Phase 17: richer surface (task config, variable editing, diff/lint, online depth)

Eleven new tools (71 → 82, across 10 areas), each API-discovered by reflection
and verified live on SP22, keeping the discover-then-verify discipline.

### Tier 1 — authoring + config depth
- **Task configuration** (`task.list` / `task.set` / `task.create`): interval
  (IEC TIME), priority, watchdog, and the POU calls per task. Verified live
  (set MainTask 50→20ms/pri 5, created BackgroundTask). Not scriptable on SP22
  (documented): assigning a POU to a task, the execution type.
- **Variable-level POU editing** (`pou.list_variables` / `pou.add_variable` /
  `pou.add_symbol_pragma`): parse a declaration into structured variables and
  insert a single variable or symbol pragma without rewriting the whole text.
- **`library.update`**: bump a referenced library to the latest installed
  version (or pin via `to`).

### Tier 2 — host-side quality tools (zero API risk)
- **`project.diff`**: diff two `mirror_export` snapshots into added/removed/
  changed with per-file line deltas (and optional unified-diff text).
- **`build.validate`**: build + lint report — verdict, errors grouped by
  source/number, detected missing libraries, and `flags` for common root causes
  (device_not_installed / missing_library / task_limit) each naming the fix.
  Verified live (flagged the IoStandard device pin correctly).

### Tier 3 — online depth
- **`online.snapshot`**: timestamped batch read for monitoring; reads struct
  members + array elements + scalars in one call (verified live reading
  `stPos.x/y`, `aData[1..3]`, `iCounter`).
- **`online.forced`**: list forced/prepared expressions (read-only safety check).
- Confirmed limits (documented): whole-struct/array online reads error
  (read members), and there's no online method-call API.

### Tests
- **190 host unit tests** (added `test_quality_tools.py` for project.diff +
  build.validate; registry coverage for all 11 new tools) + **6 live
  integration tests** (added a Tier-1 task/variable/library test).

---

## 0.2.0 — Phase 16: symbol config, device parameters/I/O, VCS workflow + acknowledgements

### Symbol configuration (2 tools)
- `symbol.create_config` — add a Symbol Configuration under the application
  (`export_comments`, `support_opc_ua`); idempotent. `symbol.list` — enumerate
  configs. SP22 has no per-symbol scripting API, so symbol *selection* is via
  the `{attribute 'symbol' := ...}` declaration pragma (written through
  `pou.set_text`), collected at build. Verified live (create + idempotent
  re-create + pragma-marked build = 0 errors).

### Device parameters / I/O (2 tools)
- `device.parameters` — list a device's parameters across its connectors:
  configuration (IPAddress, SubnetMask, DeviceName, …) and I/O channels
  (`is_mappable_io`, `channel_type`, `mapped`). `device.set_parameter` — set a
  value by name. Verified live: set `IPAddress` → `[192,168,0,42]` and
  `DeviceName` → `'mcpPlc'`, build clean. The device-parameter model
  (device → connectors → host_parameters, each with `.value`/`.io_mapping`/
  `.is_mappable_io`) was discovered via reflection. Per-channel variable binding
  needs a device with configured process-data (not exposed by the script API);
  the channels are at least discoverable via `device.parameters`.

### Version control
- Confirmed no native VCS scripting in SP22 (0 git/svn commands among 912).
  Documented the supported path: `mirror_export` → external git/svn. Verified a
  `mirror_export` → `git init/add/commit` round-trip.

### Docs
- Acknowledgements (CODESYS GmbH + trademark note, Anthropic/MCP, referenced
  community projects). Comprehensive README refresh: feature highlights, all 71
  tools, accurate test counts, Version control + Security model sections,
  corrected stale notes, refreshed roadmap. Light deslop of performative
  comments. Fixed the stale "MIT planned" license note.

### Tests
- **174 host unit tests** (registry coverage for the 4 new tools) + the live
  integration suite.

---

## 0.2.0 — Phase 15: online verified live + credential & security hardening

### Online surface verified end-to-end
Drove the full cycle against the soft PLC (CODESYS Control Win V3 x64):
`login` → `start` → `read` a live-incrementing counter → `write` a variable
that changed PLC behavior → `stop` → `logout`. The one prerequisite is a
one-time interactive comm-path setup in the IDE (gateway/target binding isn't
exposed by the SP22 script API); it persists in the project afterward.

### Credentials: user-supplied only, never invented
- `online.login` / `online.set_credentials` take `username`/`password` from the
  caller. The server never generates, defaults, or hardcodes them.
- New `username_env` / `password_env`: name a host env var the server reads, so
  the raw secret never appears in tool-call arguments (which are logged in the
  transcript).
- `setup_initial_user` now defaults to **false** — creating a device user is an
  explicit opt-in, not a login side effect.
- Login auth/policy failures return a clear "ask the user for credentials"
  envelope instead of silently provisioning anything.

### Physical-impact ops are confirm-gated
`online.start` / `reset` / `write` / `force` require `confirm: true`, enforced
host-side before anything reaches the live PLC; otherwise they return a
`ConfirmationRequired` envelope.

### Security review fixes
- **Path traversal (F1, fixed):** result filenames are derived from the command
  `id`; the watcher now validates it (`[A-Za-z0-9_-]{1,128}`, no separators) so
  a crafted command can't write JSON outside `results/`.
- **Dialog guard denylist (F5):** the auto-confirm guard skips any modal whose
  text looks destructive (delete/overwrite/erase/format/discard/wipe/…) — it
  only confirms benign prompts (storage-format upgrade, save).
- **Dashboard exposure (F4):** warns loudly when bound to a non-localhost
  interface (it serves logs/paths/dialog text unauthenticated).
- **Security model** documented in the README (trust boundary = workdir ACLs,
  `_eval` dev-gate, credential handling, confirm-gate).

### Tests
- **170 host unit tests** (added `test_online_safety.py`: confirm-gate + env
  credentials) + the live integration suite.

---

## 0.2.0 — Phase 14: reliability, modal defense, device tools, online fixes, harness, DX, dashboard

A reliability + capability pass that turns the server from "works when
babysat" into "an agent can drive it unattended." Seven workstreams:

### 1. Watcher reliability & auto-recovery
- **Heartbeat**: the watcher writes `<workdir>/watcher.heartbeat` every loop
  tick (idle) and at each op boundary (busy + op + deadline). A single-
  threaded watcher can't update anything while wedged, so a stale heartbeat
  *is* the hang signal.
- **Liveness classification** (`WatcherProcess.liveness()` → healthy/hung/
  dead): dead = PID gone; hung = idle-stale past 30s OR busy past the op's
  own deadline + grace; else healthy.
- **PID adoption**: a restarted host adopts an already-running watcher
  (`_find_existing_watcher`) instead of spawning a duplicate. Invariant: never
  two watchers on one workdir (they race for command files). `start()` adopts
  a healthy existing watcher or kills a hung one before spawning.
- **Robust script staging**: kill+respawn used to fail because the dying
  process held a lock on `<workdir>/_scripts`, and the old code fell back to
  the flaky UNC path (watcher silently never ran). Now: retry → stage to a
  fresh `_scripts_<ts>` dir (always current code) → reuse existing → UNC last.
- **PID liveness/kill** via ctypes (no psutil dependency).

### 2. Modal-dialog defense
- **Prevention**: watcher sets `system.prompt_handling =
  ForwardSimplePrompts | LogSimplePrompts | LogMessageKeys` at startup, so
  simple prompts are auto-answered instead of shown as loop-freezing modals.
- **Detection**: host-side Win32 window enumeration (`describe_dialogs`)
  reads any `#32770` modal's title, message text, and button labels.
- **Auto-dismiss**: a background **dialog guard** (`start_dialog_guard`)
  auto-confirms the watcher's own safe dialogs (storage-format upgrade, save
  prompts) by clicking Yes/OK/Continue — these are raised by *our* scripted
  ops, with no human to click them. Never clicks No/Cancel; unknown dialogs
  are surfaced, not guessed.
- **`codesys.diagnose`** tool: hang-proof health check that reads only host-
  side state (PID, heartbeat, windows) — answers instantly even when every
  other tool is timing out, and names the blocking dialog.
- Server `IpcTimeout` path returns a structured envelope with the hang
  diagnosis instead of a bare "no result".

### 3. Device-tree tools
The old code wrongly claimed there's no device-add API (the methods just
don't show under `dir()` — ScriptObject is a CLR proxy). Discovered the real
surface and added:
- **`device.tree`** — list device nodes with type/id/version.
- **`device.add`** — add a device by `device_name` (repo lookup, highest
  version) or explicit `type`+`id`(+`version`); `parent` selects the node
  (use `/` for a top-level PLC). Verified live: built a full
  PLC → Ethernet → EtherNet/IP Scanner chain.
- **`device.update`** — change a device's version. **This is the real fix for
  the IoStandard build failure**: the legacy PLCWinNT descriptor (3.1.3.0)
  pins an uninstalled IoStandard 3.1.3.1; updating the device to 3.5.22.10
  re-points it at the current library → build goes 16 errors → 0.
- Fixed a latent `device.list_installed` bug: it read PascalCase `.Id`/
  `.Version` (which raise) — DeviceId attributes are lowercase `.id`/
  `.version`/`.type`.

### 4. Online/runtime surface — three real bugs fixed
- `_resolve_app` matched `"application" in <type>` but `type` is a GUID, so it
  never found the app → now matches the Application type GUID
  `639b491f-5557-464c-af91-1471bac9f549`.
- `_login_mode` mapped to non-existent lowercase `OnlineChangeOption` members
  → real members are `Never`/`Try`/`Force`/`Keep`; `login(mode, delete_foreign)`.
- `op_reset` called `reset(opt)` but the signature is `reset(opt, force_kill)`.
- Verified live to the connection boundary: `online.state` works,
  `online.login` reaches "Gateway not configured properly" (past app
  resolution + enum). Full download needs the soft PLC service started
  (elevation) + comm path — environmental, not code.

### 5. Automated pytest integration harness
- `tests/integration/` — live end-to-end suite (the pytest port of
  build_full_project.ps1). Spawns/adopts the watcher, runs the full tool
  surface, asserts `device.update` takes the build 17 → 0. Gated by
  `MCPTOOLKIT_LIVE=1` (default `pytest` stays CODESYS-free via
  `collect_ignore_glob`). Runs the dialog guard so device.update doesn't wedge.

### 6. LLM DX — structured error envelopes
- `error_envelope()` helper: every host-side failure is a parseable JSON
  object (`status`/`error_kind`/`error`/…extra), never a bare string. Server
  catch-all + IpcTimeout path use it.
- `format_result` adds a concise `error_summary` (last traceback line) to
  error Results while keeping the full trace.

### 7. Read-only observability dashboard (optional)
- `mcptoolkit-for-codesys-dashboard` — stdlib-only (no new deps) localhost web page that
  tails the workdir: liveness, heartbeat (idle/busy + op), command/result
  queue depth, log tail, and any blocking modal. Strictly read-only.

### Tests
- **160 unit tests** (up from 130) + **3 live integration tests** passing.

---

## 0.2.0 — Phase 13: install_missing auto-fixes version pins

Built on Phase 12. When the project pins a specific version of an
already-installed library (the IoStandard 3.1.3.1 case — installed but
only as 3.5.17.0 / 3.5.22.0 on disk), `library.install_missing` now
*fixes the pin in place* instead of merely reporting it. New args:

- `auto_fix_version` (default `true`) — on a `version_mismatch_on_disk`
  outcome, the handler calls `library.remove` on the broken pinned
  reference (using the full display name `"Name, X.Y.Z (Vendor)"`) and
  `library.add` with the bare library name. CODESYS resolves the bare
  name to the highest installed version automatically — the library
  reference moves from `IoStandard, 3.1.3.1 (System)` to `IoStandard`
  (wildcard).
- `save_after_fix` (default `true`) — calls `project.save` after the
  remove+add round-trip so the change persists on disk.

New outcome values surfaced in `actions[].outcome`:

- `version_fixed` — pin replaced with wildcard; `target_version` field
  shows the now-resolving version.
- `fix_failed_at_remove`, `fix_failed_at_add` — diagnostic granularity
  when the round-trip fails partway through.

### Live verification

Against a project with `IoStandard, 3.1.3.1 (System)` pinned but only
`3.5.17.0` / `3.5.22.0` on disk:

```
=== install_missing(auto_fix_version=true) ===
before_missing: 1
fixes_applied : 1
saved_after_fix: True
actions:
  - IoStandard v3.1.3.1 -> version_fixed (target=3.5.22.0)
      rm : ok
      add: ok / IoStandard
```

After close + reopen the references list shows the fix persisted:

```
Refs:
  Standard, * (System) | ns: Standard
  IoStandard           | ns: IoStandard          (was: IoStandard, 3.1.3.1 (System))
```

### Known caveat: device-level pins

Some CODESYS device descriptors (e.g. `PLCWinNT V3` in the Standard
template) declare specific library version dependencies directly in
their `.devdesc` file. These pins are independent of the Library Manager
and are NOT addressed by `install_missing` — a build of such a project
will still fail with `PLCWinNT | Failed to insert library IoStandard,
3.1.3.1 (System)` even after the Library Manager fix lands. That's a
device-side issue and surfaces as a build error sourced from the device
name, not the Library Manager. Future work: a `device.change_version`
or `device.update` tool.

---

## 0.2.0 — Phase 12: install-missing orchestrator + filesystem search + user repos

Closing the loop: now that `library.diagnose` identifies what's missing,
add tooling to actually fix it.

### New tools

- **`codesys.library.find_on_disk`** (host-side) — scans known library
  locations (`C:\ProgramData\CODESYS\Managed Libraries` + the driven
  install's `Library` subdir, or user-supplied paths) for `.library` /
  `.compiled-library` / `.compiled-library-v3` files matching a name
  pattern (normalized — spaces/underscores/case ignored) and/or exact
  version. Parses each match's `{vendor, name, version}` from the
  standard `<Repo>/<Vendor>/<Name>/<Version>/<Name>.<ext>` layout.
  Returns matches plus a `by_name` rollup of available versions per
  library.
- **`codesys.library.install_missing`** (host-side orchestrator) —
  end-to-end remediation:
    1. Calls `library.diagnose` for the missing list.
    2. For each missing entry, walks the filesystem search paths.
    3. If an exact-version match is found AND it's not already in a
       managed location, calls `library.install`.
    4. If only different versions are on disk, reports
       `version_mismatch_on_disk` with the available versions and
       advice to update the project's reference pin.
    5. Re-runs `library.diagnose` to verify.
  Per-entry actions clearly categorized: `installed`,
  `version_mismatch_on_disk`, `candidate_found_not_installed`
  (dry-run mode), `install_failed`, `not_found`.
- **`codesys.library.create_repository`** (watcher op) — programmatically
  add an editable User repo via `lm.insert_repository(folder, name,
  index)`. Required pre-step on installs where only the read-only
  `System` repo exists. Live verified: creating a `User-Test` repo
  at `C:\Users\<you>\Documents\codesys-user-libs` showed up
  immediately as `editable: True`.

### What this teaches about the IoStandard case

The user's screenshot showed `IoStandard, 3.1.3.1 (System) has not
been installed`. Live test of `install_missing` against that exact
scenario produced:

```
actions: [{
  missing_entry: { name: "IoStandard", version: "3.1.3.1", vendor: "System", ... },
  matches_on_disk: 2,
  outcome: "version_mismatch_on_disk",
  available_versions: ["3.5.17.0", "3.5.22.0"],
  advice: "Library 'IoStandard' is installed but not at the pinned
           version '3.1.3.1'. Available: ['3.5.17.0', '3.5.22.0'].
           Update the project's reference (remove + re-add) to use one
           of the available versions, or modify the source project to
           use a wildcard version."
}]
```

The "library not installed" error was misleading — IoStandard IS
installed, just not at the version the Standard.project template pins.
The orchestrator detects this and surfaces clear remediation guidance.

### Complete missing-library workflow

1. **One-time setup (if no editable repo)**: `library.create_repository
   { name: "User", folder: "..." }`.
2. **Open a project**: `project.open` auto-attaches `library_diagnostics`
   if any refs are unresolved (Phase 11).
3. **Auto-remediate**: `library.install_missing` — installs matching
   files, surfaces version mismatches with advice.
4. **If a library is missing entirely** (not on disk at any searched
   location): use `system.install_package` (with a `.package` file from
   the CODESYS Store), `library.install` (with a `.library` file you
   have separately), or `library.resolve_missing` (online fetch via
   IDE).

### Tests

- **130 tests** passing in 3.3s (up from 127).
- Registry presence for the 3 new tools.

---

## 0.2.0 — Phase 11: auto-diagnose missing libraries

User report: opening a project surfaces a CODESYS error like

> Could not open library 'IoStandard, 3.1.3.1 (System)'. (Reason: The
> library 'IoStandard, 3.1.3.1 (System)' has not been installed to the
> system.)

The MCP server should turn that into actionable data instead of making
the user dig through the IDE Messages window.

### New tool: `codesys.library.diagnose`

Scans the IDE's Library Manager message category (GUID
`56a60174-4139-411b-86c7-df0da1cfc088` — verified at runtime) for the
canonical "library has not been installed" error, parses each
`Name, version (Vendor)` display string into structured pieces, and
cross-references against the project's `lm.references` list. Returns:

```
{
  "total_references": N,
  "total_missing": M,
  "references": [...],
  "missing": [
    {
      "name": "IoStandard",
      "version": "3.1.3.1",
      "vendor": "System",
      "raw": "IoStandard, 3.1.3.1 (System)",
      "message": "Could not open library '...'. (Reason: ...)",
      "in_references": true,
      "matched_namespace": "IoStandard"
    }
  ],
  "advice": "For each entry in 'missing': install via `codesys.library.install` ..."
}
```

The watcher reads from the *real* signal — the IDE's own error
messages. `ref.managed_library` was probed but is unreliable: it
returns stub objects with `<err>` attrs even for bogus refs, so
detecting unresolved-ness purely from reference metadata is impossible.

### `project.open` auto-runs the diagnostic

When the response would otherwise be clean (`opened: true`), the
handler now scans for the library errors and tacks on a
`library_diagnostics` block IF and only if missing libraries are
detected. Default behavior; pass `diagnose_libraries: false` to
suppress. Adds ~25ms to `project.open` on healthy projects (the scan
returns empty fast).

Live verification on SP22: opened a project with an unresolved
`IoStandard, 3.1.3.1 (System)` reference; the open response included
the diagnostics block with name/version/vendor parsed and a
remediation advice string pointing at the right next tool to call.

### Remediation paths (existing tools)

The `advice` string in the diagnostic points the caller at:

1. `codesys.library.install` — if you have the `.library` /
   `.compiled-library` file on disk.
2. `codesys.system.install_package` — if you have a `.package` /
   `.cdsv3pkg` containing the library (typical CODESYS Store download).
3. `codesys.library.resolve_missing` — invokes the IDE's "Download
   missing libraries..." workflow (online fetch, prompts suppressed —
   best-effort).

After any of these, call `library.diagnose` again to confirm the
missing list is empty.

### Caveat

The diagnostic relies on CODESYS having actually attempted to load
the library and emitted the "not installed" error. For refs added
mid-session that haven't been touched yet (no build, no compile),
CODESYS may not have emitted the error yet — the diagnostic won't see
them. Trigger a `build.build` or `build.generate_code` to force the
load attempts, then re-diagnose.

### Tests + final state

- **127 tests** (up from 126), ~3.2s.
- `codesys.library.diagnose` added to the registry-presence checks.

---

## 0.2.0 — Phase 10: system discovery + package install + PLCopenXML round-trip

System and device inventory tools so the LLM can answer "what's installed
on this machine?" and respond appropriately ("install the missing
package", "find a Modbus device", "drop in this EtherNet/IP scanner").

### New tools — device repository

- **`codesys.device.list_installed`** — search the system device
  repository (3018 devices on this install). Filter by `name` /
  `vendor` / `description` / `family` (case-insensitive substring),
  `category` (integer id), or `keywords` (list, all must match). Each
  result includes the DeviceID (type/id/version) needed to build a
  PLCopenXML import. Returns up to 50 by default; cap is 500.
  - Live test on SP22: `name="modbus"` returned 10 devices including
    `ModbusTCP Slave Device`, `Modbus TCP Master`, `Modbus TCP Slave`,
    `Modbus Master/Slave COM Port` variants.
  - `keywords=["modbus","tcp","master"]` narrows to 2 (Master + Slave
    that mentions Master in its description).
  - `name="ethernet/ip scanner"` returned 3 EtherNet/IP scanners
    (DeviceID type=100).
  - `category=100` returns all EtherNet/IP Scanner-category devices.
- **`codesys.device.categories`** — enumerate the 50 device categories
  the repo knows about (CANbus, CANopenManager, ModbusTCP Server,
  EtherNet/IP Scanner, etc.). Resolved via `get_device_category(id)`.

### New tools — system / installer

- **`codesys.system.list_installations`** — wraps
  `APInstaller.CLI --getInstallations`. Returns the parsed JSON list
  of installed CODESYS Development System instances on this machine.
  Useful for verifying which install the server is driving + spotting
  older instances that may have stale device packages.
- **`codesys.system.install_package`** — installs a `.package` /
  `.cdsv3pkg` file via `APInstaller.CLI --install <path>` (falls
  back to legacy `--installpackage`). The CODESYS Store ships
  device packages and add-ons in this format. After install, the
  watcher needs a fresh CODESYS to see new content — kick
  `build.force_recompile` or restart Claude Desktop.

### New tools — library repositories

- **`codesys.library.repositories`** — list library repositories
  visible to `librarymanager`. Each entry has `name`, `root_folder`,
  `editable`. Most fresh installs only ship the read-only `System` repo;
  installing into an editable User repo is a separate setup step.
- **`codesys.library.install`** — install a `.library` /
  `.compiled-library` file into a library repository. Requires at least
  one editable repo; surfaces a clear error if none configured.
- **`codesys.library.resolve_missing`** — invokes the IDE's "Download
  missing libraries..." command with prompts suppressed. Best-effort —
  if libraries can't be auto-located, falls back silently; query
  `library.list_project` after calling to see what changed.

### New tools — PLCopenXML round-trip

- **`codesys.project.export_xml`** — export tree objects to PLCopenXML
  (TC6 v2.0.0 schema). Pass `objects` list of names/paths to scope;
  empty = whole project. Pairs with `project.import_xml`:
  - Live test on SP22: exported one FB → 1571-byte XML file with
    proper PLCopen schema; imported into a fresh project; the FB
    appears in the tree with declaration + implementation intact.
- **`codesys.project.import_xml`** — fixed the call signature.
  Previously used invalid `(path, True, True)` shape; the correct SP22
  overload is `(String dataOrPath, Boolean bImportFolderStructure)`.
  Discovered via CLR reflection on `proj.import_xml`. Also adds a
  `ConflictResolve.Copy` fallback for projects with name collisions.

### Programmatic device-add: status

`device_repository` has `get_all_devices` / `get_device` but no
`add_device` / `create_device` / `insert_device`. Neither does
ScriptProject, ScriptApplication, or any tree node (verified via
`hasattr` probes across all candidates). The IDE's
`system.commands["Add Device..."]` (three variants) all open
interactive wizard dialogs.

The clean workflow is therefore:
1. `device.list_installed` — find the device's DeviceID + name
2. Build a PLCopenXML snippet containing the device addition
   (export from a working project via `project.export_xml`, save
    to your CI repo)
3. `project.import_xml` — replay the snippet into target projects

Live-verified end-to-end: exported FB from project A → re-imported
into fresh project B → FB present with correct content.

### Tests + final state

- **126 tests** (up from 118), ~3.3s.
- Registry presence checks for all 8 new tools.
- All Phase 10 work end-to-end smoke tested against live SP22.

---

## 0.2.0 — Phase 9: library API rewritten + PLCopenXML import

### Library API rewritten against verified SP22 surface

Smoke-tested the existing `library.*` handlers and found the API I'd
defended against in Phase 6 was a different shape than I'd guessed.
Re-probed the real surface and rewrote:

- **`library.add`** now calls `lm.add_library(name)` (the canonical SP22
  method — takes exactly 1 string arg, no version). With
  `placeholder=<name>`, calls `lm.add_placeholder(placeholder, name)`
  instead. Verified end-to-end against a Standard.project: adding
  `Util` produces a real reference (128ms); adding `3SLicense` as
  placeholder `MyLic` produces a placeholder ref pointing at 3SLicense
  (77ms).
- **`library.remove`** now calls `lm.remove_library(name_string)` —
  SP22's method takes a STRING, not the reference object. The handler
  first checks the project's `lm.references` and surfaces a clear "not
  found" error with the list of actual ref names if you miss; otherwise
  removes (84ms).
- The Phase 6 `allow_unresolved` arg is kept for back-compat but is
  now a no-op: the IDE's `add_library` doesn't validate existence
  pre-add (matching IDE behavior — you can add unresolved refs with a
  warning icon). The Phase 6 `find_library(name)` pre-check turned
  out to expect a FULL display name like
  `"Util, 3.5.17.0 (3S - Smart Software Solutions GmbH)"`, not the
  short name users want to pass; it raised `SystemError: stDisplayName`
  every call. Real safety lives at compile time.

### New: `codesys.project.import_xml`

The clean path for adding devices and any other complex tree
structures without invoking interactive IDE wizards: feed a
PLCopenXML file through `proj.import_xml(path)`. Typical workflow:

1. Build a small library of pre-baked PLCopenXML snippets (export from
   the IDE once, save next to your CI pipeline).
2. `codesys.project.import_xml { path: "snippets/control-rte.xml", parent: "Application" }`
   adds the snippet under the named tree object.

Validates path is absolute, exists, and ends in `.xml`. Parent path
goes through the same `validate_object_path` rules as POU targets.
Tries multiple `import_xml` argument shapes for SP version tolerance.

This is the API that replaces the would-be `device.add` /
`device.replace` tools, which we found cannot be cleanly invoked from
the script API — the relevant `system.commands["Add Device..."]` open
interactive wizard dialogs that require `script_prompt_handling`
configuration. PLCopenXML import sidesteps all of that.

### Tests

- 5 new tests for the import_xml validation (path missing/relative/
  wrong extension/parent traversal) + presence check for the new tool.
- **118 tests** total, ~3.3s.

---

## 0.2.0 — Phase 8: mirror export, project metadata, lifecycle fixes

### `codesys.project.mirror_export` — git-friendly source dump

Walks the project tree and writes one source file per code-bearing
object to a filesystem mirror. Each POU/DUT/GVL becomes a `<name>.st`
file with the declaration and implementation joined by
`(* === DECLARATION === *)` / `(* === IMPLEMENTATION === *)` separators
(diffable by git, readable as-is).

Function Block children (methods, properties) get a nested directory
structure:

```
PLC_PRG.st
POUs/
  FB_Worker/
    __self__.st            <- the FB's own body
    Reset.st               <- method
    Count/                 <- property
      __self__.st            (property body if any)
      Get.st                 (Get accessor)
      Set.st                 (Set accessor)
```

Args: `out_dir` (absolute, required), `clean` (wipe before writing,
default false), `dryrun` (returns the file list without writing,
default false). Path is validated host-side with the same rules as
project paths (absolute, no null bytes). Tested end-to-end on SP22 —
6 files emitted for a Standard.project with one Function Block + a
method + a property.

### `codesys.project.set_info` — project metadata update

Update Project Information fields (title, version, author, company,
description). Supplied fields overwrite; omitted fields are
preserved by merging against the current values via
`get_project_info()`. Tries `set_project_info` / `update_project_info`
on the project first; falls back to direct attribute assignment on
the ScriptProjectInfo object (which is what SP22 actually requires —
the dedicated setter method isn't exposed).

Useful for CI bump-version workflows: after building, call
`project.set_info { version: "1.2.3" }` then `project.save`.

### Bug fixes

- **`project.open` no longer fails on the `update_storage_format`
  keyword arg.** On SP22 the API rejects keyword args for both
  `update_storage_format` and `password` (the IronPython proxy maps to
  a CLR overload that only accepts positional). The handler now tries
  positional first, with multiple fallback shapes. Was breaking every
  open-after-close workflow.
- **`project.list_open` filters compiled libraries by default.** They
  show up in `projects.all` because the IDE treats compiled-libraries
  as Project instances too (e.g. `c:\programdata\codesys\managed
  libraries\system\standard\3.5.22.0\standard.compiled-library-v3`).
  These aren't user projects and were noise in the response. Pass
  `include_libraries=true` to see everything; result includes a
  `filtered_libraries` flag indicating which mode was used.

### Tests

- 6 new tests (113 total, 3.1s runtime): mirror_export validation
  (missing/relative/null-byte/valid out_dir) and registry presence for
  the two new tools.

### End-to-end verification

Live smoke on SP22 covered: create_standard → create_folder + FB +
method + property + property accessor → set_info → info → mirror_export
dryrun → mirror_export real → directory listing → list_open with/
without libraries → save → close → open (the kwarg fix). All green.

---

## 0.2.0 — Phase 7: validation plumbing, CI, structured logs

### Host-side input validation now in front of every path-bearing handler

The Phase 6 `_validation` helpers are now wired into the actual tool
handlers. Bad paths produce a `ValidationError`-tagged response on the
host side without spending a watcher round-trip on them, and the LLM
client gets the validation message in the same `{status, error,
error_kind, data}` envelope the watcher uses for its own errors.

Plumbed:

- `codesys.project.open` — path absolute + `.project` ext + file exists
- `codesys.project.create` — path absolute + `.project` ext + parent dir
  exists
- `codesys.project.create_standard` — same path rules + template name
  passes `validate_template_name` (no `/`, no `\`, no `..`, restricted
  charset)
- `codesys.project.save_as` — `new_path` absolute + `.project` ext +
  parent dir
- `codesys.project.save_archive` — `path` absolute + `.projectarchive`
  ext + parent dir
- `codesys.pou.create` / `create_dut` / `create_gvl` / `create_folder` /
  `create_method` / `create_property` — `parent` path rejects `..` /
  `.` segments and null bytes (when supplied; the field is optional)
- `codesys.pou.set_text` / `get_text` / `delete` / `rename` / `find` —
  `target` path rejects `..` / `.` segments, null bytes, empty strings
  even though the field is required by JSON schema (defense in depth)

Empty strings now reach the validator (previously the wrapper treated
them as "not supplied" and let them through). `target=""` no longer
silently silently produces a "found nothing" error on the watcher side
— it fails fast on the host.

### Tests

- **`tests/test_handler_validation.py`** (14 new tests) — drives the
  real handler functions through a stub `ToolContext` whose `IpcClient`
  raises if called. If validation works, IPC is never reached. If a
  handler regresses, the test fails with a loud "IPC should not have
  been called" message.
- Total **107 tests** passing in ~3.4s.

### CI

- **`.github/workflows/test.yml`** — runs the host-side pytest suite
  against Python 3.11 / 3.12 / 3.13 on push/PR. Also AST-parses every
  file under `scripts/` so obvious syntax mistakes in watcher code get
  caught even though it actually runs on IronPython 2.7.

### Structured watcher logging

Every `log_info` / `log_warn` / `log_error` now writes ONE JSON line to
`<workdir>/log/watcher.log` in addition to the IDE Messages window:

```json
{"ts": 1778626574.12, "level": "info", "pid": 11272, "msg": "watcher starting; workdir=...", "extra": null}
{"ts": 1778626604.91, "level": "info", "pid": 11272, "msg": "op build.build ok in 1689ms",
 "extra": {"op": "build.build", "id": "abc...", "elapsed_ms": 1689, "status": "ok"}}
```

- Log rotates at ~5MB by simple `watcher.log` → `watcher.log.1` rename.
- Each dispatched op emits one entry with `op`, `id`, `elapsed_ms`,
  `status` — useful for after-the-fact analysis of slow / failing
  tools. Success entries DON'T write_message to the IDE (would be too
  chatty); they only go to the structured file.
- Failure entries go to BOTH so they're also visible in the IDE.
- Tail with `Get-Content $workdir\log\watcher.log -Wait | ConvertFrom-Json`
  if you want a live structured view in PowerShell.

---

## 0.2.0 — Phase 6: enterprise-grade polish

This phase adds the operational scaffolding that makes the server safe to
run unattended: tests, license, validation, health probe, and four
library-management tools.

### New tools (4)

- **`codesys.library.list_installed`** — enumerate libraries in the
  system-wide repositories (`librarymanager.get_all_libraries`).
  Supports `pattern` filter and `limit`.
- **`codesys.library.list_project`** — list references on the current
  project. Walks the tree to find the Library Manager object by its
  stable type GUID (`adb5cb65-8e1d-4a00-b70a-375ea27582f3`) so it works
  regardless of localization.
- **`codesys.library.add`** — add a library reference, with safety check
  against the system repos (override with `allow_unresolved=true`).
  Tries multiple SP-version-tolerant call shapes.
- **`codesys.library.remove`** — remove a reference by name. Looks up
  the ref then tries `ref.remove()` / `lm.remove_reference(ref)` /
  `lm.remove(ref)` in order.

### New tool (1)

- **`codesys.health`** — host+watcher liveness snapshot. Host side
  reports CODESYS PID, watcher.ready file age, command/result queue
  depth. Watcher side reports uptime, primary project, open project
  count, current build message count, and the count of CODESYS-injected
  globals visible to helpers (sanity for the `register_injected`
  bridge). Watcher view returns `status: "unreachable"` if the IPC is
  wedged — use it as your first diagnostic call.

### Validation

- **`mcptoolkit_for_codesys/_validation.py`** — host-side input validators:
  `validate_project_path`, `validate_object_path`,
  `validate_template_name`, `validate_workdir`. Raise `ValidationError`
  with actionable messages. Not yet plumbed into tool handlers (next
  phase) but the helpers + 23 tests are in place.

### Tests

- **`tests/`** — pytest suite, **93 tests** covering:
  - Pydantic schemas (`Command` / `Result` round-trip, error shapes,
    watcher-emitted dict parsing).
  - `WatcherManager._parse_installations` against synthetic APInstaller
    JSON — including the Phase 2 patch-parsing fix (was always 0 before
    we started reading `Setup.Version.Patch`).
  - Full tool registry: no duplicate names, every tool has a valid JSON
    schema with `additionalProperties: false`, every `required` field
    is in `properties`, every handler is callable with `(ctx, args)`.
    Parametrized presence check for 29 known tool names.
  - End-to-end IPC against a Python "fake watcher" thread: roundtrip,
    timeout, result-file cleanup, error shape parsing, concurrent calls
    don't cross responses.
  - Validation helpers: 23 cases across project-path, object-path,
    template-name, workdir.
- `pyproject.toml` adds `[project.optional-dependencies] dev = [pytest,
  pytest-asyncio]`. Install with `pip install -e .[dev]`, run with
  `pytest`.

### Other

- **LICENSE** added (MIT). Was placeholder in README.
- **`pyproject.toml`** bumped to 0.2.0 and adds the license field.

---

## 0.2.0 — Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5

### Phase 5: build pipeline root cause and fix

**The "build no-op after first call" was a MISDIAGNOSIS.** Builds were
running correctly on every call; we just weren't capturing the messages
they emit. Found during a deeper probe round (`MCPTOOLKIT_DEV=1` +
`_eval`) of the message-service API.

What actually happens:

- `system.get_message_categories()` returns `System.Guid` instances, NOT
  category objects with `.get_messages()` methods. Calling
  `cat.get_messages()` on a Guid throws `'Guid' object has no
  attribute 'get_messages'` — which the previous `_drain_messages`
  silently swallowed in a try/except. Result: every build returned
  empty messages, masking the real compiler output.
- The correct API is `system.get_message_objects(category_guid)` or
  `system.get_messages(category_guid)`. Both take a Guid.
- The Build category GUID on SP22 is
  `97f48d64-a2a3-4856-b640-75c046e37ea9`. Categories the watcher needs
  to know about, with their descriptions (discovered at runtime):
    - `97f48d64-…` Build
    - `194b48a9-…` Script Messages
    - `d4129827-…` Model Context Protocol Server *(our own [mcp] log!)*
    - `56a60174-…` Library Manager
    - `40f0865b-…` Devices
    - `936e1a33-…` Application Composer
    - `220493a1-…` Additional code checks

Fix:

- **`_drain_messages` now queries `system.get_message_objects(build_guid)`.**
  Returns the full message list including compile errors, warnings,
  status/section markers, and the "Compile complete -- N errors, M
  warnings" trailer.
- **`_clear_build_messages` clears only the Build category** (was
  `Guid.Empty` which would have wiped the Library / Devices / MCP-log
  categories too).
- **`_classify_severity` recognizes `Severity.Text`** — used for build-
  log section headers like `"------ Build started: ... -------"` and
  `"Typify code..."`. These are mapped to severity `"status"` so they
  don't inflate the info-message counts. Also handles
  `SuppressedInformation` / `SuppressedWarning`.
- **`_summarize_message` reads `position_text`** for the structured
  position field (e.g., `"Line 1, Column 1 (Impl)"`).

End-to-end verification on a Standard.project with broken ST in
`PLC_PRG`:

- `build.build` returns `{errors: 31, warnings: 1}` with 35 structured
  messages including:
    - `"Unexpected token '@' found"` at `Line 1, Column 1 (Impl)` in
      `PLC_PRG` (one for each `@` in the broken impl)
    - `"';' expected instead of 'INVALID'"`
    - `"The code 'INVALID;' has no effect. Is this the intent?"` (the
      warning)
    - `"Compile complete -- 31 errors, 1 warnings"` (status trailer)
- After fixing the ST: `build.build` drops to `{errors: 16, warnings: 0}`
  — the 15 parse errors gone, only the device-not-installed cascade
  remains.

**The 16 leftover errors are environmental, not a server bug.** The
Standard.project template instantiates a `PLCWinNT` device, and this
machine doesn't have the `CODESYS Control Win V3` device package
installed. CODESYS reports `"Device not installed to the system. No
code generation possible."` plus a cascade of `"Unknown type:
'IoConfigTaskMap'"` / `"Unknown type: 'IoConfigConnector'"` etc. End
users will see these errors only on environments where the matching
device package is missing. Install the relevant device package via
APInstaller to make them go away.

`codesys.build.force_recompile` (added in Phase 4) is now mostly
redundant — `build.build` works correctly on every call. Keeping the
tool as a power-user escape hatch (forces a fully fresh CODESYS session
for cases where the in-memory state is suspect) but it shouldn't be
needed in normal use.

### Other Phase 5 changes

- **Dev-ops gate** also accepts a `<workdir>/dev.flag` sentinel file
  (in addition to the `MCPTOOLKIT_DEV=1` env var). Either mechanism
  enables `_introspect` and `_eval`. Useful when you can't restart
  Claude Desktop to set an env var — just `touch <workdir>/dev.flag`.

---

## 0.2.0 — Phase 1 + Phase 2 + Phase 3 + Phase 4

### Phase 3 additions

**New tools (4 — total now 41 registered ops):**

- **`codesys.pou.create_folder`** — create a folder under a parent or at
  the project root. Use to organize POUs/DUTs/GVLs into a tree. Uses
  positional `create_folder(name)` to dodge phobicdotno's `name=`-kwarg
  bug.
- **`codesys.pou.create_method`** — create a method on a Function Block
  parent. Pass `return_type` (e.g. `"BOOL"`) or omit for a void method.
  Optionally sets declaration + implementation in the same call.
- **`codesys.pou.create_property`** — create a property on a Function
  Block parent. Returns the property container; the Get/Set accessors are
  auto-created as children (set their bodies via `pou.set_text` with
  paths like `FB_Counter/Current/Get`).

**`pou.find_object` disambiguation.** A Standard.project has two
`PLC_PRG` entries (the POU object + the MainTask task-reference). The
helper now filters those candidates by type GUID (task-reference =
`413e2a7d-adb1-4d2c-be29-6ae6e4fab820`) and falls back to "objects that
expose `textual_declaration`" when needed. `pou.set_text target="PLC_PRG"`
now works without forcing a full path.

**Smart path resolution.** `_find_by_path` now resolves the FIRST path
component via recursive search (`proj.find`), then walks subsequent
components as direct children. Three usable syntaxes:

- `"PLC_PRG"` — bare name, recursive lookup (preferred for short paths).
- `"FB_Counter/Current/Get"` — first component anywhere in tree, rest
  relative to that match. Lets you reference deeply-nested objects
  without spelling out the device/PlcLogic/Application chain.
- `"/POUs/FB_Counter"` — leading `/` forces root-relative semantics
  (direct children of project root only). Use this when the first
  component has multiple matches in the tree and you specifically want
  one at the top.

**Lean ping.** The diagnostic `injected_globals` + `enum_members` fields
(~10KB per response) now only emit when the caller passes
`verbose=true`. Default response is small enough for an LLM to reread
each call without bloating context.

**`codesys.build.force_recompile`** — host-side workaround for the SP22
build-no-op-after-first-call limitation. Saves the current project,
kills CODESYS, respawns it, reopens the project, and runs the build.
Because every fresh CODESYS session's first build is real, this
delivers a working recompile at the cost of ~30-60s wall-clock. Not for
interactive editing; for CI-style verification after a batch of source
edits. Implemented purely host-side using the existing `manager.stop()`
and `ensure_started()` machinery — no new watcher op.

**`pou.create_folder` summary fix.** `parent.create_folder(name)` on SP22
returns None (void method), not the folder ScriptObject. The handler now
re-queries the parent's children to find the freshly created folder so
the response payload has the correct name/guid/type instead of empty
strings.

**Dev ops gating.** `_introspect` and `_eval` (the diagnostic ops used
heavily during Phase 1-3) now register only when `MCPTOOLKIT_DEV=1` is
set in the environment. Default registry is 39 production ops; with
dev=1 it's 41. `_eval` is powerful enough to evaluate arbitrary
IronPython in the watcher's namespace — fine for local development on
your own machine, but you don't want it exposed by default in any
deployed setting.

### Phase 3 deferred to Phase 4

- **`build.build` no-op investigation.** Confirmed: first build per
  CODESYS session takes ~1.3s (real compile, fresh project), subsequent
  builds return in 17–60ms with no errors or messages even when
  `pou.set_text` writes broken ST. `app.build`/`rebuild`/`generate_code`/
  `clean`+`build`/`system.commands['Build'].execute()` all no-op the
  same way. `app.get_signature_crc()` reports "No compile context found
  for application PLCWinNT.Plc Logic.Application" on the non-fresh path.
  `proj.dirty` does flip to True after `pou.set_text` (so the dirty mark
  works) but builds skip anyway. Close+reopen the project mid-session
  produced 234ms — neither full no-op nor full compile — so SOMETHING is
  happening, just not error generation. Likely needs a different API
  entry point that explicitly establishes the compile context, or the
  build is async-queued onto a UI dispatcher that requires a message-
  loop pump we can't issue from inside the watcher's UI-thread script.
- **`OnlineChangeOption` mapping in `online_h.py`** — still misaligned
  for online tools. Defer with online (needs PLC).
- **`codesys_version` cosmetic** — still reports `"0.0"`.
- **`pou.create_folder` summary** returns `{name: "<unnamed>", guid: ""}`
  even though the folder is correctly created and visible in
  `project.tree`. Looks like the wrapper returned from `create_folder`
  is different from the wrapper returned by `proj.find`. Minor.

---

## 0.2.0 — Phase 1 + Phase 2 (complete)

Worked end-to-end against a live CODESYS V3.5 SP22 Patch 1 install. The
inherited 0.1.0 scaffold parsed clean but had never been run; this version
turns it into a working server.

### Headline new capability

- **`codesys.project.create_standard`** — creates a project from CODESYS's
  built-in `Templates/Standard.project` (Device + Plc Logic + Application +
  MainTask + PLC_PRG). Replaces the previous empty-shell `project.create`
  as the right starting point for anything that needs to compile or run.
  Optional `template` argument lets you pick `Empty` or any other template
  that ships with the install.

### Architecture fixes (host)

- **Lazy CODESYS spawn.** `_serve()` no longer awaits `manager.start()`
  during `initialize`. The first MCP `initialize` request has a 60s client
  timeout; CODESYS takes 30–90s to load, so init was always timing out.
  Spawn now runs lazily inside `call_tool` via a new
  `WatcherManager.ensure_started()` (async-locked, idempotent).
  `mcptoolkit_for_codesys/server.py`, `mcptoolkit_for_codesys/watcher_manager.py`,
  `mcptoolkit_for_codesys/tools/__init__.py`.
- **Script staging.** `WatcherManager._stage_scripts()` copies the
  `scripts/` tree to `<workdir>/_scripts/` before each spawn. CODESYS
  scripting is flaky reading `--runscript` from a UNC path; the local
  staged copy avoids it.
- **Install discovery: patch field.** `_parse_installations` now reads the
  patch number from `Setup.Version.Patch` in the APInstaller JSON. The
  previous code parsed it from the 4th segment of `ProductId.Generation`
  ("3.5.22.0"), which is always 0 regardless of installed patch.

### Architecture fixes (watcher)

- **`register_injected(globals())` bridge** — biggest single bug. CODESYS
  injects scripting globals (`projects`, `system`, `online`,
  `device_repository`, `librarymanager`, all the enums) into the runscript
  module's globals — NOT into `__builtin__`. Imported helper modules
  couldn't see them, so every handler going through `_codesys_helpers._g`
  failed with "CODESYS global 'X' not found". `watcher.py` now calls
  `_codesys_helpers.register_injected(globals())` at startup so the bridge
  works. `scripts/watcher.py`, `scripts/_codesys_helpers.py`.
- **`_coerce_for_json` helper** in `_codesys_helpers.py`. Walks
  dict/list/tuple trees coercing IronPython 2.7 `long`, `System.Int64`,
  and other CLR/COM values into json-serializable Python. Applied to every
  result before `json.dumps` in the watcher's `_process_one`. Preempts
  phobicdotno's known `compile_messages` long-serialization bug.
- **`_introspect` op** — diagnostic only. Returns `dir()` and type info for
  a list of named globals. Used during the smoke test to discover the
  actual API surface SP22 exposes (vs. what the 0.1.0 code assumed).
  Callable via direct file IPC (not via MCP) to avoid Claude Desktop
  registration churn during development.
- **`_eval` op** — diagnostic only. Evaluates a Python expression or runs
  a script inside the watcher's namespace with all injected globals
  available. The single most valuable tool for figuring out the Script API
  during development.

### Enum drift fixes (watcher)

Verified at runtime via the diagnostic ping's `enum_members` field.

- `pou_type_from_string`: `PouType.program` → `PouType.Program` (and
  `.FunctionBlock`, `.Function`). SP22 uses PascalCase.
- `dut_type` mapping in `pou_h.py`: same PascalCase fix
  (`Structure`/`Enumeration`/`EnumerationWithTextList`/`Union`/`Alias`);
  added the `EnumerationWithTextList` variant.
- `language_from_string`: enum name `ScriptImplementationLanguage` →
  `ImplementationLanguages` (members `st`/`ladder`/`fbd`/... are
  lowercase and unchanged; only the enum class name was wrong).
- `online.reset` mapping: `warm`/`cold`/`origin` →
  `Warm`/`Cold`/`Original`. Friendly string `"origin"` still accepted as
  an alias for `Original`.

### Build pipeline fixes

- **`_find_application`** rewritten. The previous version walked
  `proj.get_children` looking for `"application"` substring in `ch.type`
  — but `type` returns a GUID string, not a name. Worse, the walked
  ScriptObject doesn't have the build extension methods on it (only the
  generic IScriptObject interface). The fix uses `proj.active_application`,
  which returns the SAME application but with the build extensions
  resolvable via dynamic dispatch. For multi-application projects,
  `proj.set_active_application(target)` then return `active_application`.
- **Message service drift.** `_drain_messages` was calling
  `system.get_message_service().get_message_categories()` — but
  `system.get_message_service()` doesn't exist on SP22. The message
  accessors are direct methods on `system`: `get_message_objects()`,
  `get_messages()`, `get_message_categories()`, `clear_messages()`.
  The handler now uses `get_message_objects()` (structured) with a
  category-walk fallback and a string-list last-resort fallback.
- **Structured message parsing.** `_summarize_message` extracts
  `text`/`severity`/`prefix`/`position_text`/`number`/`source` from the
  `IScriptMessage` objects SP22 returns. Severity is classified against
  the injected `Severity` enum (`Information`/`Warning`/`Error`/
  `FatalError`).
- **Clean before build.** Each `build.build`/`rebuild`/`generate_code`
  calls `system.clear_messages(System.Guid.Empty)` first, so the drained
  messages reflect only the last operation.

### Known limitations (deferred to Phase 3)

- **`build.build` is a no-op after the first compile per CODESYS session.**
  Real first build takes ~1.5s; subsequent calls return immediately with
  no errors and no messages, even after `pou.set_text` injects broken ST
  that should fail compilation. Investigated mechanisms: `app.build()`
  returns None, `app.rebuild()` same, `app.clean()` + `app.build()` same,
  `system.commands['Build'].execute()` same, `app.generate_code()` same.
  Smoking gun: probing `app.get_signature_crc()` directly returns
  *"No compile context found for application PLCWinNT.Plc Logic.Application"*
  — the compile context isn't being established. Likely root cause is one
  of: (a) the application needs to be opened via a different API path that
  initializes the compile context, (b) builds are queued onto a UI thread
  dispatcher that requires a `process_messageloop()` pump after — but
  pumping from inside the watcher's UI-thread runscript deadlocks. Need a
  fresh look in Phase 3.
- **`pou.find_object` ambiguity.** A Standard.project contains two
  `PLC_PRG` entries (the POU under Application and a task-reference under
  MainTask). `pou.set_text target="PLC_PRG"` fails ambiguous; you have to
  use the full path `PLCWinNT/Plc Logic/Application/PLC_PRG`. Want a
  type-aware disambiguator that prefers POU objects when both POU and
  task-reference share a name.
- **`OnlineChangeOption` mapping in `online_h.py` is misaligned.** The
  handler expected members like `login_on_download_no_change_no_oc` but
  the real enum is `Force`/`Keep`/`Never`/`Try` — completely different
  shape (login modes vs. online-change behavior). Won't matter until
  online tools are exercised against a PLC; defer.
- **Pou ring-1 gaps.** `pou.create_property`, `pou.create_method`,
  `pou.create_folder` still aren't implemented. `proj.create_folder`
  exists (verified) and works positional-args-only on SP22; the others
  need a probe round to find the right API.
- **`codesys_version` reports `"0.0"` instead of `"3.5.22.1"`.** The
  improved `_safe_codesys_version` finds *something* (probably a
  zero-initialized Version struct) but not the real version. Cosmetic.
- **Ping response is bloated** with `injected_globals` (~85 entries) and
  `enum_members` (~12 enums × ~30 names) diagnostics. Useful during
  development; should move behind an opt-in arg before 0.2.0 ships.

### Things that turned out NOT to be drift

The handoff brief flagged these as risks; verified at runtime they're fine
on SP22:

- `text_obj.replace(text)` on `textual_declaration` / `textual_implementation`
  is exactly the right method. No fallback needed.
- `Severity` enum has the expected `Information`/`Warning`/`Error` members
  (plus extras: `FatalError`, `SuppressedInformation`, `SuppressedWarning`,
  `Text`). The fallback `_Sev` class in `scripts/watcher.py` is never
  exercised — `Severity` is always injected.
- `DutType`, `OnlineChangeOption`, `ResetOption` all exist as injected
  globals; only the member-name casing was wrong (now fixed for the first
  three; OnlineChangeOption is more involved, deferred).
- SP21 P5 supposedly removed `system.execute_on_primary_thread` — but SP22
  still has it listed on `system`. We don't use it (the threading model
  forbids it), but the absence-of-method assumption in the README is no
  longer accurate.

### How to pick up these changes

The Python MCP server reloads its modules when Claude Desktop spawns a
fresh server process. To pick up host-side changes (lazy spawn, patch
parsing fix, the `project.create_standard` tool registration):

1. Quit Claude Desktop fully (system tray → Quit).
2. Re-launch. The new MCP server process loads the updated code.
3. The new tool `codesys.project.create_standard` appears in the tool list.

Watcher-side changes are picked up automatically on the next CODESYS
spawn — `_stage_scripts` re-copies the source on every `start()`.

The diagnostic `_eval` and `_introspect` ops registered in the watcher are
not exposed as MCP tools (they don't have host-side `ToolSpec`s). They
remain callable via direct file IPC (write a command JSON into
`<workdir>/commands/`) which is intentional — useful for development but
not for LLM clients.
