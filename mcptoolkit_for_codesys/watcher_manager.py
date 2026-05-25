"""
Spawn and supervise the CODESYS.exe watcher process.

The watcher is `CODESYS.exe --profile=... --runscript=watcher.py
--scriptargs="<workdir>"`. We hand it our workdir; it polls commands/ and
writes results/.

Discovery order for the CODESYS install + profile:

  1. Explicit env vars CODESYS_EXE and CODESYS_PROFILE.
  2. APInstaller.CLI.exe --getInstallations (canonical, JSON).
  3. Fallback: well-known paths under Program Files / D:\\Program Files.

We always prefer the highest SP version available unless the user pins one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


APINSTALLER_CLI = Path(
    r"C:\Program Files (x86)\CODESYS\APInstaller\APInstaller.CLI.exe"
)


# How long a heartbeat may be stale before we judge the watcher hung.
# - When idle, the watcher ticks every ~2s; 30s of silence means it's wedged.
# - When busy, we allow the in-flight op's own deadline + this grace before
#   declaring a hang (a legitimate long build shouldn't be killed).
HEARTBEAT_IDLE_STALE_S = 30.0
HEARTBEAT_BUSY_GRACE_S = 30.0


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID currently exists. Cross-platform,
    dependency-free (no psutil)."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True  # couldn't read exit code, but the handle opened
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but not ours to signal
        except OSError:
            return False
        return True


def _kill_pid(pid: int) -> None:
    """Forcefully terminate a process by PID. Best-effort."""
    if not pid or pid <= 0:
        return
    if sys.platform == "win32":
        import ctypes

        PROCESS_TERMINATE = 0x0001
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if handle:
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(int(pid), 9)
        except OSError:
            pass


# Standard Win32 dialog window class. A visible top-level "#32770" owned by the
# CODESYS process is almost always a modal that's blocking the script thread.
_WIN32_DIALOG_CLASS = "#32770"


def capture_windows(pid: int) -> list[dict]:
    """Enumerate visible top-level windows owned by `pid` (Windows only).

    Returns a list of {title, class_name, is_dialog}. Used to explain a hang:
    when the watcher's primary thread is wedged, it's nearly always sitting
    behind a modal dialog, and the dialog's title is the single most useful
    breadcrumb we can hand the LLM/user ("CODESYS is asking: 'Download missing
    libraries?'"). Returns [] off-Windows or on any failure.
    """
    if sys.platform != "win32" or not pid:
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []

    user32 = ctypes.windll.user32
    found: list[dict] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            owner_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            if owner_pid.value != int(pid):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            title_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buf, length + 1)
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)
            title = title_buf.value
            cls = cls_buf.value
            # Skip the empty-titled main shell windows that aren't dialogs.
            is_dialog = cls == _WIN32_DIALOG_CLASS
            if title or is_dialog:
                found.append(
                    {"title": title, "class_name": cls, "is_dialog": is_dialog}
                )
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        return []
    return found


def _dialog_details(pid: int) -> list[dict]:
    """For each #32770 dialog owned by `pid`, read its message text and button
    labels. Returns [{hwnd, text, buttons: [{hwnd, label}]}]. Windows only."""
    if sys.platform != "win32" or not pid:
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []

    user32 = ctypes.windll.user32
    dialogs: list[dict] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _text(h) -> str:
        n = user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, buf, n + 1)
        return buf.value

    def _cls(h) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, buf, 256)
        return buf.value

    def _top_cb(hwnd, _l):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != int(pid) or _cls(hwnd) != _WIN32_DIALOG_CLASS:
                return True
            buttons: list[dict] = []
            statics: list[str] = []

            ENUMCHILD = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def _child_cb(ch, _l2):
                c = _cls(ch)
                t = _text(ch)
                if c == "Button" and t.strip():
                    buttons.append({"hwnd": ch, "label": t.replace("&", "").strip()})
                elif c == "Static" and t.strip():
                    statics.append(t)
                return True

            user32.EnumChildWindows(hwnd, ENUMCHILD(_child_cb), 0)
            dialogs.append({
                "hwnd": hwnd,
                "title": _text(hwnd),
                "text": " ".join(statics)[:500],
                "buttons": buttons,
            })
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_top_cb), 0)
    except Exception:
        return []
    return dialogs


def describe_dialogs(pid: int) -> dict:
    """Summarize any modal dialogs blocking the CODESYS process.

    Returns {dialog_count, dialogs: [{title, text, buttons}], all_windows}.
    `dialogs` is the filtered list of "#32770" modals with their message text
    and button labels — the LLM should surface these. Empty when nothing's
    blocking.
    """
    windows = capture_windows(pid)
    details = _dialog_details(pid)
    return {
        "dialog_count": len(details),
        "dialogs": [
            {"title": d["title"], "text": d["text"],
             "buttons": [b["label"] for b in d["buttons"]]}
            for d in details
        ],
        "all_windows": windows,
    }


# Buttons that are safe to auto-click on the watcher's OWN dialogs during a
# scripted op (no human present). Ordered by preference. We confirm/proceed —
# we never auto-click No/Cancel/Abort, so a dialog we don't understand is left
# alone and surfaced via diagnose instead.
_SAFE_DIALOG_BUTTONS = ("Yes", "OK", "Continue", "Save")

# If a dialog's message contains any of these, DO NOT auto-confirm it — even
# "Yes" could be destructive. Leave it for diagnose to surface so a human (or
# an explicit tool call) decides. Confirming "upgrade storage format" is safe;
# confirming "delete/overwrite/erase" is not something to do behind the user's
# back.
_DESTRUCTIVE_DIALOG_WORDS = (
    "delete", "remove", "erase", "overwrite", "format", "reset to",
    "discard", "factory", "wipe", "permanently",
)


def dismiss_dialogs(pid: int, prefer: tuple[str, ...] = _SAFE_DIALOG_BUTTONS) -> list[dict]:
    """Click a safe default button on each of the watcher's modal dialogs.

    These dialogs are raised by OUR scripted operations (storage-format
    upgrade, save confirmations), not by a user — so auto-confirming the
    default is the right move for unattended automation. Dialogs whose text
    looks destructive are skipped (never auto-confirmed). Returns a list of
    {title, clicked} for each dialog acted on. Windows only; best-effort.
    """
    if sys.platform != "win32":
        return []
    import ctypes

    BM_CLICK = 0x00F5
    user32 = ctypes.windll.user32
    acted: list[dict] = []
    for d in _dialog_details(pid):
        text_low = (d.get("text") or "").lower()
        if any(w in text_low for w in _DESTRUCTIVE_DIALOG_WORDS):
            acted.append({"title": d["title"], "clicked": None,
                          "reason": "skipped: potentially destructive",
                          "buttons": [b["label"] for b in d["buttons"]]})
            continue
        labels = {b["label"].lower(): b["hwnd"] for b in d["buttons"]}
        chosen = None
        for pref in prefer:
            if pref.lower() in labels:
                chosen = (pref, labels[pref.lower()])
                break
        if chosen is None:
            # Unknown dialog — don't guess. Leave it for diagnose to surface.
            acted.append({"title": d["title"], "clicked": None,
                          "buttons": [b["label"] for b in d["buttons"]]})
            continue
        try:
            user32.SendMessageW(chosen[1], BM_CLICK, 0, 0)
            acted.append({"title": d["title"], "clicked": chosen[0]})
        except Exception:
            acted.append({"title": d["title"], "clicked": None})
    return acted


@dataclass
class CodesysInstall:
    key: str               # e.g. "CODESYS 3.5 SP22 (64 bit)"
    version: str           # e.g. "Patch 0" / "3.5.22.0"
    install_dir: Path      # e.g. D:\Program Files\CODESYS 3.5.22.0\CODESYS
    exe: Path              # <install_dir>\Common\CODESYS.exe
    profile: Path          # <install_dir>\Profiles\CODESYS V3.5 SP22.profile.xml
    sp: int                # service-pack number (e.g. 22)
    patch: int             # patch number (e.g. 0)

    def profile_name(self) -> str:
        """Name as expected by `--profile=...`. Stripped of `.profile.xml`."""
        return self.profile.stem.replace(".profile", "")


def _parse_installations(raw: str) -> list[CodesysInstall]:
    out: list[CodesysInstall] = []
    data = json.loads(raw)
    for entry in data:
        pid = entry.get("ProductId") or {}
        gen = (pid.get("Generation") or "").strip()      # "3.5.22.0"
        key = (pid.get("KeyString") or "").strip()       # "CODESYS 3.5 SP22 (64 bit)"
        ver = (pid.get("VersionString") or "").strip()   # "Patch 1"
        install_dir = entry.get("InstallationPath") or entry.get("RootDirectory")
        profiles = entry.get("ProfileFiles") or []
        if not (install_dir and profiles):
            continue
        install_path = Path(install_dir)
        exe = install_path / "Common" / "CODESYS.exe"
        if not exe.exists():
            continue
        # SP comes from "3.5.<sp>.<patch>" generation but the patch field there
        # is always 0 — the real patch number lives in Setup.Version.Patch.
        sp = 0
        patch = 0
        parts = gen.split(".")
        if len(parts) >= 3:
            try:
                sp = int(parts[2])
            except ValueError:
                pass
        setup = entry.get("Setup") or {}
        setup_ver = setup.get("Version") or {}
        try:
            patch = int(setup_ver.get("Patch") or 0)
        except (TypeError, ValueError):
            patch = 0
        out.append(
            CodesysInstall(
                key=key,
                version=ver,
                install_dir=install_path,
                exe=exe,
                profile=Path(profiles[0]),
                sp=sp,
                patch=patch,
            )
        )
    return out


def discover_installs() -> list[CodesysInstall]:
    """Return all CODESYS installs known to APInstaller, sorted newest-first."""
    if not APINSTALLER_CLI.exists():
        log.warning("APInstaller not found at %s — falling back to none.", APINSTALLER_CLI)
        return []
    try:
        proc = subprocess.run(
            [str(APINSTALLER_CLI), "--getInstallations"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.error("APInstaller probe failed: %s", exc)
        return []
    if proc.returncode != 0:
        log.error("APInstaller exited %d: %s", proc.returncode, proc.stderr.strip())
        return []
    try:
        installs = _parse_installations(proc.stdout)
    except (ValueError, KeyError) as exc:
        log.error("APInstaller JSON parse failed: %s", exc)
        return []
    installs.sort(key=lambda i: (i.sp, i.patch), reverse=True)
    return installs


def pick_install(
    prefer_sp: int | None = None,
    explicit_exe: Path | None = None,
    explicit_profile: Path | None = None,
) -> CodesysInstall:
    """Choose which CODESYS to drive. Raises if none found."""

    # 1. Fully explicit override
    if explicit_exe and explicit_profile:
        if not explicit_exe.exists():
            raise FileNotFoundError(f"CODESYS_EXE does not exist: {explicit_exe}")
        if not explicit_profile.exists():
            raise FileNotFoundError(f"CODESYS_PROFILE does not exist: {explicit_profile}")
        return CodesysInstall(
            key="explicit",
            version="explicit",
            install_dir=explicit_exe.parent.parent,
            exe=explicit_exe,
            profile=explicit_profile,
            sp=prefer_sp or 0,
            patch=0,
        )

    installs = discover_installs()
    if not installs:
        raise RuntimeError(
            "No CODESYS installations found. Install CODESYS via APInstaller, "
            "or set CODESYS_EXE and CODESYS_PROFILE."
        )

    if prefer_sp is not None:
        for inst in installs:
            if inst.sp == prefer_sp:
                return inst
        raise RuntimeError(
            f"No CODESYS SP{prefer_sp} install found. Available: "
            + ", ".join(f"SP{i.sp}P{i.patch}" for i in installs)
        )

    return installs[0]


# ---------------------------------------------------------------------------


@dataclass
class WatcherProcess:
    install: CodesysInstall
    workdir: Path
    proc: subprocess.Popen | None = None
    # When we adopt a watcher spawned by a previous host run, we only have its
    # PID (read from watcher.ready), not a Popen handle. `pid` is the source of
    # truth for liveness in that case.
    pid: int | None = None
    adopted: bool = False

    @property
    def ready_marker(self) -> Path:
        return self.workdir / "watcher.ready"

    @property
    def heartbeat_file(self) -> Path:
        return self.workdir / "watcher.heartbeat"

    @property
    def stop_sentinel(self) -> Path:
        return self.workdir / "STOP"

    def is_running(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        if self.pid is not None:
            return _pid_alive(self.pid)
        return False

    def kill(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
        elif self.pid is not None:
            _kill_pid(self.pid)

    def heartbeat(self) -> dict | None:
        """Read the watcher's heartbeat record, or None if absent/unreadable."""
        try:
            raw = self.heartbeat_file.read_text(encoding="utf-8")
            return json.loads(raw)
        except (OSError, ValueError):
            return None

    def liveness(self) -> str:
        """Classify the watcher: 'dead', 'hung', or 'healthy'.

        - dead   : the OS process is gone.
        - hung   : process alive but the heartbeat says it's wedged — idle and
                   silent past HEARTBEAT_IDLE_STALE_S, or busy past the op's own
                   deadline + grace.
        - healthy: process alive and heartbeat fresh (or no heartbeat yet, e.g.
                   just-spawned and still starting — callers gate on the ready
                   marker separately).
        """
        if not self.is_running():
            return "dead"
        hb = self.heartbeat()
        if hb is None:
            # No heartbeat file yet. Treat as healthy here; ready-marker logic
            # handles the startup window.
            return "healthy"
        now = time.time()
        ts = float(hb.get("ts") or 0)
        state = hb.get("state") or "idle"
        if state == "busy":
            started = float(hb.get("op_started_ts") or ts)
            deadline = float(hb.get("deadline_s") or 120.0)
            if (now - started) > (deadline + HEARTBEAT_BUSY_GRACE_S):
                return "hung"
            return "healthy"
        # idle
        if (now - ts) > HEARTBEAT_IDLE_STALE_S:
            return "hung"
        return "healthy"


class WatcherManager:
    """Lifecycle owner for the CODESYS watcher process.

    Responsibilities:
      - Locate CODESYS install + profile.
      - Copy/locate the watcher.py to a path CODESYS can read.
      - Spawn CODESYS.exe --runscript --scriptargs=<workdir>.
      - Wait for watcher.ready marker.
      - Soft-stop via STOP sentinel; hard-kill on timeout.
    """

    def __init__(
        self,
        workdir: Path,
        watcher_script: Path,
        install: CodesysInstall,
        startup_timeout_s: float = 90.0,
        show_ide: bool = True,
    ):
        self.workdir = Path(workdir).resolve()
        # Source watcher.py — may live on a UNC / shared-folder path when the
        # package is installed editably from a VM-mounted dir. We stage the
        # whole scripts/ tree to a local path under workdir before each start
        # so CODESYS reads from a stable local C:\ location.
        self.watcher_script = Path(watcher_script).resolve()
        self._staged_watcher_script: Path | None = None
        self.install = install
        self.startup_timeout_s = startup_timeout_s
        self.show_ide = show_ide
        self._process: WatcherProcess | None = None
        self._start_lock: asyncio.Lock | None = None
        self._dialog_guard: asyncio.Task | None = None

    async def ensure_started(self) -> WatcherProcess:
        """Idempotent + concurrency-safe: ensure a HEALTHY watcher is running.

        Cheap fast-path when the tracked watcher is alive and its heartbeat is
        fresh (no lock acquired). Otherwise the slow path is serialized so
        concurrent callers don't race the spawn:
          - dead   → respawn (or adopt a watcher from a prior host run)
          - hung   → kill + respawn
          - healthy→ return it
        """
        wp = self._process
        if wp is not None:
            state = wp.liveness()
            if state == "healthy":
                return wp
            if state == "hung":
                log.warning(
                    "watcher (pid=%s) appears hung; killing and respawning.",
                    wp.pid or (wp.proc.pid if wp.proc else "?"),
                )
                wp.kill()
                self._process = None
            else:  # dead
                self._process = None
        # Lazily build the lock on the running loop to avoid binding it to a
        # loop that's already closed (e.g. if _serve is re-entered).
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            # Re-check under the lock — another caller may have just recovered.
            wp = self._process
            if wp is not None and wp.liveness() == "healthy":
                return wp
            return await self.start()

    def _prepare_workdir(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "commands").mkdir(exist_ok=True)
        (self.workdir / "results").mkdir(exist_ok=True)
        # Clear stale markers from previous runs
        for stale in ("watcher.ready", "watcher.heartbeat", "STOP"):
            p = self.workdir / stale
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        self._stage_scripts()

    def _stage_scripts(self) -> None:
        """Copy the watcher scripts/ tree to a LOCAL path so CODESYS reads from
        C:\\ rather than a UNC/VM-shared mount. CODESYS `--runscript` silently
        fails to execute from `\\\\host\\share\\...`, so a local staged copy is
        required, not an optimization.

        On kill+respawn, the dying process can hold a lock on <workdir>/_scripts
        for ~1s. Falling back to the UNC source path reintroduces the flakiness
        staging exists to avoid, so the fallback order is:
          1. Refresh <workdir>/_scripts (retry a few times for lock release).
          2. If that fails but a previous staged copy exists, reuse it (stale
             but local and runnable).
          3. Stage to a fresh timestamped dir (sidesteps the locked one).
          4. Only as a last resort use the UNC source path.
        """
        source_dir = self.watcher_script.parent
        staged_dir = self.workdir / "_scripts"
        last_exc: Exception | None = None

        # 1. Try to refresh the canonical _scripts dir, retrying through the
        #    brief window where a just-killed watcher still holds file locks.
        for _ in range(3):
            try:
                if staged_dir.exists():
                    shutil.rmtree(staged_dir)
                shutil.copytree(source_dir, staged_dir)
                self._staged_watcher_script = staged_dir / self.watcher_script.name
                log.info("staged watcher scripts to %s", staged_dir)
                self._cleanup_alt_staging()
                return
            except OSError as exc:
                last_exc = exc
                time.sleep(0.5)

        # 2. Refresh failed (the dir is locked, e.g. AppContainer holding the
        #    old .pyc files). Stage to a FRESH timestamped dir — this always
        #    contains current code, sidestepping the locked _scripts. Preferred
        #    over reusing the existing copy, which may be stale after an edit.
        alt_dir = self.workdir / ("_scripts_%d" % int(time.time()))
        try:
            shutil.copytree(source_dir, alt_dir)
            self._staged_watcher_script = alt_dir / self.watcher_script.name
            log.warning(
                "could not refresh %s (%s); staged to fresh dir %s.",
                staged_dir, last_exc, alt_dir,
            )
            self._cleanup_alt_staging(keep=alt_dir)
            return
        except OSError as exc:
            last_exc = exc

        # 3. Couldn't make a fresh copy at all. Reuse an existing staged copy if
        #    present (stale but local and runnable) before resorting to UNC.
        existing = staged_dir / self.watcher_script.name
        if existing.exists():
            log.warning("reusing existing staged copy at %s (%s).", existing, last_exc)
            self._staged_watcher_script = existing
            return

        # 4. Last resort: UNC source path (flaky, but better than not running).
        log.warning(
            "all local staging failed (%s); falling back to source path %s",
            last_exc, source_dir,
        )
        self._staged_watcher_script = self.watcher_script

    def _cleanup_alt_staging(self, keep: Path | None = None) -> None:
        """Best-effort removal of stale `_scripts_<ts>` dirs from prior spawns
        so they don't accumulate. Skips `keep` and silently ignores locked ones."""
        try:
            for child in self.workdir.glob("_scripts_*"):
                if keep is not None and child == keep:
                    continue
                try:
                    shutil.rmtree(child)
                except OSError:
                    pass
        except OSError:
            pass

    def _build_command(self) -> list[str]:
        script_path = self._staged_watcher_script or self.watcher_script
        cmd: list[str] = [
            str(self.install.exe),
            f'--profile={self.install.profile_name()}',
            f'--runscript={script_path}',
            f'--scriptargs={self.workdir}',
        ]
        if not self.show_ide:
            cmd.append("--noUI")
        return cmd

    def _find_existing_watcher(self) -> WatcherProcess | None:
        """Return a handle to ANY watcher already owning this workdir, whether
        healthy or hung — based purely on a live PID in watcher.ready.

        Critical invariant: there must never be two watchers on one workdir
        (they race for command files and corrupt each other's results). So we
        never spawn alongside an existing live watcher — the caller either
        adopts it (if healthy) or kills it first (if hung) before spawning.
        Must run BEFORE _prepare_workdir (which wipes watcher.ready).
        """
        ready = self.workdir / "watcher.ready"
        try:
            marker = json.loads(ready.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        pid = marker.get("pid")
        if not pid or not _pid_alive(int(pid)):
            return None
        return WatcherProcess(
            install=self.install, workdir=self.workdir, pid=int(pid), adopted=True
        )

    async def start(self) -> WatcherProcess:
        if self._process and self._process.is_running():
            return self._process
        # Never spawn a second watcher on the same workdir. If one is already
        # running, adopt it (healthy) or kill it (hung) — then spawn only if
        # there's nothing live to take over.
        existing = self._find_existing_watcher()
        if existing is not None:
            state = existing.liveness()
            if state == "healthy":
                log.info("adopting existing healthy watcher pid=%s", existing.pid)
                self._process = existing
                return existing
            log.warning(
                "existing watcher pid=%s is %s; killing before respawn.",
                existing.pid, state,
            )
            existing.kill()
            # Give the OS a moment to release the process + file handles.
            await asyncio.sleep(1.0)
        self._prepare_workdir()
        cmd = self._build_command()
        log.info("spawning watcher: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=str(self.workdir),
        )
        wp = WatcherProcess(install=self.install, workdir=self.workdir, proc=proc)
        self._process = wp
        await self._await_ready(wp)
        return wp

    async def _await_ready(self, wp: WatcherProcess) -> None:
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            if wp.ready_marker.exists():
                log.info("watcher ready in %.1fs", time.monotonic() - (deadline - self.startup_timeout_s))
                return
            if not wp.is_running():
                raise RuntimeError(
                    f"CODESYS exited before signaling ready (code={wp.proc.returncode}). "
                    f"Check that watcher.py path and --profile are correct."
                )
            await asyncio.sleep(0.25)
        raise TimeoutError(
            f"watcher did not signal ready within {self.startup_timeout_s:.0f}s. "
            f"Is the CODESYS UI waiting on a dialog?"
        )

    async def stop(self, hard_kill_after_s: float = 15.0) -> None:
        if not self._process:
            return
        wp = self._process
        if not wp.is_running():
            self._process = None
            return
        if wp.adopted:
            # We attached to a watcher we did NOT spawn — one left running by a
            # prior run, or (the SSH case) started in the user's interactive
            # desktop so its UI is visible on the physical machine. We don't own
            # its lifecycle: detach without stopping it, so a remote/secondary
            # client disconnecting never closes someone else's live IDE. The
            # owner stops it (close CODESYS, or the `codesys.shutdown` tool).
            log.info("detaching from adopted watcher pid=%s (left running).", wp.pid)
            self._process = None
            return
        # Soft stop: drop STOP sentinel and let the watcher loop exit.
        try:
            wp.stop_sentinel.write_text("stop")
        except OSError:
            pass
        deadline = time.monotonic() + hard_kill_after_s
        while time.monotonic() < deadline:
            if not wp.is_running():
                self._process = None
                return
            await asyncio.sleep(0.25)
        log.warning("watcher did not exit after %ds; killing.", int(hard_kill_after_s))
        wp.kill()
        self._process = None

    @property
    def process(self) -> WatcherProcess | None:
        return self._process

    def current_pid(self) -> int | None:
        """PID of the running watcher, whether spawned or adopted."""
        wp = self._process
        if wp is None:
            return None
        if wp.pid is not None:
            return wp.pid
        if wp.proc is not None:
            return wp.proc.pid
        return None

    def start_dialog_guard(self, interval_s: float = 2.0) -> None:
        """Run a background task that auto-confirms the watcher's safe modal
        dialogs (storage-format upgrade, save prompts) so scripted operations
        don't wedge waiting for input. Idempotent. Safe to call once the event
        loop is running (e.g. from the server's serve loop)."""
        if self._dialog_guard is not None and not self._dialog_guard.done():
            return
        self._dialog_guard = asyncio.ensure_future(self._dialog_guard_loop(interval_s))

    async def stop_dialog_guard(self) -> None:
        if self._dialog_guard is not None:
            self._dialog_guard.cancel()
            try:
                await self._dialog_guard
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._dialog_guard = None

    async def _dialog_guard_loop(self, interval_s: float) -> None:
        while True:
            try:
                await asyncio.sleep(interval_s)
                pid = self.current_pid()
                if not pid:
                    continue
                acted = await asyncio.to_thread(dismiss_dialogs, pid)
                for a in acted:
                    if a.get("clicked"):
                        log.info(
                            "dialog guard auto-clicked %r on '%s'",
                            a["clicked"], a.get("title"),
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — guard must never die
                log.debug("dialog guard tick failed", exc_info=True)

    def diagnose_hang(self) -> dict:
        """Explain why a call may be stuck: watcher liveness + any modal dialog.

        Called by the server when an IPC call times out. The dialog titles are
        the actionable part — they tell the caller exactly what CODESYS is
        waiting for (e.g. a 'Download missing libraries?' prompt).
        """
        wp = self._process
        pid = self.current_pid()
        info: dict = {
            "watcher_pid": pid,
            "liveness": wp.liveness() if wp is not None else "none",
        }
        if wp is not None:
            hb = wp.heartbeat()
            if hb is not None:
                info["heartbeat"] = hb
        if pid is not None:
            info.update(describe_dialogs(pid))
        return info


def find_watcher_script() -> Path:
    """Locate watcher.py shipped alongside this package."""
    # When installed via pip, hatch force-includes `scripts/` under
    # mcptoolkit_for_codesys/scripts/. In a source checkout it sits one dir above the
    # package.
    here = Path(__file__).resolve().parent
    candidates = [
        here / "scripts" / "watcher.py",
        here.parent / "scripts" / "watcher.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "watcher.py not found near package; expected one of: "
        + ", ".join(str(c) for c in candidates)
    )
