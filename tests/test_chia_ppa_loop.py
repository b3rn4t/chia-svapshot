#!/usr/bin/env python3
"""Helpers and decisions for the CHIA PPA loop (no Ray)."""

from __future__ import annotations

import os
import unittest

import _path_setup  # noqa: F401, E402

import sys
from pathlib import Path

CHIA = Path(__file__).resolve().parents[1] / 'chia_svapshot'
if str(CHIA) not in sys.path:
    sys.path.insert(0, str(CHIA))
HACK = CHIA / 'hackathon'
if str(HACK) not in sys.path:
    sys.path.insert(0, str(HACK))

from chia_svapshot.ppa_nodes import _rewrite_prompt  # noqa: E402
from chia_svapshot.ppa_rtl import (  # noqa: E402
    DESIGNER_MARKER,
    canonical_rewrite_mode,
    classify_mode_outcome,
    extract_assertions,
    extract_ports,
    extract_sv_module,
    headline_for,
    lec_headline,
    port_diff,
    ports_equal,
    reprove_passed,
    splice_assertions,
)
from chia_svapshot.ppa_state import PortSig  # noqa: E402


RTL = """
module div_unit
    import drac_pkg::*;
(
    input  logic        clk_i,
    input  logic        rstn_i,
    input  logic [63:0] divisor_i,
    output logic [63:0] quo_o
);
    assign quo_o = divisor_i;
endmodule
"""

RTL_BAD_PORT = """
module div_unit (
    input  logic        clk_i,
    input  logic        rstn_i,
    input  logic [31:0] divisor_i,
    output logic [63:0] extra_o
);
endmodule
"""

PROP = f"""
module div_unit_prop;
{DESIGNER_MARKER}
p_div0: assert property (@(posedge clk_i) div_zero |-> quo_o == '1);

p_div1: assert property (@(posedge clk_i) divisor_i == 64'd1 |-> rem_o == '0);
endmodule
"""


class PortFreezeTest(unittest.TestCase):
    def test_extracts_ansi_ports(self):
        ports = extract_ports(RTL, 'div_unit')
        self.assertEqual([p.name for p in ports], ['clk_i', 'rstn_i', 'divisor_i', 'quo_o'])
        self.assertEqual(ports[2].packed, '[63:0]')
        self.assertEqual(ports[3].direction, 'output')

    def test_rejects_width_and_name_change(self):
        left = extract_ports(RTL, 'div_unit')
        right = extract_ports(RTL_BAD_PORT, 'div_unit')
        self.assertFalse(ports_equal(left, right))
        self.assertIn('port names/order', port_diff(left, right))


class AssertionSpliceTest(unittest.TestCase):
    def test_extract_and_splice_roundtrip(self):
        rows = extract_assertions(PROP)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].startswith('p_div0'))
        spliced = splice_assertions(PROP, rows[:1])
        self.assertIn('p_div0', spliced)
        self.assertNotIn('p_div1', spliced)
        self.assertIn(DESIGNER_MARKER, spliced)
        self.assertIn('endmodule', spliced)

    def test_splice_keeps_environment_assumes(self):
        text = (
            f"module div_unit_prop;\n{DESIGNER_MARKER}\n"
            "p_div0: assert property (1'b1);\n"
            "\n// ---- SVApshot environment assumptions ----\n"
            "m_hold: assume property (in_valid_i);\n"
            "endmodule\n"
        )
        spliced = splice_assertions(text, [
            "p_div0: assert property (1'b1);",
        ])
        self.assertIn('m_hold: assume property', spliced)
        self.assertIn('SVApshot environment assumptions', spliced)

    def test_splice_without_designer_marker(self):
        text = (
            "module ptw_prop;\n"
            "a_ok: assert property (1'b1);\n"
            "a_bad: assert property (1'b0);\n"
            "endmodule\n"
        )
        spliced = splice_assertions(text, [
            "a_ok: assert property (1'b1);",
        ])
        self.assertIn('a_ok', spliced)
        self.assertNotIn('a_bad', spliced)
        self.assertIn('endmodule', spliced)


