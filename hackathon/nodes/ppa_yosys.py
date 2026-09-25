"""Yosys area + combinational-depth measurement for one snapshot DUT."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple


_CELL_RE = re.compile(r'^\s+(?:Number of cells:\s+)?(\d+)\s+cells\s*$', re.M)
_WIRE_RE = re.compile(r'^\s+(?:Number of wires:\s+)?(\d+)\s+wires\s*$', re.M)
_LTP_RE = re.compile(r'^\s+(\d+):\s+', re.M)
_AREA_RE = re.compile(
    r'Chip area for (?:top )?module .*?:\s*([0-9]+(?:\.[0-9]+)?)',
)
_LIBCELL_RE = re.compile(r'^\s+(\d+)\s+\S+\s+\S+__', re.M)
_LTP_LEN_RE = re.compile(r'Longest topological path .*?\(length=(\d+)\)')


def _last_int(pattern: re.Pattern, text: str) -> Optional[int]:
    matches = pattern.findall(text)
    return int(matches[-1]) if matches else None


def _yosys_bin() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(here, 'toolchains', 'oss-cad-suite', 'bin', 'yosys')
    return bundled if os.path.isfile(bundled) else 'yosys'


def _liberty_path() -> Optional[str]:
    """Sky130 HD typical corner, the open PDK CHIA/Chipyard Hammer uses.

    ``hammer.technology.sky130`` plus ``hammer.synthesis.yosys`` maps onto
    ``sky130_fd_sc_hd`` at tt, 25 °C, 1.8 V. ``SVAPSHOT_LIBERTY`` overrides
    the file. ``SVAPSHOT_YOSYS_GENERIC=1`` keeps the unmapped cell/LTP judge.
    """
    if os.environ.get("SVAPSHOT_YOSYS_GENERIC", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        return None
    override = os.environ.get("SVAPSHOT_LIBERTY", "").strip()
    if override:
        return override if os.path.isfile(override) else None
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default = os.path.join(
        here, "pdk", "sky130", "sky130_fd_sc_hd__tt_025C_1v80.lib",
    )
    return default if os.path.isfile(default) else None


def _synth_script(read_cmd: str, top: str) -> str:
    liberty = _liberty_path()
    if liberty:
        # Same open flow as Chipyard Hammer on Sky130: Yosys synth, then
        # dfflibmap + abc against the HD typical Liberty. No commercial tool.
        lib = liberty.replace(" ", r"\ ")
        body = [
            read_cmd,
            "proc; opt",
            f"synth -top {top}",
            f"dfflibmap -liberty {lib}",
            f"abc -liberty {lib}",
            "clean",
            f"stat -liberty {lib}",
            "ltp -noff",
        ]
        return "\n".join(body) + "\n"
    # Generic cells when the PDK file is absent. ``-noabc`` is the historical
    # judge; ``SVAPSHOT_YOSYS_ABC=1`` turns abc back on.
    abc = os.environ.get("SVAPSHOT_YOSYS_ABC", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    synth = f"synth -top {top}" if abc else f"synth -top {top} -noabc"
    return "\n".join([read_cmd, "proc; opt", synth, "stat", "ltp -noff"]) + "\n"


def _run_yosys(script: str, cwd: Optional[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_yosys_bin(), '-Q', '-T', '-p', script],
        capture_output=True, text=True, cwd=cwd,
    )


# yosys-slang aborts on executed $warning/$fatal ElabSystemTask nodes.
_ELAB_SYS_TASK = re.compile(
    r'\$(?:warning|error|fatal|info)\s*\((?:[^();]|\([^()]*\))*\)\s*;',
    re.S,
)


def _silence_elab_system_tasks(path: str) -> None:
    """Replace elab system tasks with ';' so ``else $fatal`` stays valid SV."""
    if not path.endswith(('.sv', '.v', '.svh')):
        return
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            text = handle.read()
    except OSError:
        return
    if '$warning' not in text and '$fatal' not in text and '$error' not in text and '$info' not in text:
        return
    patched = _ELAB_SYS_TASK.sub(';', text)
    if patched != text:
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(patched)


def _prep_yosys_tree(
    sources: List[str],
    include_dirs: Optional[List[str]],
    workdir: str,
) -> Tuple[List[str], List[str]]:
    """Copy RTL/includes into workdir and silence elab tasks (VCF tree stays clean)."""
    src_dir = os.path.join(workdir, 'rtl')
    inc_dir = os.path.join(workdir, 'include')
    os.makedirs(src_dir, exist_ok=True)
    copied: List[str] = []
    for src in sources:
        dest = os.path.join(src_dir, os.path.basename(src))
        shutil.copy2(src, dest)
        _silence_elab_system_tasks(dest)
        copied.append(dest)
    local_includes: List[str] = []
    if include_dirs:
        os.makedirs(inc_dir, exist_ok=True)
        for src in include_dirs:
            if not os.path.isdir(src):
                continue
            for child in os.listdir(src):
                origin = os.path.join(src, child)
                target = os.path.join(inc_dir, child)
                if os.path.isdir(origin):
                    shutil.copytree(origin, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(origin, target)
        for root, _dirs, files in os.walk(inc_dir):
            for name in files:
                _silence_elab_system_tasks(os.path.join(root, name))
        local_includes.append(inc_dir)
    return copied, local_includes


def measure_ppa(
    sources: List[str],
    top: str,
    *,
    workdir: Optional[str] = None,
    parameters: Optional[Dict[str, object]] = None,
    include_dirs: Optional[List[str]] = None,
) -> Dict[str, object]:
    """Synth ``top`` and return cell/wire counts plus LTP depth."""
    td = None
    root = workdir
    if root is None:
        td = tempfile.TemporaryDirectory(prefix='yosys_ppa_')
        root = td.name
    os.makedirs(root, exist_ok=True)
    sources, include_dirs = _prep_yosys_tree(sources, include_dirs, root)
    quoted = ' '.join(sources)
    gens = ''
    if parameters:
        gens = ' ' + ' '.join(f'-G {key}={value}' for key, value in parameters.items())
    includes = ''
    if include_dirs:
        includes = ''.join(f' -I {path}' for path in include_dirs if path)
    slang = (
        f'plugin -i slang; read_slang --single-unit --ignore-initial '
        f'--empty-blackboxes --top {top}{gens}{includes} {quoted}'
    )
    verilog = f'read_verilog -sv {quoted}; hierarchy -check -top {top}'
    try:
        proc = _run_yosys(_synth_script(slang, top), root)
        frontend = 'slang'
        slang_text = (proc.stdout or '') + '\n' + (proc.stderr or '')
        if proc.returncode != 0:
            fallback = _run_yosys(_synth_script(verilog, top), root)
            frontend = 'read_verilog'
            text = (
                '===== slang (failed) =====\n' + slang_text
                + '\n===== read_verilog fallback =====\n'
                + (fallback.stdout or '') + '\n' + (fallback.stderr or '')
            )
            proc = fallback
        else:
            text = slang_text
        with open(os.path.join(root, 'yosys_ppa.log'), 'w', encoding='utf-8') as handle:
            handle.write(text)
        cells = _last_int(_CELL_RE, text)
        wires = _last_int(_WIRE_RE, text)
        length = _LTP_LEN_RE.findall(text)
        levels = int(length[-1]) if length else _last_int(_LTP_RE, text)
        if levels == 0:
            levels = None
        area_match = _AREA_RE.findall(text)
        area = float(area_match[-1]) if area_match else None
        mapped = [int(n) for n in _LIBCELL_RE.findall(text)]
        if area is not None and mapped:
            cells = sum(mapped)
        liberty = _liberty_path()
        return {
            'ok': proc.returncode == 0 and cells is not None,
            'returncode': proc.returncode,
            'frontend': frontend,
            'cells': cells,
            'wires': wires,
            'levels': levels,
            'area_um2': area,
            'liberty': liberty or "",
            'pdk': "sky130_fd_sc_hd tt_025C_1v80" if liberty else "generic",
            'log_tail': text[-2000:],
        }
    finally:
        if td is not None:
            td.cleanup()


def better(
    baseline: Dict[str, object],
    candidate: Dict[str, object],
    *,
    goal: str = 'area_or_delay',
    max_regression: float = 0.05,
) -> Dict[str, object]:
    """Decide whether ``candidate`` is an acceptable PPA win."""
    def ratio(key: str) -> Optional[float]:
        b, c = baseline.get(key), candidate.get(key)
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)) or b <= 0:
            return None
        return (c - b) / b

    area_key = (
        'area_um2'
        if baseline.get('area_um2') and candidate.get('area_um2')
        else 'cells'
    )
    area = ratio(area_key)
    delay = ratio('levels')
    area_ok = area is not None and area <= max_regression
    delay_ok = delay is None or delay <= max_regression
    improved_area = area is not None and area < -1e-9
    improved_delay = delay is not None and delay < -1e-9
    if goal == 'delay':
        win = bool(improved_delay and area_ok)
    elif goal == 'area':
        win = bool(improved_area and delay_ok)
    else:
        win = bool((improved_area or improved_delay) and area_ok and delay_ok)
    return {
        'win': win,
        'area_delta': area,
        'delay_delta': delay,
        'improved_area': improved_area,
        'improved_delay': improved_delay,
    }


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Yosys PPA for one DUT')
    parser.add_argument('--top', required=True)
    parser.add_argument('sources', nargs='+')
    args = parser.parse_args()
    print(json.dumps(measure_ppa(args.sources, args.top), indent=2))
