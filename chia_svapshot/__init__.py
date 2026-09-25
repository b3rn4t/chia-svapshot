"""CHIA integration for SVApshot.

Exposes SVApshot's SVA-snapshot pipeline as CHIA nodes and an MCP tool so it can
be orchestrated as a loop on a Ray cluster.
"""

from .state import (
    DutSpec,
    PropertyRecord,
    Snapshot,
    StageResult,
    SvapshotConfig,
    Verdict,
)
from .ppa_state import (
    ElabResult,
    FrozenSnapshot,
    PpaDesign,
    PpaMeasure,
    PpaScore,
    ReproveResult,
    RetainResult,
    RewriteResult,
)

__all__ = [
    "DutSpec",
    "PropertyRecord",
    "Snapshot",
    "StageResult",
    "SvapshotConfig",
    "Verdict",
    "PpaDesign",
    "ElabResult",
    "PpaMeasure",
    "FrozenSnapshot",
    "RewriteResult",
    "RetainResult",
    "ReproveResult",
    "PpaScore",
]
