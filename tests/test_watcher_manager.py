"""Tests for install discovery from APInstaller JSON. Pure-data tests — no
CODESYS install required.

The APInstaller JSON shape is the single most brittle external contract on
the host side. Keep these tests in sync with whatever APInstaller v1 outputs
on currently-supported SP versions.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcptoolkit_for_codesys.watcher_manager import (
    CodesysInstall,
    WatcherManager,
    _parse_installations,
    pick_install,
)


def _make_entry(
    sp: int = 22,
    patch: int = 1,
    install_root: str = r"C:\Program Files\CODESYS 3.5.22.10",
    bitness: int = 64,
):
    """One APInstaller JSON entry as it appears in --getInstallations output."""
    return {
        "ProductId": {
            "ProductType": "CODESYS",
            "Bit": bitness,
            "Generation": "3.5.{}.0".format(sp),
            "KeyString": "CODESYS 3.5 SP{} ({} bit)".format(sp, bitness),
            "VersionString": "Patch {}".format(patch),
        },
        # Build paths OS-natively so the tmp_path dirs the tests create line up
        # on both Windows and Linux CI. Hardcoded "\\" separators only match on
        # Windows, which silently made these tests depend on a real CODESYS
        # install (and fail on Linux).
        "InstallationPath": os.path.join(install_root, "CODESYS"),
        "ProfileFiles": [
            os.path.join(
                install_root, "CODESYS", "Profiles",
                "CODESYS V3.5 SP{} Patch {}.profile.xml".format(sp, patch),
            ),
        ],
        "Setup": {
            "Version": {"Patch": patch, "Hotfix": 0, "Build": 0},
        },
    }


class TestParseInstallations:
    def test_empty(self):
        assert _parse_installations("[]") == []

    def test_skips_entries_without_install_dir(self, tmp_path: Path):
        # Setting the exe doesn't matter since we patch existence check.
        bad = _make_entry()
        bad["InstallationPath"] = None
        bad["ProfileFiles"] = []
        out = _parse_installations('[{}]'.format(__import__("json").dumps(bad)))
        assert out == []

    def test_skips_entries_with_missing_exe(self, tmp_path: Path):
        """We check exe.exists() and skip if not found; ensure that's wired."""
        import json as _json

        entry = _make_entry(install_root=str(tmp_path / "nonexistent"))
        out = _parse_installations(_json.dumps([entry]))
        # exe at <root>/CODESYS/Common/CODESYS.exe doesn't exist
        assert out == []

    def test_parses_patch_from_setup_version_not_generation(self, tmp_path: Path):
        """The bug we fixed in Phase 2: previously patch was parsed from
        Generation's 4th segment (always 0). Now reads Setup.Version.Patch.
        """
        import json as _json

        install_root = tmp_path / "fake-codesys-3.5.22.10"
        exe = install_root / "CODESYS" / "Common" / "CODESYS.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("fake")

        entry = _make_entry(sp=22, patch=7, install_root=str(install_root))
        out = _parse_installations(_json.dumps([entry]))
        assert len(out) == 1
        install = out[0]
        assert install.sp == 22
        assert install.patch == 7  # would be 0 under the old bug
        assert install.version == "Patch 7"
        assert install.key == "CODESYS 3.5 SP22 (64 bit)"

    def test_extracts_profile_name(self, tmp_path: Path):
        import json as _json

        install_root = tmp_path / "fake"
        (install_root / "CODESYS" / "Common").mkdir(parents=True)
        (install_root / "CODESYS" / "Common" / "CODESYS.exe").write_text("x")

        entry = _make_entry(install_root=str(install_root))
        installs = _parse_installations(_json.dumps([entry]))
        assert len(installs) == 1
        assert installs[0].profile_name() == "CODESYS V3.5 SP22 Patch 1"

    def test_handles_malformed_setup_version(self, tmp_path: Path):
        import json as _json

        install_root = tmp_path / "fake"
        (install_root / "CODESYS" / "Common").mkdir(parents=True)
        (install_root / "CODESYS" / "Common" / "CODESYS.exe").write_text("x")

        entry = _make_entry(install_root=str(install_root))
        entry["Setup"]["Version"]["Patch"] = "garbage"  # not an int
        installs = _parse_installations(_json.dumps([entry]))
        assert installs[0].patch == 0  # falls back gracefully

    def test_multiple_installs(self, tmp_path: Path):
        import json as _json

        roots = []
        for sp, patch in [(20, 1), (22, 1), (22, 3)]:
            r = tmp_path / "sp{}p{}".format(sp, patch)
            (r / "CODESYS" / "Common").mkdir(parents=True)
            (r / "CODESYS" / "Common" / "CODESYS.exe").write_text("x")
            roots.append((sp, patch, r))

        entries = [_make_entry(sp=sp, patch=patch, install_root=str(r))
                   for sp, patch, r in roots]
        installs = _parse_installations(_json.dumps(entries))
        assert len(installs) == 3
        # They come back in input order; discover_installs sorts.
        sps_patches = [(i.sp, i.patch) for i in installs]
        assert (22, 1) in sps_patches
        assert (22, 3) in sps_patches
        assert (20, 1) in sps_patches


