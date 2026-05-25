"""Tests for mcptoolkit_for_codesys._validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from mcptoolkit_for_codesys._validation import (
    ValidationError,
    validate_object_path,
    validate_project_path,
    validate_template_name,
    validate_workdir,
)


class TestValidateProjectPath:
    def test_absolute_with_correct_extension(self, tmp_path: Path):
        target = tmp_path / "x.project"
        result = validate_project_path(str(target))
        assert result == target

    def test_empty_path_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            validate_project_path("")
        with pytest.raises(ValidationError):
            validate_project_path(None)  # type: ignore[arg-type]

    def test_null_byte_rejected(self):
        with pytest.raises(ValidationError, match="null byte"):
            validate_project_path("C:\\x\x00.project")

    def test_relative_rejected(self):
        with pytest.raises(ValidationError, match="absolute"):
            validate_project_path("foo.project")
        with pytest.raises(ValidationError, match="absolute"):
            validate_project_path(".\\foo.project")

    def test_wrong_extension_rejected(self, tmp_path: Path):
        with pytest.raises(ValidationError, match="\\.project"):
            validate_project_path(str(tmp_path / "x.txt"))

    def test_extension_case_insensitive(self, tmp_path: Path):
        # .PROJECT, .Project all OK
        validate_project_path(str(tmp_path / "x.PROJECT"))
        validate_project_path(str(tmp_path / "x.Project"))

    def test_must_exist_rejected_when_missing(self, tmp_path: Path):
        with pytest.raises(ValidationError, match="does not exist"):
            validate_project_path(str(tmp_path / "nope.project"), must_exist=True)

    def test_must_exist_accepted_when_present(self, tmp_path: Path):
        target = tmp_path / "x.project"
        target.write_text("fake")
        result = validate_project_path(str(target), must_exist=True)
        assert result.exists()

    def test_parent_dir_must_exist(self, tmp_path: Path):
        with pytest.raises(ValidationError, match="parent directory"):
            validate_project_path(str(tmp_path / "subdir" / "x.project"))

    def test_overlong_path_rejected(self, tmp_path: Path):
        long = str(tmp_path) + ("x" * 2000) + ".project"
        with pytest.raises(ValidationError, match="too long"):
            validate_project_path(long)

    def test_custom_extension(self, tmp_path: Path):
        target = tmp_path / "x.library"
        validate_project_path(str(target), extension=".library")


class TestValidateObjectPath:
    def test_bare_name(self):
        assert validate_object_path("PLC_PRG") == "PLC_PRG"

    def test_separator_path(self):
        p = "PLCWinNT/Plc Logic/Application/PLC_PRG"
        assert validate_object_path(p) == p

    def test_leading_slash_root_relative(self):
        # Allowed — the watcher treats leading / as root-relative semantics.
        assert validate_object_path("/POUs/FB_X") == "/POUs/FB_X"

    def test_empty_rejected(self):
        with pytest.raises(ValidationError):
            validate_object_path("")

    def test_dotdot_rejected(self):
        with pytest.raises(ValidationError, match="\\.\\."):
            validate_object_path("A/../B")

    def test_dot_segment_rejected(self):
        with pytest.raises(ValidationError):
            validate_object_path("./X")

    def test_null_byte_rejected(self):
        with pytest.raises(ValidationError, match="null byte"):
            validate_object_path("PLC_PRG\x00")


class TestValidateTemplateName:
    def test_standard(self):
        assert validate_template_name("Standard") == "Standard"

    def test_empty(self):
        assert validate_template_name("Empty") == "Empty"

    def test_with_dot_project_suffix(self):
        # Suffix is stripped in the return.
        assert validate_template_name("Standard.project") == "Standard"

    def test_separator_rejected(self):
        with pytest.raises(ValidationError, match="path separators"):
            validate_template_name("../Standard")
        with pytest.raises(ValidationError):
            validate_template_name("foo/bar")
        with pytest.raises(ValidationError):
            validate_template_name("foo\\bar")

    def test_special_chars_rejected(self):
        with pytest.raises(ValidationError):
            validate_template_name("$Template")
        with pytest.raises(ValidationError):
            validate_template_name("foo:bar")

    def test_empty_rejected(self):
        with pytest.raises(ValidationError):
            validate_template_name("")

    def test_normal_chars_allowed(self):
        validate_template_name("My-Template")
        validate_template_name("v1_2_3")
        validate_template_name("Mixed Case Name")


class TestValidateWorkdir:
    def test_normal_dir(self, tmp_path: Path):
        result = validate_workdir(str(tmp_path))
        assert result == tmp_path

    def test_relative_rejected(self):
        with pytest.raises(ValidationError, match="absolute"):
            validate_workdir("relative/path")

    def test_root_rejected(self):
        with pytest.raises(ValidationError, match="root"):
            validate_workdir("C:\\")
