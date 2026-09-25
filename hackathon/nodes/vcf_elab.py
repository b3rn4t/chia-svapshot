"""VC Formal analyze+elaborate gate for one snapshot DUT (no properties)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Dict, List, Optional


def _ok(text: str, returncode: int) -> bool:
    return (
        returncode == 0
        and 'Error:[' not in text
        and 'Error-[' not in text
        and ('0 error(s)' in text or 'Elaborat' in text)
    )


def _elaborate_docker(
    sources: List[str],
    top: str,
    root: str,
    container: str,
) -> Dict[str, object]:
    vcf_home = os.environ.get('VC_FORMAL_HOME_DOCKER') or os.environ.get('VC_FORMAL_HOME')
    if not vcf_home:
        raise RuntimeError('Set VC_FORMAL_HOME')
    proc = subprocess.run(
        [
            'docker', 'exec',
            '-e', 'TERM=vt100',
            '-e', 'TERMINFO=/usr/share/terminfo',
            '-e', f'VC_FORMAL_HOME={vcf_home}',
            '-e', f'VC_STATIC_HOME={vcf_home}',
            '-e', f'SNPSLMD_LICENSE_FILE={os.environ.get("SNPSLMD_LICENSE_FILE", "")}',
            '-w', root,
            container,
            f'{vcf_home}/bin/vcf',
            '-session', 'vcst_rtdb',
            '-no_restore', '-f', 'elab.tcl', '-batch',
        ],
        capture_output=True, text=True,
    )
    text = (proc.stdout or '') + '\n' + (proc.stderr or '')
    return {
        'ok': _ok(text, proc.returncode) or (
            proc.returncode == 0 and 'Error:[' not in text and 'Error-[' not in text
        ),
        'returncode': proc.returncode,
        'log_tail': text[-2500:],
    }


def _pvalue_flags(top: str, parameters: Optional[Dict[str, object]]) -> str:
    """VCS ``-pvalue`` flags for simple numeric / based-literal overrides."""
    if not parameters:
        return ''
    try:
        from instantiation_params import format_vcs_pvalue_string

        flags = format_vcs_pvalue_string(
            top, {str(k): str(v) for k, v in parameters.items()})
    except Exception:
        flags = ''
        parts = []
        for name, value in parameters.items():
            text = str(value).strip()
            if text.isdigit():
                parts.append(f'-pvalue+{top}.{name}={text}')
        flags = ' '.join(parts)
    return flags


def elaborate(
    sources: List[str],
    top: str,
    *,
    workdir: Optional[str] = None,
    vcf_bin: str = 'vcf',
    include_dirs: Optional[List[str]] = None,
    parameters: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Return ok=True when Verdi KDB reports 0 errors.

    Set ``SVAPSHOT_VCF_DOCKER`` (container name) to run ``vcf`` via
    ``docker exec`` when the host shell cannot invoke it.
    """
    docker = os.environ.get('SVAPSHOT_VCF_DOCKER')
    # docker exec cannot chdir into host /tmp; keep the session under $HOME.
    if workdir is None and docker:
        cache = os.path.join(os.path.expanduser('~'), '.cache', 'vcf_elab')
        os.makedirs(cache, exist_ok=True)
        workdir = tempfile.mkdtemp(prefix='vcf_elab_', dir=cache)
    td_cm = tempfile.TemporaryDirectory(prefix='vcf_elab_') if workdir is None else None
    root = workdir or td_cm.name
    if workdir is not None:
        os.makedirs(root, exist_ok=True)
    try:
        tcl_path = os.path.join(root, 'elab.tcl')
        rel_sources = []
        for src in sources:
            dest = os.path.join(root, os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dest):
                try:
                    if not os.path.exists(dest):
                        os.symlink(os.path.abspath(src), dest)
                except OSError:
                    import shutil
                    shutil.copy2(src, dest)
            rel_sources.append(os.path.basename(src))
        inc_flags = []
        for include in include_dirs or []:
            dest = os.path.join(root, 'include')
            if os.path.isdir(include):
                import shutil
                os.makedirs(dest, exist_ok=True)
                for child in os.listdir(include):
                    origin = os.path.join(include, child)
                    target = os.path.join(dest, child)
                    if os.path.isdir(origin):
                        if os.path.exists(target):
                            shutil.copytree(origin, target, dirs_exist_ok=True)
                        else:
                            shutil.copytree(origin, target)
                    else:
                        shutil.copy2(origin, target)
                inc_flags.append('+incdir+include')
        vcs_inc = f'-vcs "{" ".join(inc_flags)}"' if inc_flags else ''
        pvalues = _pvalue_flags(top, parameters)
        with open(tcl_path, 'w', encoding='utf-8') as handle:
            handle.write('set_fml_appmode FPV\n')
            for src in rel_sources:
                if vcs_inc:
                    handle.write(f'analyze -format sverilog {vcs_inc} {src}\n')
                else:
                    handle.write(f'analyze -format sverilog {src}\n')
            if pvalues:
                handle.write(f'elaborate {top} -vcs {{{pvalues}}}\nexit\n')
            else:
                handle.write(f'elaborate {top}\nexit\n')
        docker = os.environ.get('SVAPSHOT_VCF_DOCKER')
        if docker:
            report = _elaborate_docker(sources, top, root, docker)
            log_path = os.path.join(root, 'vcf_elab.log')
            with open(log_path, 'w', encoding='utf-8') as log:
                log.write(report.get('log_tail') or '')
            return report
        proc = subprocess.run(
            [vcf_bin, '-session', 'vcst_rtdb', '-no_restore', '-f', 'elab.tcl', '-batch'],
            capture_output=True, text=True, cwd=root,
        )
        text = (proc.stdout or '') + '\n' + (proc.stderr or '')
        with open(os.path.join(root, 'vcf_elab.log'), 'w', encoding='utf-8') as log:
            log.write(text)
        return {
            'ok': _ok(text, proc.returncode),
            'returncode': proc.returncode,
            'log_tail': text[-2500:],
        }
    finally:
        if td_cm is not None:
            td_cm.cleanup()
