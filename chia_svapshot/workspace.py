"""Build an isolated SVApshot run root out of symlinks.

SVApshot is CWD-relative: the harness scaffolder writes ``$CWD/ft_<module>/``,
the agent drops logs beside it, and the formal scripts create ``vcf_projs/``.
Running two snapshots from the same checkout therefore has them fighting over
the same output paths.

A run root fixes that without copying a 200 MB tree: every *file* and every
read-only support directory of the SVApshot checkout is symlinked into a fresh
directory, which then becomes both ``$CWD`` and ``$SVAPSHOT_ROOT``. Tool code is
read through the links (so the run always picks up the live checkout), while
every write lands in the run root.
"""

from __future__ import annotations

import os
from typing import List

# Support directories SVApshot reads but never meaningfully writes. Keep this
# list limited to runtime dependencies; generated results stay in the run root.
LINKED_DIRS = (
    "assets",
    "baseline-designs",
    "fpv_app_scripts",
    "scripts",
    "src",
    "SVALint",
    "sva_grammar",
    "svapshot_ui",
)

# Directories that are outputs, scratch, or our own tooling. Linking these would
# put writes back into the shared checkout, which is exactly what we are
# avoiding.
# Top-level files that are noise rather than tooling: stale tool logs, the 46 MB
# user guide, and the dozens of .vc_env_*.txt droppings VC Formal leaves behind.
# A symlinked .gitignore is worse than noise — git refuses to follow one and
# warns on every status.
SKIPPED_SUFFIXES = (".pdf", ".log", ".bak", ".pyc")

SKIPPED_DIRS = (
    ".git",
    "chia",
    "chia_env",
    "chia_svapshot",
    "myenv",
    "modules",
    "packages",
    "experiments",
    "good_results",
    "vcf_projs",
    "projs",
    "verdiLog",
    "certitudeDB",
)


def build_run_root(svapshot_root: str, run_root: str) -> List[str]:
    """Create *run_root* as a symlink overlay of *svapshot_root*.

    ``modules/`` and ``packages/`` are deliberately not linked: the DUT staging
    node owns those two directories so the run sees only the staged design.

    Returns:
        The names that were linked, for logging.
    """
    svapshot_root = os.path.abspath(svapshot_root)
    run_root = os.path.abspath(run_root)
    os.makedirs(run_root, exist_ok=True)

    linked: List[str] = []
    for name in sorted(os.listdir(svapshot_root)):
        if name in SKIPPED_DIRS or name.startswith("ft_") or name.endswith("_learn_dir"):
            continue
        src = os.path.join(svapshot_root, name)
        if os.path.isdir(src):
            if name not in LINKED_DIRS:
                continue
        elif name.startswith(".") or name.endswith(SKIPPED_SUFFIXES):
            continue
        dst = os.path.join(run_root, name)
        if os.path.lexists(dst):
            continue
        os.symlink(src, dst)
        linked.append(name)

    for sub in ("vcf_projs", "projs"):
        os.makedirs(os.path.join(run_root, sub), exist_ok=True)

    return linked


def stage_env(svapshot_root: str, run_root: str, dut_root: str) -> dict:
    """Environment for a SVApshot subprocess rooted at *run_root*.

    ``SVAPSHOT_ROOT`` points at the run root rather than the checkout because
    the scaffolder bakes it into the generated ``FPV.tcl`` file lists as the location of
    ``ft_<module>/``.
    """
    env = dict(os.environ)
    env["SVAPSHOT_ROOT"] = os.path.abspath(run_root)
    env["DUT_ROOT"] = dut_root
    env["SVAPSHOT_CHECKOUT"] = os.path.abspath(svapshot_root)
    vc_home = env.get("VC_FORMAL_HOME")
    if vc_home:
        env["PATH"] = f"{vc_home}/bin:" + env.get("PATH", "")
    return env
