#!/usr/bin/env python3
"""Entry-gate CLI only (Yosys PPA + optional VCF elaborate).

The full CHIA PPA loop — elaborate, baseline PPA, SVApshot, LLM rewrite,
retain, reprove, candidate PPA — lives in ``chia_svapshot/chia_ppa_loop.py``
(``./run_ppa.sh``). This script stays useful as a cheap gate check.
"""

from __future__ import annotations

import argparse
import json
import os
import string
import sys
from typing import Any, Dict, List

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))

from nodes.ppa_yosys import measure_ppa  # noqa: E402
from nodes.vcf_elab import elaborate  # noqa: E402


def _expand(path: str, mapping: Dict[str, str]) -> str:
    return string.Template(path).safe_substitute(mapping)


def load_targets(path: str) -> Dict[str, Any]:
    with open(path, encoding='utf-8') as handle:
        return yaml.safe_load(handle)


def resolve_design(name: str, spec: Dict[str, Any], mapping: Dict[str, str]) -> Dict[str, Any]:
    rtl = [_expand(p, mapping) for p in spec.get('rtl', [])]
    packages = [_expand(p, mapping) for p in spec.get('packages', [])]
    missing = [p for p in rtl + packages if not os.path.isfile(p)]
    return {
        'name': name,
        'module': spec['snapshot_module'],
        'rtl': rtl,
        'packages': packages,
        'sources': packages + rtl,
        'missing': missing,
        'parameters': spec.get('parameters') or {},
        'goal': spec.get('goal', 'area_or_delay'),
        'max_regression': spec.get('max_regression', 0.05),
        'rewrite': spec.get('rewrite', '').strip(),
    }


def gate_one(design: Dict[str, Any], *, run_vcf: bool) -> Dict[str, Any]:
    report: Dict[str, Any] = {'design': design['name'], 'module': design['module']}
    if design['missing']:
        report['ok'] = False
        report['error'] = 'missing RTL: ' + ', '.join(design['missing'])
        return report
    ppa = measure_ppa(
        design['sources'], design['module'], parameters=design.get('parameters'),
    )
    report['yosys'] = ppa
    if run_vcf:
        report['vcf'] = elaborate(design['sources'], design['module'])
        report['ok'] = bool(ppa.get('ok') and report['vcf'].get('ok'))
    else:
        report['ok'] = bool(ppa.get('ok'))
    return report


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--targets', default=os.path.join(HERE, 'targets.yaml'))
    parser.add_argument('--design', action='append', help='Restrict to these keys')
    parser.add_argument('--vcf', action='store_true', help='Also run VC Formal elaborate')
    parser.add_argument('--out', default=os.path.join(HERE, 'artifacts', 'ppa_gate.json'))
    args = parser.parse_args(argv)

    mapping = {
        'SVAPSHOT_ROOT': os.environ.get('SVAPSHOT_ROOT') or os.path.abspath(os.path.join(HERE, '..', '..')),
        'CHIPYARD_ROOT': os.environ.get('CHIPYARD_ROOT') or os.path.expanduser('~/chipyard'),
    }
    blob = load_targets(args.targets)
    names = args.design or list(blob['designs'])
    reports = []
    for name in names:
        design = resolve_design(name, blob['designs'][name], mapping)
        print(f'== gate {name} / {design["module"]}', flush=True)
        reports.append(gate_one(design, run_vcf=args.vcf))
        print(json.dumps(reports[-1], indent=2)[:1200], flush=True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as handle:
        json.dump({'mapping': mapping, 'reports': reports}, handle, indent=2)
        handle.write('\n')
    failed = [r['design'] for r in reports if not r.get('ok')]
    if failed:
        print('gate failed:', ', '.join(failed))
        return 1
    print('gate passed:', ', '.join(r['design'] for r in reports))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
