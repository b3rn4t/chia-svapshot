"""Stage a module out of a large RTL tree into a self-contained SVApshot DUT.

SVApshot expects a flat ``$DUT_ROOT`` of RTL plus an ``--includes`` directory of
packages, whereas Sargantana is a deep tree whose modules pull types out of
``drac_pkg``/``riscv_pkg``. The node here bridges the two: it resolves the
transitive ``import <pkg>::*`` closure of one module and copies just that
closure into a fresh workspace.

Keeping this as its own CHIA node (rather than a helper called inline by the
loop) means the staging step is scheduled, profiled, cached and retried like any
other node in the graph.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Dict, List, Optional, Tuple

from chia.base.ChiaFunction import ChiaFunction

from .state import DutSpec

_IMPORT_RE = re.compile(r"^\s*import\s+(\w+)\s*::", re.MULTILINE)
# A package can also be reached without importing it, by naming a symbol
# directly: `fpnew_pkg::status_t fp_status;`. drac_pkg does exactly that, and a
# closure built from import statements alone leaves the file list incomplete —
# VC Formal then fails elaboration with a scope-resolution error.
_SCOPE_RE = re.compile(r"\b(\w+)\s*::")
_PACKAGE_RE = re.compile(r"^\s*package\s+(\w+)\s*;", re.MULTILINE)
_MODULE_RE = re.compile(r"^\s*module\s+(\w+)\b", re.MULTILINE)
# A clocked module is one that actually has a clock port; SVApshot needs to know
# because sequential and combinational DUTs get different property templates.
_CLOCK_RE = re.compile(r"\b(clk|clk_i|clock|clk_ck)\b\s*[,)]")

_RTL_SUFFIXES = (".sv", ".v", ".svh")


def _read(path: str) -> str:
    with open(path, "r", errors="replace") as handle:
        return handle.read()


def _index_tree(root: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Map every ``package``/``module`` name in *root* to the file declaring it."""
    packages: Dict[str, str] = {}
    modules: Dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "build", "sim")]
        for name in filenames:
            if not name.endswith(_RTL_SUFFIXES):
                continue
            path = os.path.join(dirpath, name)
            try:
                text = _read(path)
            except OSError:
                continue
            for pkg in _PACKAGE_RE.findall(text):
                packages.setdefault(pkg, path)
            for mod in _MODULE_RE.findall(text):
                modules.setdefault(mod, path)
    return packages, modules


def _referenced_packages(text: str, packages: Dict[str, str]) -> List[str]:
    """Package names *text* depends on, whether imported or scope-resolved.

    Filtering against the package index is what makes the loose ``ident::``
    pattern safe: class scopes, enum qualifications and ``$unit::`` all fail the
    lookup and are dropped.
    """
    names: List[str] = []
    for name in _IMPORT_RE.findall(text) + _SCOPE_RE.findall(text):
        if name in packages and name not in names:
            names.append(name)
    return names


def _package_closure(entry_text: str, packages: Dict[str, str]) -> List[str]:
    """Return package files needed by *entry_text*, dependencies first.

    Compilation order matters to every SystemVerilog front end, so the result is
    a post-order DFS: a package is emitted only after everything it references.
    """
    ordered: List[str] = []
    seen: set = set()

    def visit(pkg: str) -> None:
        if pkg in seen:
            return
        seen.add(pkg)
        path = packages[pkg]
        for dep in _referenced_packages(_read(path), packages):
            if dep != pkg:
                visit(dep)
        ordered.append(path)

    for pkg in _referenced_packages(entry_text, packages):
        visit(pkg)
    return ordered


@ChiaFunction(resources={"svapshot_cpu": 1})
def stage_rtl_dut(
    module: str,
    source_root: str,
    workspace: str,
    module_type: Optional[str] = None,
) -> DutSpec:
    """Copy *module* and its package closure out of *source_root* into a DUT dir.

    Args:
        module: Name of the RTL module to snapshot, e.g. ``div_4bits``.
        source_root: Root of the design tree to search, e.g. the Sargantana checkout.
        workspace: Directory to build the DUT layout in. Recreated from scratch.
        module_type: ``sequential`` or ``combinational``. Inferred from the
            presence of a clock port when omitted.

    Returns:
        A :class:`DutSpec` pointing at the staged copy.
    """
    packages, modules = _index_tree(source_root)
    if module not in modules:
        raise FileNotFoundError(
            f"module {module!r} not found under {source_root!r} "
            f"({len(modules)} modules indexed)"
        )

    rtl_src = modules[module]
    text = _read(rtl_src)

    modules_dir = os.path.join(workspace, "modules")
    packages_dir = os.path.join(workspace, "packages")
    os.makedirs(modules_dir, exist_ok=True)
    os.makedirs(packages_dir, exist_ok=True)

    rtl_relpath = os.path.join("modules", f"{module}.sv")
    shutil.copyfile(rtl_src, os.path.join(workspace, rtl_relpath))

    staged_packages: List[str] = []
    for pkg_path in _package_closure(text, packages):
        dest = os.path.join(packages_dir, os.path.basename(pkg_path))
        shutil.copyfile(pkg_path, dest)
        staged_packages.append(os.path.basename(dest))

    if module_type is None:
        header = text[: text.find(");") + 2] if ");" in text else text
        module_type = "sequential" if _CLOCK_RE.search(header) else "combinational"

    return DutSpec(
        module=module,
        rtl_relpath=rtl_relpath,
        workspace=os.path.abspath(workspace),
        sources=["modules"],
        includes="packages",
        module_type=module_type,
        packages=staged_packages,
        origin=rtl_src,
        line_count=text.count("\n") + 1,
    )