class ReproveDecisionTest(unittest.TestCase):
    def test_pass_when_all_kept_prove(self):
        ok, reason = reprove_passed(
            kept=4, snapshot_worthy=4, failed=0, ports_ok=True,
        )
        self.assertTrue(ok)
        self.assertIn('4', reason)

    def test_fail_returns_to_implementation(self):
        ok, reason = reprove_passed(
            kept=4, snapshot_worthy=3, failed=1, ports_ok=True,
        )
        self.assertFalse(ok)
        self.assertIn('failed', reason)

    def test_empty_retain_and_port_break_fail(self):
        ok, _ = reprove_passed(kept=0, snapshot_worthy=0, failed=0, ports_ok=True)
        self.assertFalse(ok)
        ok, reason = reprove_passed(
            kept=2, snapshot_worthy=2, failed=0, ports_ok=False,
        )
        self.assertFalse(ok)
        self.assertIn('port', reason)


class SnapshotLibraryTest(unittest.TestCase):
    def test_archives_and_points_latest_at_worthiest(self):
        import json
        import tempfile
        from pathlib import Path

        from chia_svapshot.snapshot_library import archive_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'lib'
            weak = Path(tmp) / 'weak'
            strong = Path(tmp) / 'strong'
            for folder, worthy in ((weak, 1), (strong, 4)):
                folder.mkdir()
                (folder / 'toy_prop.sv').write_text(
                    'p0: assert property (1);\n'
                )
                (folder / 'snapshot_metrics.json').write_text(json.dumps({
                    'qualification': {
                        'proved_non_vacuous': worthy,
                        'total_properties': 5,
                    },
                }))
            archive_snapshot('toy', str(weak), label='weak', root=str(root))
            dest = archive_snapshot(
                'toy', str(strong), label='strong', root=str(root),
            )
            latest = root / 'toy' / 'latest'
            self.assertTrue(latest.exists())
            self.assertEqual(Path(dest).resolve(), latest.resolve())
            info = json.loads((latest / 'archive.json').read_text())
            self.assertEqual(info['snapshot_worthy'], 4)


class ModeOutcomeTest(unittest.TestCase):
    def test_headline_cases(self):
        win = classify_mode_outcome(
            rewrite_ok=True, snapshot_ok=True, ppa_win=True,
        )
        fail = classify_mode_outcome(
            rewrite_ok=True, snapshot_ok=False, ppa_win=True,
        )
        self.assertEqual(win, 'ppa_win_snapshot_pass')
        self.assertEqual(fail, 'snapshot_fail')
        self.assertIn('better PPA', headline_for(win))
        self.assertIn('failing snapshot', headline_for(fail))
        self.assertEqual(
            classify_mode_outcome(
                rewrite_ok=True, snapshot_ok=True, ppa_win=False,
            ),
            'snapshot_pass_no_ppa',
        )
        self.assertEqual(headline_for('snapshot_pass_no_ppa'), '')
        self.assertEqual(
            lec_headline(
                snapshot_ok=True, equivalent=False,
                inconclusive=False, skipped=False,
            ),
            'snapshot-clean rewrite failed LEC',
        )
        self.assertEqual(
            lec_headline(
                snapshot_ok=True, equivalent=True,
                inconclusive=False, skipped=False,
            ),
            '',
        )
        self.assertEqual(
            lec_headline(
                snapshot_ok=False, equivalent=False,
                inconclusive=False, skipped=True,
            ),
            '',
        )

    def test_targets_declare_three_modes(self):
        from chia_svapshot.ppa_nodes import load_target

        for name in ('sargantana', 'mul_unit', 'ptw', 'tlb'):
            spec = load_target(name)
            modes = spec['rewrites']
            self.assertIn('optimise', modes)
            self.assertIn('expand', modes)
            self.assertIn('trim', modes)
            self.assertTrue(modes['optimise'])
            self.assertTrue(modes['expand'])
            self.assertTrue(modes['trim'])