class TestPickInstall:
    def _fake_install(self, sp, patch, root: Path) -> CodesysInstall:
        return CodesysInstall(
            key="CODESYS 3.5 SP{} (64 bit)".format(sp),
            version="Patch {}".format(patch),
            install_dir=root,
            exe=root / "Common" / "CODESYS.exe",
            profile=root / "Profiles" / "fake.profile.xml",
            sp=sp,
            patch=patch,
        )

    def test_explicit_exe_and_profile(self, tmp_path: Path):
        exe = tmp_path / "Common" / "CODESYS.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        prof = tmp_path / "p.profile.xml"
        prof.write_text("p")

        install = pick_install(
            prefer_sp=22,
            explicit_exe=exe,
            explicit_profile=prof,
        )
        assert install.exe == exe
        assert install.profile == prof
        assert install.sp == 22  # from prefer_sp

    def test_explicit_exe_missing_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            pick_install(
                explicit_exe=tmp_path / "nope.exe",
                explicit_profile=tmp_path / "p.profile.xml",
            )


class TestBuildCommand:
    """The spawn command line is built by hand, not via subprocess' argv
    quoting. CODESYS parses the RAW command line (GetCommandLine), so the
    quoting style is part of the contract with the IDE.

    Regression: `subprocess.list2cmdline` wraps the whole `--profile=NAME`
    token in quotes when NAME contains a space. CODESYS then fails to resolve
    the profile and, in `--noUI` mode, aborts with "it must specify a profile"
    (exit code 1). Only the VALUE may be quoted: `--profile="NAME"`.
    """

    def _manager(self, tmp_path: Path, show_ide: bool = True, root: Path | None = None) -> "WatcherManager":
        root = root or tmp_path
        install = CodesysInstall(
            key="CODESYS 3.5 SP22 (64 bit)",
            version="Patch 4",
            install_dir=root,
            exe=root / "Common" / "CODESYS.exe",
            profile=root / "Profiles" / "CODESYS V3.5 SP22 Patch 4.profile.xml",
            sp=22,
            patch=4,
        )
        mgr = WatcherManager(
            workdir=tmp_path,
            watcher_script=tmp_path / "watcher.py",
            install=install,
            show_ide=show_ide,
        )
        mgr._staged_watcher_script = tmp_path / "_scripts" / "watcher.py"
        return mgr

    def test_profile_quotes_only_the_value(self, tmp_path: Path):
        cmd = self._manager(tmp_path)._build_command()
        assert '--profile="CODESYS V3.5 SP22 Patch 4"' in cmd
        # The whole token must NOT be wrapped — that is what breaks --noUI.
        assert '"--profile=' not in cmd

    def test_exe_path_with_spaces_is_quoted(self, tmp_path: Path):
        root = tmp_path / "Program Files" / "CODESYS 3.5.22.40"
        cmd = self._manager(tmp_path, root=root)._build_command()
        assert cmd.startswith('"')

    def test_paths_without_spaces_are_not_quoted(self, tmp_path: Path):
        cmd = self._manager(tmp_path)._build_command()
        assert "--runscript=" in cmd
        assert '--runscript="' not in cmd

    def test_headless_adds_noUI_flag(self, tmp_path: Path):
        cmd = self._manager(tmp_path, show_ide=False)._build_command()
        assert cmd.rstrip().endswith("--noUI")

    def test_ui_mode_has_no_noUI_flag(self, tmp_path: Path):
        cmd = self._manager(tmp_path, show_ide=True)._build_command()
        assert "--noUI" not in cmd

    def test_command_is_a_single_string(self, tmp_path: Path):
        cmd = self._manager(tmp_path)._build_command()
        assert isinstance(cmd, str)
