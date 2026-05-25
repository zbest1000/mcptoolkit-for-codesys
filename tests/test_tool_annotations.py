"""MCP tool annotation classification.

Annotations are advisory risk hints clients surface to the user/model. These
tests pin the classification and — importantly — guard the central name sets
against typos and against drift as tools are added or renamed.
"""
from __future__ import annotations

from mcptoolkit_for_codesys.tools import (
    _DESTRUCTIVE,
    _OPEN_WORLD,
    _READ_ONLY,
    REGISTRY,
    annotations_for,
)

# online.py's own confirm-gated set — the destructive online ops must match it.
_CONFIRM_GATED = {
    "codesys.online.start",
    "codesys.online.reset",
    "codesys.online.write",
    "codesys.online.force",
}


def _all_names() -> set[str]:
    return {s.name for s in REGISTRY.specs()}


def test_every_tool_gets_annotations():
    for spec in REGISTRY.specs():
        tool = spec.to_mcp_tool()
        assert tool.annotations is not None, f"{spec.name} has no annotations"


def test_classification_sets_reference_real_tools():
    """Catch typos/renames: a name in any set must be an actually-registered
    tool, otherwise the hint silently applies to nothing."""
    names = _all_names()
    for label, group in (("_READ_ONLY", _READ_ONLY),
                         ("_DESTRUCTIVE", _DESTRUCTIVE),
                         ("_OPEN_WORLD", _OPEN_WORLD)):
        unknown = group - names
        assert not unknown, f"{label} references unregistered tools: {sorted(unknown)}"


def test_read_only_and_destructive_are_disjoint():
    assert not (_READ_ONLY & _DESTRUCTIVE)


def test_read_only_tools_make_no_destructive_claim():
    for name in _READ_ONLY:
        ann = annotations_for(name)
        assert ann.readOnlyHint is True
        # destructiveHint is only meaningful when not read-only -> left unset.
        assert ann.destructiveHint is None


def test_destructive_tools_are_flagged():
    for name in _DESTRUCTIVE:
        ann = annotations_for(name)
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is True


def test_mutating_majority_marked_non_destructive():
    """A routine editing tool (not in either special set) must be readOnly=False
    AND destructive=False — i.e. we override the spec's destructive-by-default."""
    ann = annotations_for("codesys.pou.set_text")
    assert ann.readOnlyHint is False
    assert ann.destructiveHint is False


def test_confirm_gated_online_ops_are_marked_destructive():
    """The physical-actuation ops online.py gates with confirm:true must also
    carry destructiveHint, so the gate and the hint never disagree."""
    assert _CONFIRM_GATED <= _DESTRUCTIVE


def test_all_online_tools_are_open_world():
    online = {n for n in _all_names() if n.startswith("codesys.online.")}
    assert online <= _OPEN_WORLD, f"missing open-world: {sorted(online - _OPEN_WORLD)}"


def test_local_tools_are_closed_world():
    ann = annotations_for("codesys.pou.create")
    assert ann.openWorldHint is False