class ExtractModuleTest(unittest.TestCase):
    def test_fenced_module(self):
        reply = "Sure.\n```systemverilog\n" + RTL + "```\n"
        text = extract_sv_module(reply, 'div_unit')
        self.assertTrue(text.startswith('module div_unit'))
        self.assertTrue(text.rstrip().endswith('endmodule'))


class RewritePromptTest(unittest.TestCase):
    def test_every_mode_forbids_port_list_changes(self):
        ports = [
            PortSig(direction='input', name='clk_i'),
            PortSig(direction='output', name='resp_o', packed='[63:0]'),
        ]
        for mode in ('optimise', 'expand', 'trim', 'refactor'):
            prompt = _rewrite_prompt(
                'ptw', 'module ptw; endmodule\n', 'goal text', ports, '', mode,
            )
            self.assertIn('Do not modify the port list', prompt)
            self.assertIn('snapshot', prompt.lower())
            self.assertIn('clk_i', prompt)
            self.assertIn('resp_o', prompt)
            self.assertIn('endmodule', prompt)
        opt = _rewrite_prompt(
            'ptw', 'module ptw; endmodule\n', 'goal text', ports, '', 'optimise',
        )
        self.assertIn('Keep interface, keep functionality and optimise RTL for better PPA', opt)
        self.assertIn('Scope: top-level', opt)
        self.assertEqual(canonical_rewrite_mode('refactor'), 'optimise')

    def test_fpnew_top_is_top_scope_with_children(self):
        from chia_svapshot.ppa_nodes import load_target

        spec = load_target('fpnew_top')
        self.assertEqual(spec['module'], 'fpnew_top')
        self.assertEqual(spec['rewrite_scope'], 'top')
        self.assertTrue(any(path.endswith('fpnew_top.sv') for path in spec['rtl']))
        self.assertTrue(any('fpnew_opgroup_block.sv' in path for path in spec['rtl']))
        self.assertTrue(any(path.endswith('ct_vfdsu_top.v') for path in spec['rtl']))
        self.assertTrue(any(path.endswith('gated_clk_cell.v') for path in spec['rtl']))
        self.assertFalse(any('blackboxes/ct_vfdsu_top' in path for path in spec['rtl']))
        self.assertTrue(any(path.endswith('defs_div_sqrt_mvp.sv') for path in spec['packages']))
        self.assertTrue(spec['include_dirs'])
        self.assertIn('Top-level only', spec['rewrites']['optimise'])
        match = spec.get('match_instance') or {}
        self.assertTrue(match.get('parent', '').endswith('fpu_drac_wrapper.sv'))
        self.assertEqual(match.get('instance'), 'i_fpuv_top')
        self.assertTrue(any(path.endswith('drac_pkg.sv') for path in match.get('packages') or []))

    def test_silence_elab_system_tasks(self):
        from nodes.ppa_yosys import _silence_elab_system_tasks
        import tempfile

        text = (
            'if (THMULTI) begin\n'
            '  $warning("no FP8 \\\nplease use PULP");\n'
            '  assert (ok) else $fatal(1, "bad width");\n'
            'end\n'
        )
        with tempfile.NamedTemporaryFile('w', suffix='.sv', delete=False) as handle:
            handle.write(text)
            path = handle.name
        try:
            _silence_elab_system_tasks(path)
            with open(path, encoding='utf-8') as handle:
                patched = handle.read()
        finally:
            os.unlink(path)
        self.assertNotIn('$warning(', patched)
        self.assertNotIn('$fatal(', patched)
        self.assertIn('else ;', patched)

    def test_copy_sources_keeps_common_cells_headers(self):
        import tempfile
        from nodes.snapshot import _copy_sources

        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, 'src')
            dest = os.path.join(root, 'dest')
            os.makedirs(os.path.join(source, 'include', 'common_cells'))
            os.makedirs(os.path.join(source, 'packages'))
            os.makedirs(os.path.join(source, 'modules'))
            with open(os.path.join(source, 'include', 'common_cells', 'registers.svh'), 'w') as handle:
                handle.write('`define FFLARNC(a,b,c,d,e,f,g)\n')
            with open(os.path.join(source, 'packages', 'defs_div_sqrt_mvp.sv'), 'w') as handle:
                handle.write('package defs_div_sqrt_mvp;\nendpackage\n')
            with open(os.path.join(source, 'modules', 'fpnew_top.sv'), 'w') as handle:
                handle.write('module fpnew_top; endmodule\n')
            _copy_sources(
                source,
                dest,
                [
                    os.path.join(source, 'packages', 'defs_div_sqrt_mvp.sv'),
                    os.path.join(source, 'modules', 'fpnew_top.sv'),
                ],
            )
            self.assertTrue(
                os.path.isfile(os.path.join(dest, 'packages', 'common_cells', 'registers.svh'))
            )
            self.assertTrue(os.path.isfile(os.path.join(dest, 'packages', 'defs_div_sqrt_mvp.sv')))


