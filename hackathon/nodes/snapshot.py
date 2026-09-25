"""Stage 0 / 2: generate, freeze, and re-prove the formal snapshot."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from typing import Optional

from nodes.emit_verilog import EmitResult


@dataclass
class FrozenContract:
    """On-disk non-vacuous contract produced by Stage 0."""

    module: str
    directory: str
    prop_file: str
    metrics_file: str
    snapshot_worthy: int
    total: int
    yield_ratio: float
    accepted: bool
    reason: str


def _svapshot_cfg(formal_tool: str = 'vcformal'):
    from chia_svapshot.state import SvapshotConfig

    root = os.environ.get('SVAPSHOT_ROOT') or os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..')
    )
    return SvapshotConfig(
        svapshot_root=root,
        interpreter=os.environ.get('SVAPSHOT_PYTHON', 'python3'),
        llm_model=os.environ.get('SVAPSHOT_LLM_MODEL', 'gemini-2.5-pro'),
        execution=os.environ.get('SVAPSHOT_EXECUTION', 'golden'),
        verbosity=os.environ.get('SVAPSHOT_VERBOSITY', 'low'),
        formal_tool=formal_tool,
        assertion_source='rtl',
        timeout_s=int(os.environ.get('SVAPSHOT_TIMEOUT_S', '12000')),
    )


def _copy_include_headers(source_root: str, dest: str) -> None:
    """Snapshot/reprove VCF uses +incdir+packages for `include "common_cells/*.svh"`."""
    packages = os.path.join(dest, 'packages')
    os.makedirs(packages, exist_ok=True)
    for folder in (
        os.path.join(source_root, 'include'),
        os.path.join(source_root, 'packages'),
    ):
        if not os.path.isdir(folder):
            continue
        for child in os.listdir(folder):
            origin = os.path.join(folder, child)
            target = os.path.join(packages, child)
            if os.path.isdir(origin):
                shutil.copytree(origin, target, dirs_exist_ok=True)
            elif child.endswith('.svh'):
                shutil.copy2(origin, target)


def _copy_sources(source_root: str, dest: str, files: list) -> None:
    os.makedirs(os.path.join(dest, 'modules'), exist_ok=True)
    os.makedirs(os.path.join(dest, 'packages'), exist_ok=True)
    for name in files:
        src = name if os.path.isabs(name) else os.path.join(source_root, name)
        if not os.path.isfile(src):
            src = os.path.join(source_root, os.path.basename(name))
        if not os.path.isfile(src):
            continue
        parent = os.path.basename(os.path.dirname(os.path.abspath(src)))
        bucket = (
            'packages'
            if (
                os.path.basename(src).endswith('_pkg.sv')
                or parent == 'packages'
                or '/includes/' in src
            )
            else 'modules'
        )
        shutil.copy2(src, os.path.join(dest, bucket, os.path.basename(src)))
    _copy_include_headers(source_root, dest)


def generate_and_freeze(
    emit: EmitResult,
    module: str,
    target_yield: float = 0.5,
    formal_tool: str = 'vcformal',
    module_type: str = 'sequential',
    instantiation_context: str = '',
    elaboration_overrides: Optional[dict] = None,
) -> FrozenContract:
    """Stage 0: SVApshot ``main.py`` writes and repairs SVA; freeze non-vacuous proofs."""
    from chia_svapshot.workspace import build_run_root

    snap_ws = os.path.join(emit.workspace, f'snap_{module}')
    os.makedirs(snap_ws, exist_ok=True)
    _copy_sources(emit.source_root, snap_ws, emit.files)
    cfg = _svapshot_cfg(formal_tool)
    build_run_root(cfg.svapshot_root, snap_ws)

    env = os.environ.copy()
    env['SVAPSHOT_ROOT'] = os.path.abspath(snap_ws)
    env['DUT_ROOT'] = os.path.join(os.path.abspath(snap_ws), 'modules')
    env['SVAPSHOT_CHECKOUT'] = cfg.svapshot_root
    env.setdefault('SVAPSHOT_MAX_ASSERTIONS', '50')
    core = os.path.join(cfg.svapshot_root, 'src', 'core')
    analysis = os.path.join(cfg.svapshot_root, 'src', 'analysis')
    env['PYTHONPATH'] = os.pathsep.join(
        [core, analysis, cfg.svapshot_root, env.get('PYTHONPATH', '')]
    )
    env['PYTHONUNBUFFERED'] = '1'

    import subprocess

    # run_single_module_flow only — do not walk main.py's child-module phase.
    runner = os.path.join(snap_ws, '_run_single.py')
    paths = [core, analysis, cfg.svapshot_root]
    with open(runner, 'w', encoding='utf-8') as handle:
        handle.write('import os, sys\n')
        handle.write(f'sys.path[:0] = {paths!r}\n')
        handle.write('import main\n')
        handle.write(
            'ok = main.run_single_module_flow(\n'
            f'    os.path.abspath("modules/{module}.sv"),\n'
            f'    {cfg.llm_model!r}, {module_type!r}, {cfg.execution!r},\n'
            f'    {cfg.verbosity!r}, ["modules"], "packages",\n'
            f'    "rtl", "initial_assertions", False, {formal_tool!r},\n'
            f'    instantiation_context={json.dumps(instantiation_context or "")},\n'
            f'    elaboration_overrides={json.dumps(elaboration_overrides or {})},\n'
            ')\n'
            'sys.exit(0 if ok else 1)\n'
        )
    proc = subprocess.run(
        [cfg.interpreter, runner], cwd=snap_ws, env=env,
        capture_output=True, text=True, timeout=cfg.timeout_s,
    )
    log_path = os.path.join(snap_ws, 'generate_and_freeze.log')
    with open(log_path, 'w', encoding='utf-8') as handle:
        handle.write(proc.stdout or '')
        handle.write('\n--- stderr ---\n')
        handle.write(proc.stderr or '')
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or '')[-800:]
        print(
            f'  [snap] run_single_module_flow exited {proc.returncode}\n{tail}',
            flush=True,
        )

    return freeze_existing(snap_ws, module, target_yield)


def freeze_existing(run_root: str, module: str, target_yield: float = 0.5) -> FrozenContract:
    """Collect metrics from an already-run snap workspace and copy the contract."""
    sva_dir = os.path.join(run_root, f'ft_{module}', 'sva')
    prop_src = os.path.join(sva_dir, f'{module}_prop.sv')
    metrics_src = os.path.join(sva_dir, 'snapshot_metrics.json')
    metrics = {}
    if os.path.isfile(metrics_src):
        with open(metrics_src, encoding='utf-8') as handle:
            metrics = json.load(handle)
    qual = metrics.get('qualification') or {}
    worthy = int(qual.get('proved_non_vacuous') or 0)
    total = int(qual.get('total_properties') or 0)
    if total == 0 and os.path.isfile(prop_src):
        import re
        with open(prop_src, encoding='utf-8', errors='replace') as handle:
            total = len(re.findall(
                r'^\s*\w+\s*:\s*assert\s+property', handle.read(), re.M))
    ratio = (worthy / total) if total else 0.0
    accepted = total > 0 and ratio >= target_yield
    reason = (
        f'{worthy}/{total} properties are snapshot-worthy '
        f'({ratio:.0%} >= {target_yield:.0%})'
        if total else 'no properties were generated'
    )

    parent = os.path.dirname(os.path.abspath(run_root))
    frozen_dir = os.path.join(parent, 'contract', 'frozen')
    os.makedirs(frozen_dir, exist_ok=True)
    prop_dest = os.path.join(frozen_dir, f'{module}_prop.sv')
    metrics_dest = os.path.join(frozen_dir, 'snapshot_metrics.json')
    if os.path.isfile(prop_src):
        shutil.copy2(prop_src, prop_dest)
    with open(metrics_dest, 'w', encoding='utf-8') as handle:
        json.dump(metrics or {'qualification': {}}, handle, indent=2)
        handle.write('\n')
    try:
        from chia_svapshot.snapshot_library import archive_snapshot

        label = 'chia_reprove' if os.path.basename(run_root).startswith('reprove_') else 'chia_generate'
        archive_snapshot(module, sva_dir if os.path.isfile(prop_src) else frozen_dir, label=label)
    except Exception as exc:
        print(f'  [snap] archive skipped: {exc}', flush=True)
    return FrozenContract(
        module=module,
        directory=frozen_dir,
        prop_file=prop_dest,
        metrics_file=metrics_dest,
        snapshot_worthy=worthy,
        total=total,
        yield_ratio=ratio,
        accepted=accepted,
        reason=reason,
    )


def reprove_frozen(
    emit: EmitResult,
    module: str,
    prop_file: str,
    formal_tool: str = 'vcformal',
) -> FrozenContract:
    """Stage 2: re-prove the frozen file. No new SVA, no LLM."""
    from chia_svapshot.workspace import build_run_root
    from chia_svapshot._stage_driver import stage_formal, stage_scaffold, _load_svapshot

    snap_ws = os.path.join(emit.workspace, f'reprove_{module}')
    os.makedirs(snap_ws, exist_ok=True)
    _copy_sources(emit.source_root, snap_ws, emit.files)
    cfg = _svapshot_cfg(formal_tool)
    build_run_root(cfg.svapshot_root, snap_ws)

    env = os.environ.copy()
    env['SVAPSHOT_ROOT'] = os.path.abspath(snap_ws)
    env['DUT_ROOT'] = os.path.join(os.path.abspath(snap_ws), 'modules')
    env['SVAPSHOT_CHECKOUT'] = cfg.svapshot_root
    os.environ.update({
        'SVAPSHOT_ROOT': env['SVAPSHOT_ROOT'],
        'DUT_ROOT': env['DUT_ROOT'],
    })

    cwd = os.getcwd()
    try:
        os.chdir(snap_ws)
        sv = _load_svapshot(cfg.svapshot_root)
        args = {
            'rtl_module': os.path.join('modules', f'{module}.sv'),
            'sources': ['modules'],
            'includes': 'packages',
            'module_type': 'sequential',
            'formal_tool': formal_tool,
            'module': module,
            'checkout': cfg.svapshot_root,
            'disable_package_detection': False,
        }
        ok, steps = stage_scaffold(sv, args)
        if not ok:
            raise RuntimeError(f'scaffold failed: {steps}')
        dest_prop = os.path.join(snap_ws, f'ft_{module}', 'sva', f'{module}_prop.sv')
        if os.path.isfile(prop_file):
            shutil.copy2(prop_file, dest_prop)
        ok, steps = stage_formal(sv, args)
        if not ok:
            print(f'  [reprove] formal reported ok=False: {steps}', flush=True)
    finally:
        os.chdir(cwd)

    return freeze_existing(snap_ws, module, target_yield=0.0)
