"""Typed payloads on the edges of the CHIA PPA loop.

Plain dataclasses so Ray can pickle them across workers. Nothing here imports
``ray`` or ``chia``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PortSig:
    """One ANSI port: direction, name, packed range (normalized)."""

    direction: str
    name: str
    packed: str = ""


@dataclass
class PpaDesign:
    """A snapshot DUT staged for the PPA loop (module + child RTL + packages)."""

    name: str
    module: str
    module_type: str
    workspace: str
    tree: str
    rtl_path: str
    sources: List[str] = field(default_factory=list)
    packages: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    rewrite_goal: str = ""
    rewrite_modes: Dict[str, str] = field(default_factory=dict)
    ppa_goal: str = "area_or_delay"
    max_regression: float = 0.05
    origin: str = ""
    ports: List[PortSig] = field(default_factory=list)
    include_dirs: List[str] = field(default_factory=list)
    rewrite_scope: str = "top"
    instantiation_context: str = ""
    parameter_defaults: Dict[str, str] = field(default_factory=dict)
    match_parent: str = ""
    match_instance: str = ""


@dataclass
class ElabResult:
    """VC Formal analyze+elaborate gate. Failure is fatal — not a rewrite."""

    ok: bool
    module: str
    returncode: int = 0
    elapsed_s: float = 0.0
    workspace: str = ""
    log_tail: str = ""
    bypassed: bool = False


@dataclass
class PpaMeasure:
    """One Yosys synth/stat/ltp reading."""

    ok: bool
    label: str
    module: str
    cells: Optional[int] = None
    wires: Optional[int] = None
    levels: Optional[int] = None
    area_um2: Optional[float] = None
    pdk: str = ""
    frontend: str = ""
    elapsed_s: float = 0.0
    workspace: str = ""
    log_tail: str = ""
    bypassed: bool = False


@dataclass
class FrozenSnapshot:
    """Non-vacuous contract frozen on the baseline DUT."""

    ok: bool
    module: str
    directory: str
    prop_file: str
    metrics_file: str
    snapshot_worthy: int
    total: int
    yield_ratio: float
    reason: str
    workspace: str = ""
    elapsed_s: float = 0.0
    bypassed: bool = False


@dataclass
class RewriteResult:
    """LLM RTL candidate. Ports must match the baseline or ``ok`` is False."""

    ok: bool
    attempt: int
    module: str
    tree: str
    rtl_path: str
    sources: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    ports_ok: bool = False
    reason: str = ""
    feedback: str = ""
    rewrite_mode: str = "optimise"
    elapsed_s: float = 0.0
    bypassed: bool = False


@dataclass
class RetainResult:
    """Assertions from the frozen snapshot that still name signals in the rewrite."""

    ok: bool
    module: str
    prop_file: str
    kept: int
    dropped: int
    kept_names: List[str] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    elapsed_s: float = 0.0
    bypassed: bool = False


@dataclass
class ReproveResult:
    """Re-proof of the retained contract against the rewritten DUT."""

    ok: bool
    module: str
    snapshot_worthy: int
    total: int
    failed: int
    reason: str
    feedback: str = ""
    prop_file: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0
    bypassed: bool = False


@dataclass
class PpaScore:
    """Candidate vs baseline Yosys delta."""

    win: bool
    reason: str
    area_delta: Optional[float] = None
    delay_delta: Optional[float] = None
    improved_area: bool = False
    improved_delay: bool = False
    baseline: Dict[str, Any] = field(default_factory=dict)
    candidate: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LecResult:
    """EQY check of a snapshot-clean rewrite against the baseline DUT.

    Observational: a failing LEC does not fail the PPA loop. It records that
    the snapshot accepted a rewrite LEC would have rejected.
    """

    ok: bool
    equivalent: bool
    inconclusive: bool
    snapshot_clean: bool
    module: str
    reason: str
    partitions_failed: int = 0
    frontend: str = ""
    elapsed_s: float = 0.0
    workspace: str = ""
    log_tail: str = ""
    skipped: bool = False
    bypassed: bool = False
