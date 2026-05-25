"""
Wire-format contracts shared between the MCP host (CPython 3.11+) and the
IronPython 2.7 watcher.

Only the HOST side validates with Pydantic. The watcher uses plain dicts and
hand-rolled checks (no Pydantic — IronPython 2.7 can't run it). Keep the
schema simple and stable; treat this file as the IPC ABI.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Command(BaseModel):
    """Host -> watcher request. Serialized as JSON in commands/<id>.json."""

    id: str = Field(description="Unique correlation ID (uuid4 hex).")
    op: str = Field(description="Handler name, e.g. 'ping', 'project.open'.")
    args: dict[str, Any] = Field(default_factory=dict)
    deadline_s: float = Field(
        default=120.0,
        description="Watcher should abandon the command after this many seconds.",
    )


class Result(BaseModel):
    """Watcher -> host reply. Serialized as JSON in results/<id>.json."""

    id: str
    status: Literal["ok", "error"]
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_kind: str | None = Field(
        default=None,
        description="Short machine-readable error class, e.g. 'NotFound', 'BuildFailed'.",
    )
    elapsed_ms: int = 0
