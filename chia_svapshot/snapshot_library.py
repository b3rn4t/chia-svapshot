"""Durable archive of generated SVApshot contracts.

Run workspaces get deleted and overwritten. Every freeze copies the property
file and metrics into ``chia_svapshot/snapshot_library/<module>/<stamp>/``.
``latest`` points at the copy with the most proved-non-vacuous properties.

Reuse::

    ./run_ppa.sh --real --design ptw \\
        --reuse-snapshot snapshot_library/ptw/latest
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.abspath(os.path.join(_HERE, "..", "snapshot_library"))

_KEEP = (
    "{module}_prop.sv",
    "{module}_prop_only_proven.sv",
    "{module}_assume.svh",
    "snapshot_metrics.json",
    "qualification.json",
    "snapshot_manifest.json",
    "snapshot.json",
    "assumptions.json",
    "{module}_bind.svh",
)


def library_root() -> str:
    return os.environ.get("SVAPSHOT_LIBRARY") or DEFAULT_ROOT


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def _count_asserts(prop_file: str) -> int:
    if not os.path.isfile(prop_file):
        return 0
    with open(prop_file, encoding="utf-8", errors="replace") as handle:
        return len(re.findall(r"^\s*\w+\s*:\s*assert\s+property", handle.read(), re.M))


def _qualify(metrics: Dict[str, Any], prop_file: str) -> Dict[str, Any]:
    qual = metrics.get("qualification") or {}
    props = metrics.get("properties") or qual.get("properties") or {}
    worthy = int(qual.get("proved_non_vacuous") or 0)
    if worthy == 0 and props:
        worthy = sum(
            1
            for status in props.values()
            if status in ("proved_non_vacuous", "proven", "proven_non_vacuous")
        )
    total = int(qual.get("total_properties") or 0) or len(props) or _count_asserts(prop_file)
    return {
        "snapshot_worthy": worthy,
        "total": total,
        "compile_failed": bool((metrics.get("summary") or {}).get("compile_failed")),
        "model": metrics.get("model") or "",
    }


def archive_snapshot(
    module: str,
    source_dir: str,
    *,
    label: str = "",
    root: Optional[str] = None,
) -> Optional[str]:
    """Copy contract files from ``source_dir`` into the library. Return dest."""
    prop = os.path.join(source_dir, f"{module}_prop.sv")
    if not os.path.isfile(prop):
        return None
    root = os.path.abspath(root or library_root())
    source_dir = os.path.abspath(source_dir)
    if source_dir == root or source_dir.startswith(root + os.sep):
        return source_dir
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    if label:
        stamp = f"{stamp}_{label}"
    dest = os.path.join(root, module, stamp)
    os.makedirs(dest, exist_ok=True)
    for pattern in _KEEP:
        name = pattern.format(module=module)
        src = os.path.join(source_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, name))
    metrics = _read_json(os.path.join(dest, "snapshot_metrics.json"))
    stats = _qualify(metrics, os.path.join(dest, f"{module}_prop.sv"))
    record = {
        "module": module,
        "archived_utc": datetime.now(timezone.utc).isoformat(),
        "source_dir": os.path.abspath(source_dir),
        "label": label,
        **stats,
    }
    with open(os.path.join(dest, "archive.json"), "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    _refresh_latest(module, root)
    _rewrite_catalog(root)
    return dest


def _refresh_latest(module: str, root: str) -> None:
    module_dir = os.path.join(root, module)
    if not os.path.isdir(module_dir):
        return
    ranked: List[tuple] = []
    for name in os.listdir(module_dir):
        if name == "latest":
            continue
        path = os.path.join(module_dir, name)
        if not os.path.isdir(path):
            continue
        info = _read_json(os.path.join(path, "archive.json"))
        ranked.append((
            int(info.get("snapshot_worthy") or 0),
            int(info.get("total") or 0),
            name,
            path,
        ))
    if not ranked:
        return
    # Prefer yield (worthy/total) so a 43/50 failing cap does not beat 43/43.
    ranked.sort(
        key=lambda row: (
            (row[0] / row[1]) if row[1] else 0.0,
            row[0],
            row[2],
        ),
        reverse=True,
    )
    latest = os.path.join(module_dir, "latest")
    if os.path.islink(latest) or os.path.exists(latest):
        os.remove(latest)
    os.symlink(ranked[0][3], latest)


def _rewrite_catalog(root: str) -> None:
    rows = []
    if os.path.isdir(root):
        for module in sorted(os.listdir(root)):
            latest = os.path.join(root, module, "latest")
            if not os.path.exists(latest):
                continue
            info = _read_json(os.path.join(latest, "archive.json"))
            info["latest"] = os.path.abspath(latest)
            rows.append(info)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "catalog.json"), "w", encoding="utf-8") as handle:
        json.dump({"modules": rows}, handle, indent=2)
        handle.write("\n")


def ingest_known(root: Optional[str] = None) -> List[str]:
    """Archive the snapshots already sitting under chia_svapshot/runs."""
    root = root or library_root()
    here = os.path.abspath(os.path.join(_HERE, ".."))
    jobs = [
        ("div_unit", os.path.join(
            here, "hackathon", "runs", "div_unit_flow",
            "snap", "snap_div_unit", "ft_div_unit", "sva",
        ), "div_unit_flow"),
        ("div_unit", os.path.join(
            here, "runs", "ppa_sargantana", "contract", "frozen",
        ), "chia_ppa_reused"),
        ("div_unit", os.path.join(
            here, "runs", "ppa_sargantana", "contract", "retained",
        ), "chia_ppa_retained"),
        ("ptw", os.path.join(
            here, "runs", "ppa_ptw", "snap", "snap_ptw", "ft_ptw", "sva",
        ), "chia_generate"),
        ("tlb", os.path.join(
            here, "runs", "ppa_tlb", "snap", "snap_tlb", "ft_tlb", "sva",
        ), "chia_generate"),
        ("mul_unit", os.path.join(
            here, "runs", "ppa_mul_unit", "snap", "snap_mul_unit",
            "ft_mul_unit", "sva",
        ), "chia_generate"),
    ]
    stored = []
    for module, path, label in jobs:
        dest = archive_snapshot(module, path, label=label, root=root)
        if dest:
            stored.append(dest)
    return stored


if __name__ == "__main__":
    for dest in ingest_known():
        print(dest)
