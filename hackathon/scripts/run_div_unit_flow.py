#!/usr/bin/env python3
"""Full PPA + SVApshot + retain/reprove flow for Sargantana ``div_unit``.

Graph (PPA.md):
  stage → Yosys/VCF gate → generate_and_freeze (Vertex, cap 50)
        → /0 /1 early-out rewrite → retain_after_refactor → reprove
        → Yosys candidate vs baseline → score

Does not write into the repo-root ``ft_*`` DATE cells. All artifacts land
under ``hackathon/runs/div_unit_flow/``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(REPO, 'src', 'analysis'))
sys.path.insert(0, os.path.join(REPO, 'src', 'core'))

os.environ.pop('VC_FORMAL_HOME', None)
os.environ.pop('VC_STATIC_HOME', None)

from nodes.emit_verilog import EmitResult
from nodes.ppa_yosys import better, measure_ppa
from nodes.snapshot import FrozenContract, generate_and_freeze, reprove_frozen
from retain_after_refactor import kept_assertions, retain_after_refactor

SARG = os.path.join(REPO, 'benchmarks', 'sargantana')
DIV_UNIT = os.path.join(SARG, 'rtl/datapath/rtl/exe_stage/rtl/div_unit.sv')
DIV_4BITS = os.path.join(SARG, 'rtl/datapath/rtl/exe_stage/rtl/div_4bits.sv')
DRAC = os.path.join(SARG, 'includes/drac_pkg.sv')
RISCV = os.path.join(SARG, 'includes/riscv_pkg.sv')
FPNEW = os.path.join(SARG, 'rtl/datapath/rtl/exe_stage/rtl/fpu/src/fpnew_pkg.sv')

RUN = os.path.join(HERE, 'runs', 'div_unit_flow')
BASE_SRC = os.path.join(RUN, 'baseline_rtl')
CAND_SRC = os.path.join(RUN, 'candidate_rtl')
ARTIFACTS = os.path.join(RUN, 'artifacts')

_CYCLES_LOAD = (
    "            cycles_counter[div_unit_sel_i]        <= "
    "(instruction_i.instr.op_32) ? 6'd17 : 6'd33;"
)
_EARLY_OUT = """\
            // Latency early-out: RISC-V /0 and /1 need no radix-4 steps.
            // /0 results are already muxed (quo=all-1s, rem=dividend).
            // /1 leaves the (abs) dividend in dividend_quotient_q and rem=0.
            if (div_zero_d[div_unit_sel_i]
                    || (instruction_i.instr.op_32 ? (divisor_d[31:0] == 32'd1)
                                                  : (divisor_d == 64'd1))) begin
                cycles_counter[div_unit_sel_i] <= 6'd1;
            end else begin
                cycles_counter[div_unit_sel_i] <= (instruction_i.instr.op_32) ? 6'd17 : 6'd33;
            end"""

DESIGNER_MARKER = '//====DESIGNER-ADDED-SVA====//'


def _dump(name: str, payload) -> str:
    os.makedirs(ARTIFACTS, exist_ok=True)
    path = os.path.join(ARTIFACTS, name)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2, default=lambda o: getattr(o, '__dict__', str(o)))
        handle.write('\n')
    return path


def stage_tree(dest: str, div_unit_text: str) -> None:
    modules = os.path.join(dest, 'modules')
    packages = os.path.join(dest, 'packages')
    os.makedirs(modules, exist_ok=True)
    os.makedirs(packages, exist_ok=True)
    with open(os.path.join(modules, 'div_unit.sv'), 'w', encoding='utf-8') as handle:
        handle.write(div_unit_text)
        if not div_unit_text.endswith('\n'):
            handle.write('\n')
    shutil.copy2(DIV_4BITS, os.path.join(modules, 'div_4bits.sv'))
    shutil.copy2(FPNEW, os.path.join(packages, 'fpnew_pkg.sv'))
    shutil.copy2(RISCV, os.path.join(packages, 'riscv_pkg.sv'))
    shutil.copy2(DRAC, os.path.join(packages, 'drac_pkg.sv'))


def apply_early_out(text: str) -> str:
    if _CYCLES_LOAD not in text:
        raise RuntimeError('div_unit cycles_counter load site not found; refuse to rewrite')
    if 'cycles_counter[div_unit_sel_i] <= 6\'d1' in text:
        return text
    return text.replace(_CYCLES_LOAD, _EARLY_OUT, 1)


def sources_of(tree: str) -> list:
    return [
        os.path.join(tree, 'packages', 'fpnew_pkg.sv'),
        os.path.join(tree, 'packages', 'riscv_pkg.sv'),
        os.path.join(tree, 'packages', 'drac_pkg.sv'),
        os.path.join(tree, 'modules', 'div_4bits.sv'),
        os.path.join(tree, 'modules', 'div_unit.sv'),
    ]


def vcf_elaborate_docker(srcs: list, top: str, workdir: str) -> dict:
    """VC Formal elaborate via vcformal-dev (host dash cannot run vcf)."""
    os.makedirs(workdir, exist_ok=True)
    rel = []
    for src in srcs:
        dest = os.path.join(workdir, os.path.basename(src))
        if os.path.abspath(src) != os.path.abspath(dest):
            if os.path.lexists(dest):
                os.remove(dest)
            os.symlink(os.path.abspath(src), dest)
        rel.append(os.path.basename(src))
    tcl = os.path.join(workdir, 'elab.tcl')
    with open(tcl, 'w', encoding='utf-8') as handle:
        handle.write('set_fml_appmode FPV\n')
        for name in rel:
            handle.write(f'analyze -format sverilog {name}\n')
        handle.write(f'elaborate {top}\nexit\n')
    container = os.environ.get('SVAPSHOT_VCF_DOCKER', 'vcformal-dev')
    vcf_home = os.environ.get('VC_FORMAL_HOME_DOCKER') or os.environ.get('VC_FORMAL_HOME')
    if not vcf_home:
        raise RuntimeError('Set VC_FORMAL_HOME')
    import subprocess
    proc = subprocess.run(
        [
            'docker', 'exec',
            '-e', 'TERM=vt100',
            '-e', 'TERMINFO=/usr/share/terminfo',
            '-e', f'VC_FORMAL_HOME={vcf_home}',
            '-e', f'VC_STATIC_HOME={vcf_home}',
            '-e', f'SNPSLMD_LICENSE_FILE={os.environ.get("SNPSLMD_LICENSE_FILE", "")}',
            '-w', workdir,
            container,
            f'{vcf_home}/bin/vcf',
            '-session', 'vcst_rtdb',
            '-no_restore', '-f', 'elab.tcl', '-batch',
        ],
        capture_output=True, text=True,
    )
    text = (proc.stdout or '') + '\n' + (proc.stderr or '')
    log = os.path.join(workdir, 'vcf_elab.log')
    with open(log, 'w', encoding='utf-8') as handle:
        handle.write(text)
    ok = proc.returncode == 0 and 'Error:[' not in text and 'Error-[' not in text
    return {'ok': ok, 'returncode': proc.returncode, 'log': log, 'log_tail': text[-2500:]}


def extract_assertions(prop_text: str) -> list:
    if DESIGNER_MARKER not in prop_text:
        return []
    body = prop_text.split(DESIGNER_MARKER, 1)[1]
    body = re.sub(r'\n\s*endmodule\b.*', '\n', body, flags=re.S)
    chunks = re.split(r'(?=^\w+\s*:\s*(?:assert|assume|cover)\s+property)', body, flags=re.M)
    return [chunk.strip() for chunk in chunks if 'assert property' in chunk]


def splice_assertions(prop_text: str, assertions: list) -> str:
    if DESIGNER_MARKER not in prop_text:
        raise RuntimeError('property file has no designer marker')
    head, tail = prop_text.split(DESIGNER_MARKER, 1)
    after = tail
    end = re.search(r'\nendmodule\b', after)
    suffix = after[end.start():] if end else '\nendmodule\n'
    block = '\n\n'.join(assertions)
    return head + DESIGNER_MARKER + '\n' + block + '\n' + suffix


def proved_names(metrics: dict) -> set:
    props = (metrics or {}).get('properties') or {}
    if not props:
        qual = (metrics or {}).get('qualification') or {}
        props = qual.get('properties') or {}
    names = set()
    for name, status in props.items():
        if status in ('proved_non_vacuous', 'proven', 'proven_non_vacuous'):
            names.add(name)
    return names


def emit_for(tree: str, workspace: str) -> EmitResult:
    files = [
        os.path.join(tree, 'modules', 'div_unit.sv'),
        os.path.join(tree, 'modules', 'div_4bits.sv'),
        os.path.join(tree, 'packages', 'fpnew_pkg.sv'),
        os.path.join(tree, 'packages', 'riscv_pkg.sv'),
        os.path.join(tree, 'packages', 'drac_pkg.sv'),
    ]
    return EmitResult(
        workspace=workspace,
        source_root=tree,
        modules=['div_unit'],
        files=files,
        notes=['sargantana div_unit + div_4bits + pkgs'],
    )


def main() -> int:
    started = time.time()
    os.makedirs(RUN, exist_ok=True)
    os.makedirs(ARTIFACTS, exist_ok=True)

    with open(DIV_UNIT, encoding='utf-8') as handle:
        baseline_rtl = handle.read()
    candidate_rtl = apply_early_out(baseline_rtl)
    stage_tree(BASE_SRC, baseline_rtl)
    stage_tree(CAND_SRC, candidate_rtl)
    print('[stage] baseline + /0 /1 early-out candidate', flush=True)

    print('[gate] Yosys baseline', flush=True)
    os.makedirs(os.path.join(RUN, 'yosys_base'), exist_ok=True)
    base_ppa = measure_ppa(sources_of(BASE_SRC), 'div_unit', workdir=os.path.join(RUN, 'yosys_base'))
    _dump('baseline_ppa.json', base_ppa)
    print(f"  cells={base_ppa.get('cells')} levels={base_ppa.get('levels')} ok={base_ppa.get('ok')}", flush=True)
    if not base_ppa.get('ok'):
        print(base_ppa.get('log_tail', ''), flush=True)
        return 1

    print('[gate] VCF elaborate baseline', flush=True)
    base_elab = vcf_elaborate_docker(
        sources_of(BASE_SRC), 'div_unit', os.path.join(RUN, 'vcf_elab_base'),
    )
    _dump('baseline_vcf.json', {k: v for k, v in base_elab.items() if k != 'log_tail'})
    print(f"  vcf ok={base_elab.get('ok')} rc={base_elab.get('returncode')}", flush=True)
    if not base_elab.get('ok'):
        print(base_elab.get('log_tail', ''), flush=True)
        return 1

    print('[snap] generate_and_freeze (Vertex, cap 50)', flush=True)
    contract = generate_and_freeze(
        emit_for(BASE_SRC, os.path.join(RUN, 'snap')),
        'div_unit',
        target_yield=0.0,
        formal_tool='vcformal',
    )
    _dump('frozen_contract.json', contract)
    print(
        f"  [snap] {contract.snapshot_worthy}/{contract.total} "
        f"worthy ({contract.yield_ratio:.0%}) accepted={contract.accepted} "
        f"— {contract.reason}",
        flush=True,
    )

    with open(contract.prop_file, encoding='utf-8', errors='replace') as handle:
        prop_text = handle.read()
    assertions = extract_assertions(prop_text)
    with open(contract.metrics_file, encoding='utf-8') as handle:
        metrics = json.load(handle)
    worthy = proved_names(metrics)
    if worthy:
        sys.path.insert(0, os.path.join(REPO, 'src', 'analysis'))
        from coi import assertion_name as _an
        assertions = [
            row for row in assertions
            if (_an(row) or '') in worthy
        ]
    print(f'[retain] {len(assertions)} frozen assertions', flush=True)

    decisions = retain_after_refactor(
        assertions,
        candidate_rtl,
        module_name='div_unit',
        baseline_rtl=baseline_rtl,
        mode='exists',
    )
    kept = kept_assertions(decisions)
    _dump('retain.json', {
        'mode': 'exists',
        'input': len(assertions),
        'kept': len(kept),
        'dropped': [row.to_dict() for row in decisions if not row.kept],
        'kept_names': [row.name for row in decisions if row.kept],
    })
    print(f'  kept {len(kept)}/{len(assertions)}', flush=True)

    kept_prop = os.path.join(RUN, 'contract', 'retained', 'div_unit_prop.sv')
    os.makedirs(os.path.dirname(kept_prop), exist_ok=True)
    with open(kept_prop, 'w', encoding='utf-8') as handle:
        handle.write(splice_assertions(prop_text, kept))

    print('[compat] reprove retained SVA on early-out RTL', flush=True)
    reproved = FrozenContract(
        module='div_unit', directory='', prop_file='', metrics_file='',
        snapshot_worthy=0, total=0, yield_ratio=0.0, accepted=False, reason='skipped',
    )
    if kept:
        reproved = reprove_frozen(
            emit_for(CAND_SRC, os.path.join(RUN, 'reprove')),
            'div_unit',
            kept_prop,
            formal_tool='vcformal',
        )
    _dump('reprove_contract.json', reproved)
    print(
        f"  [reprove] {reproved.snapshot_worthy}/{reproved.total} "
        f"worthy — {reproved.reason}",
        flush=True,
    )

    print('[score] Yosys candidate', flush=True)
    os.makedirs(os.path.join(RUN, 'yosys_cand'), exist_ok=True)
    cand_ppa = measure_ppa(sources_of(CAND_SRC), 'div_unit', workdir=os.path.join(RUN, 'yosys_cand'))
    _dump('candidate_ppa.json', cand_ppa)
    score = better(base_ppa, cand_ppa, goal='area_or_delay', max_regression=0.05)
    summary = {
        'elapsed_s': round(time.time() - started, 2),
        'rewrite': '/0 and /1 issue-time cycles_counter<=1',
        'baseline_ppa': {k: base_ppa.get(k) for k in ('ok', 'cells', 'wires', 'levels')},
        'candidate_ppa': {k: cand_ppa.get(k) for k in ('ok', 'cells', 'wires', 'levels')},
        'score': score,
        'snapshot': contract.__dict__,
        'retain_kept': len(kept),
        'retain_input': len(assertions),
        'reprove': reproved.__dict__,
        'compatibility_ok': bool(kept) and reproved.snapshot_worthy > 0,
    }
    out = _dump('summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)
    print(f'== done {summary["elapsed_s"]}s == {out}', flush=True)
    return 0 if base_ppa.get('ok') and cand_ppa.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
