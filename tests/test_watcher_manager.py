"""Tests for install discovery from APInstaller JSON. Pure-data tests — no
CODESYS install required.

The APInstaller JSON shape is the single most brittle external contract on
the host side. Keep these tests in sync with whatever APInstaller v1 outputs
on currently-supported SP versions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mcptoolkit_for_codesys.watcher_manager import (
    CodesysInstall,
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
        "InstallationPath": "{}\\CODESYS".format(install_root),
        "ProfileFiles": [
            "{}\\CODESYS\\Profiles\\CODESYS V3.5 SP{} Patch {}.profile.xml".format(
                install_root, sp, patch
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