class LecCheckTest(unittest.TestCase):
    GOLD = (
        "module toy_lec(input logic a, output logic y);\n"
        "  assign y = a;\n"
        "endmodule\n"
    )
    GATE_OK = GOLD
    GATE_BAD = (
        "module toy_lec(input logic a, output logic y);\n"
        "  assign y = ~a;\n"
        "endmodule\n"
    )

    def test_parse_eqy_verdicts(self):
        from nodes.ppa_lec import parse_eqy_log

        passed = parse_eqy_log("EQY 00:00:00 [job] Successfully proved designs equivalent\n")
        self.assertTrue(passed["equivalent"])
        self.assertFalse(passed["inconclusive"])
        failed = parse_eqy_log(
            "EQY 00:00:00 [job] Failed to prove equivalence for 2/5 partitions:\n"
            "Failed to prove equivalence of partition toy.y\n"
        )
        self.assertFalse(failed["equivalent"])
        self.assertEqual(failed["partitions_failed"], 2)
        self.assertEqual(failed["failed_partitions"], ["toy.y"])
        open_ = parse_eqy_log("EQY still running partitions\n")
        self.assertTrue(open_["inconclusive"])

    def test_timeout_bytes_decode(self):
        from nodes.ppa_lec import _as_text

        self.assertEqual(_as_text(None), "")
        self.assertEqual(_as_text("ok"), "ok")
        self.assertEqual(_as_text(b"EQY timed out"), "EQY timed out")
        self.assertEqual(_as_text(b"\n") + "x", "\nx")

    def test_identity_passes_and_invert_fails(self):
        import shutil
        import tempfile

        from nodes.ppa_lec import run_lec

        if not shutil.which("eqy") and not os.path.isfile(
            str(HACK / "toolchains" / "oss-cad-suite" / "bin" / "eqy")
        ):
            self.skipTest("eqy not installed")
        with tempfile.TemporaryDirectory() as tmp:
            gold = os.path.join(tmp, "gold.sv")
            gate_ok = os.path.join(tmp, "gate_ok.sv")
            gate_bad = os.path.join(tmp, "gate_bad.sv")
            with open(gold, "w") as handle:
                handle.write(self.GOLD)
            with open(gate_ok, "w") as handle:
                handle.write(self.GATE_OK)
            with open(gate_bad, "w") as handle:
                handle.write(self.GATE_BAD)
            same = run_lec(
                [gold], [gate_ok], "toy_lec",
                workdir=os.path.join(tmp, "same"),
                timeout_s=60, depth=2,
            )
            self.assertTrue(same["equivalent"], same.get("reason"))
            self.assertFalse(same["inconclusive"])
            diff = run_lec(
                [gold], [gate_bad], "toy_lec",
                workdir=os.path.join(tmp, "diff"),
                timeout_s=60, depth=2,
            )
            self.assertFalse(diff["equivalent"], diff.get("reason"))
            self.assertFalse(diff["inconclusive"])


if __name__ == '__main__':
    unittest.main()
