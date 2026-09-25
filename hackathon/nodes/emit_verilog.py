"""Staged Verilog payload for SVApshot (hackathon / CHIA edge)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class EmitResult:
    """Verilog staged for SVApshot. ``from_chipyard`` is False in fixture mode."""

    workspace: str
    source_root: str
    modules: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    from_chipyard: bool = False
    config: str = ''
    notes: List[str] = field(default_factory=list)
