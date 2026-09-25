"""An MCP tool server that lets an agent drive SVApshot directly.

Where the nodes in :mod:`.svapshot_nodes` are *programmatic* edges — the loop
decides when they run — this tool is the *agentic* edge: it is handed to an LLM
along with a prompt, and the model chooses which methods to call and when.

The two styles compose. ``propose_properties`` is an ordinary Python method, so
it re-dispatches the real ``svapshot_prove`` node with
``chia_remote_blocking``: one tool call from the agent fans out scheduled
cluster work on a formal-licence worker and returns a value the agent can reason
about.

Docstrings on the methods below are part of the interface, not commentary — MCP
ships them to the model as the tool descriptions.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from chia.base.tools.ChiaTool import ChiaTool

from .state import DutSpec, StageResult, SvapshotConfig


def _tail_file(path: str, lines: int) -> str:
    if not os.path.isfile(path):
        return f"(not found: {path})"
    with open(path, "r", errors="replace") as handle:
        content = handle.readlines()
    return "".join(content[-lines:])


class SVApshotTool(ChiaTool):
    """Read/write access to one DUT's SVApshot workspace, exposed over MCP."""

    def setup(self, dut: DutSpec, cfg: SvapshotConfig, run_root: str):
        self.dut = dut
        self.cfg = cfg
        self.run_root = run_root
        self.sva_dir = os.path.join(run_root, f"ft_{dut.module}", "sva")

        for method in (
            self.read_rtl,
            self.read_properties,
            self.snapshot_status,
            self.formal_log_tail,
            self.propose_properties,
        ):
            self.mcp.add_tool(method, name=f"{self.name}_{method.__name__}")

    def read_rtl(self) -> str:
        """Return the full SystemVerilog source of the module under snapshot."""
        path = os.path.join(self.run_root, self.dut.rtl_relpath)
        if not os.path.isfile(path):
            return f"(RTL not found at {path})"
        with open(path, "r", errors="replace") as handle:
            return handle.read()

    def read_properties(self) -> str:
        """Return the current SVA property file SVApshot is working on.

        This is the file the formal tool binds to the DUT. It contains the
        property module header, its port list, and every assertion generated so
        far.
        """
        path = os.path.join(self.sva_dir, f"{self.dut.module}_prop.sv")
        if not os.path.isfile(path):
            return f"(no property file yet at {path})"
        with open(path, "r", errors="replace") as handle:
            return handle.read()

    def snapshot_status(self) -> Dict[str, Any]:
        """Return the qualification breakdown of the latest snapshot attempt.

        Keys include ``total_properties``, ``proved_non_vacuous`` (the only
        snapshot-worthy category), ``proved_vacuous``,
        ``failing_property_mismatch`` and ``inconclusive``. Returns a
        ``status`` key explaining the situation when no metrics exist yet.
        """
        import json

        path = os.path.join(self.sva_dir, "snapshot_metrics.json")
        if not os.path.isfile(path):
            return {
                "status": "no metrics yet; the prove stage has not produced a "
                "snapshot_metrics.json for this module",
                "module": self.dut.module,
            }
        with open(path, "r", errors="replace") as handle:
            metrics = json.load(handle)
        return metrics.get("qualification", metrics)

    def formal_log_tail(self, lines: int = 120) -> str:
        """Return the tail of the formal tool log for the current module.

        Args:
            lines: How many trailing lines to return.
        """
        candidates = [
            os.path.join(self.run_root, "vcf_projs", self.dut.module, "vcf.log"),
            os.path.join(self.run_root, "projs", self.dut.module, "jg.log"),
            os.path.join(self.run_root, "vcf.log"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return _tail_file(path, lines)
        return f"(no formal log found; looked in {candidates})"

    def propose_properties(self, sva_text: str) -> Dict[str, Any]:
        """Replace the seed assertions and re-run the SVApshot prove stage.

        Use this once you have read the RTL and the current properties and can
        write better assertions. The call blocks while the formal tool runs and
        returns the outcome.

        Args:
            sva_text: SystemVerilog assertion statements, one per line, without
                a surrounding module. Each should be of the form
                ``name: assert property (@(posedge clk_i) ... );``.

        Returns:
            A dict with ``ok``, ``stage``, ``elapsed_s`` and a log tail.
        """
        # Local import keeps the tool module importable without Ray running,
        # which matters because ChiaTool instances are pickled to their actor.
        from .svapshot_nodes import svapshot_prove, svapshot_seed

        seed_cfg = SvapshotConfig(**{**self.cfg.__dict__, "assertion_source": "empty"})
        scaffold_ok = StageResult(
            stage="scaffold",
            module=self.dut.module,
            ok=True,
            returncode=0,
            elapsed_s=0.0,
            workspace=self.run_root,
            detail={"note": "scaffolding reused from the loop's earlier stage"},
        )

        seeded = svapshot_seed.chia_remote_blocking(
            self.dut, seed_cfg, self.run_root, scaffold_ok, seed_text=sva_text
        )
        if not seeded.ok:
            return {
                "ok": False,
                "stage": "seed",
                "elapsed_s": seeded.elapsed_s,
                "log": seeded.stderr_tail or seeded.stdout_tail,
            }

        proved = svapshot_prove.chia_remote_blocking(
            self.dut, self.cfg, self.run_root, seeded
        )
        return {
            "ok": proved.ok,
            "stage": "prove",
            "elapsed_s": proved.elapsed_s,
            "artifacts": proved.artifacts,
            "log": proved.stderr_tail or proved.stdout_tail,
        }
