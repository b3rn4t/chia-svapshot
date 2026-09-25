"""Typed payloads carried on the edges of the SVApshot CHIA loop.

Every value that moves between two ``@ChiaFunction`` nodes is pickled by Ray and
may cross a machine boundary, so the types here are plain dataclasses holding
only JSON-friendly fields. Nothing in this module imports ``ray`` or ``chia``:
it is the vocabulary shared by the nodes, the tool server and the loop driver.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def unwrap(value: Any) -> Any:
    """Return the payload of a value that arrived over a chained ``ObjectRef``.

    Passing one node's ref straight into the next is how CHIA expresses an edge,
    but with the profiler enabled the upstream node returns a
    ``chia.trace.profiler._ProfiledResult`` wrapper carrying worker metadata.
    ``get()`` strips it on the driver; a downstream node receives it intact
    because Ray resolves the ref without CHIA in the path. Nodes therefore call
    this on any argument that may come from another node.
    """
    if type(value).__name__ == "_ProfiledResult" and hasattr(value, "value"):
        return value.value
    return value


def _asdict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return obj


@dataclass
class DutSpec:
    """A design under test, staged into a layout SVApshot can consume.

    SVApshot resolves RTL relative to ``$DUT_ROOT`` and packages relative to the
    ``--includes`` directory, so a staged DUT is always a self-contained
    workspace rather than a pointer into the original source tree.
    """

    module: str
    rtl_relpath: str
    workspace: str
    sources: List[str] = field(default_factory=lambda: ["modules"])
    includes: str = "packages"
    module_type: str = "combinational"
    packages: List[str] = field(default_factory=list)
    origin: str = ""
    line_count: int = 0

    @property
    def rtl_path(self) -> str:
        import os

        return os.path.join(self.workspace, self.rtl_relpath)

    @property
    def ft_dir(self) -> str:
        import os

        return os.path.join(self.workspace, f"ft_{self.module}")

    @property
    def sva_dir(self) -> str:
        import os

        return os.path.join(self.ft_dir, "sva")


def _default_svapshot_root() -> str:
    import os

    return os.environ.get("SVAPSHOT_ROOT") or "/aisva"


@dataclass
class SvapshotConfig:
    """Everything needed to invoke SVApshot that is not part of the DUT."""

    svapshot_root: str = field(default_factory=_default_svapshot_root)
    interpreter: str = "python3"
    llm_model: str = "meta/llama-3.3-70b-instruct"
    execution: str = "validation"
    verbosity: str = "low"
    formal_tool: str = "vcformal"
    assertion_source: str = "rtl"
    timeout_s: int = 3600


@dataclass
class StageResult:
    """Outcome of one SVApshot pipeline stage executed as a subprocess."""

    stage: str
    module: str
    ok: bool
    returncode: int
    elapsed_s: float
    workspace: str
    stdout_tail: str = ""
    stderr_tail: str = ""
    artifacts: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)
    bypassed: bool = False


@dataclass
class PropertyRecord:
    """One SVA property extracted from the snapshot's property file."""

    name: str
    kind: str = "assert"
    text: str = ""
    status: str = "unknown"


@dataclass
class Snapshot:
    """The regression contract SVApshot produced for one DUT revision.

    ``snapshot_worthy`` counts the properties SVApshot proved non-vacuously;
    those are the only ones that belong in a contract you can replay against a
    later RTL revision.
    """

    module: str
    workspace: str
    ok: bool = False
    properties: List[PropertyRecord] = field(default_factory=list)
    proved: int = 0
    failed: int = 0
    inconclusive: int = 0
    vacuous: int = 0
    snapshot_worthy: int = 0
    total_properties: int = 0
    prop_file: Optional[str] = None
    manifest: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Property count, preferring SVApshot's own tally over a text scan.

        The two can disagree: the property file is what the loop last wrote,
        while the metrics describe what the formal tool actually evaluated.
        """
        return self.total_properties or len(self.properties)

    @property
    def yield_ratio(self) -> float:
        """Fraction of generated properties that made it into the contract."""
        return (self.snapshot_worthy / self.total) if self.total else 0.0


@dataclass
class Verdict:
    """The loop's stopping decision plus the feedback handed to the agent."""

    accepted: bool
    iteration: int
    yield_ratio: float
    target_yield: float
    reason: str
    feedback: str = ""
