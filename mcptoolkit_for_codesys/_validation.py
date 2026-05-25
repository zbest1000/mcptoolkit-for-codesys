"""Input validation helpers for tool handlers.

The watcher trusts the host. The host is the gate that keeps obviously-bad
inputs from reaching CODESYS. These helpers raise `ValueError` with an
actionable message; tool handlers can catch and return a structured error
to the LLM client.

Conservative by default — anything path-shaped goes through here before we
let the watcher near it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


class ValidationError(ValueError):
    """Raised when a tool argument fails validation. Tool handlers should
    catch this and turn it into a structured response."""


# A reasonable bound on path length on Windows (the OS allows ~32k via
# the long-path API but most real paths are well under 260).
_MAX_PATH_LEN = 1024


def validate_project_path(
    path_str: str,
    *,
    must_exist: bool = False,
    extension: str = ".project",
) -> Path:
    """Normalize and validate a .project path.

    Rules:
      - non-empty string
      - reasonable length
      - no null bytes (defends against odd injection vectors)
      - absolute path (relative paths produce hard-to-debug surprises
        because the watcher's CWD is the IPC workdir, not the user's)
      - correct extension
      - if must_exist: file exists
      - if not must_exist: parent directory exists (we won't auto-create
        the parent — the user should have intentionally placed it)

    Returns the resolved Path.
    """
    if not isinstance(path_str, str) or not path_str:
        raise ValidationError("path is required and must be a non-empty string")
    if "\x00" in path_str:
        raise ValidationError("path contains null byte")
    if len(path_str) > _MAX_PATH_LEN:
        raise ValidationError(
            "path is too long ({} chars; max {}); use a shorter path".format(
                len(path_str), _MAX_PATH_LEN
            )
        )
    p = Path(path_str)
    if not p.is_absolute():
        raise ValidationError(
            "path must be absolute (got {!r}); the watcher's working "
            "directory is the IPC workdir, not yours, so relative paths "
            "won't resolve where you expect".format(path_str)
        )
    if extension and not path_str.lower().endswith(extension.lower()):
        raise ValidationError(
            "path must end with {!r}; got {!r}".format(extension, path_str)
        )
    if must_exist:
        if not p.exists():
            raise ValidationError("file does not exist: {}".format(p))
    else:
        parent = p.parent
        if not parent.exists():
            raise ValidationError(
                "parent directory does not exist: {} (create it first)".format(parent)
            )
    return p


def validate_object_path(path: str) -> str:
    """Sanity check a `/`-separated tree-object path.

    POU/DUT/GVL targets in tool args are paths like
    `"PLCWinNT/Plc Logic/Application/PLC_PRG"` or bare names. The
    actual resolution is done watcher-side by `find_object`. This helper
    only catches obvious nonsense.
    """
    if not isinstance(path, str) or not path:
        raise ValidationError("target is required and must be a non-empty string")
    if "\x00" in path:
        raise ValidationError("target contains null byte")
    if len(path) > _MAX_PATH_LEN:
        raise ValidationError("target is too long")
    # `..` is meaningless in CODESYS tree paths — treat as suspicious.
    parts = [p for p in path.split("/") if p]
    for part in parts:
        if part in ("..", "."):
            raise ValidationError(
                "target may not contain '..' or '.' segments; got {!r}".format(path)
            )
    return path


# Template names ship as files in <install>/Templates/ — validate that the
# user-supplied name doesn't try to escape that directory.
_TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,127}$")


def validate_template_name(name: str) -> str:
    """Restrict to a small printable charset; no separators or escapes.

    `name` may include or omit the trailing `.project`. We return the
    normalized form WITHOUT the extension since the project handler adds
    it. (The handler also disambiguates Standard / Empty / etc.)
    """
    if not isinstance(name, str) or not name:
        raise ValidationError("template is required and must be a non-empty string")
    stripped = name[:-8] if name.lower().endswith(".project") else name
    # Path-separator/escape check first so we get the most specific error
    # message — otherwise the regex catches '../X' as "no slash allowed".
    if "/" in stripped or "\\" in stripped or ".." in stripped:
        raise ValidationError(
            "template name may not contain path separators or '..': {!r}".format(name)
        )
    if not _TEMPLATE_NAME_RE.match(stripped):
        raise ValidationError(
            "template name must match {!r} (got {!r}); only letters, "
            "digits, space, underscore, dot, and hyphen are allowed".format(
                _TEMPLATE_NAME_RE.pattern, name
            )
        )
    return stripped


def validate_workdir(path: Path | str) -> Path:
    """Validate a workdir path. Used at server startup to refuse obviously
    bad values like the system root. Errs on the side of permissive — we
    don't want to lock people out of unusual setups."""
    p = Path(path)
    if not p.is_absolute():
        raise ValidationError("workdir must be absolute: {}".format(p))
    # Forbid the root.
    if p == Path(p.anchor):
        raise ValidationError("workdir must not be the filesystem root: {}".format(p))
    return p
