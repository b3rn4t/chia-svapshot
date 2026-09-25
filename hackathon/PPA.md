# Hackathon shift: PPA on three snapshot DUTs

The RVV-on-MegaBOOM attach is paused. Vector correctness was never checkable
at `DecodeUnit`, and the isolation net could not certify a VPU that does not
exist yet. This loop instead takes three designs we already have, freezes a
small formal snapshot on one submodule each, applies a *modest* RTL rewrite,
and lets open-source Yosys certify a PPA delta. VC Formal elaborate is the
other half of the entry gate.

Success is **the same CHIA graph completing on all three designs**, not a
large win on one of them.

## Designs and snapshot DUTs

Do not snapshot a core or `fpnew_top`. One module per design. Start here;
after this trio completes, move up (e.g. `fpnew_opgroup_block`, then
`fpnew_top`).

| Design | Snapshot DUT | Why this leaf | Modest rewrite | Yosys metric |
| --- | --- | --- | --- | --- |
| **fpnew** (cvfpu / common_cells) | `lzc` | 112 lines, combinational CLZ/CTZ, used by FMA/cast. Already a binary tree with `2**N` padding. | WIDTH-exact reduction (drop unused pad nodes) or one-hot→bin encoder. Ports stay `in_i`, `cnt_o`, `empty_o`. | cell count and/or ABC logic levels |
| **Sargantana** | `div_unit` | Sequential divider that instantiates `div_4bits` twice. SVApshot already treats this as the execution-stage sequential DUT (`drac_pkg` + `riscv_pkg`). | Early-out when remainder is 0 (skip remaining radix-4 steps). Port list frozen; `div_4bits` stays a leaf. | cell count and/or LTP on the iteration datapath |
| **Sargantana** | `mul_unit` | Two-stage 32/64 multiply. Self-contained. | Bypass *0/*1. Ports frozen. | cells / LTP |
| **Sargantana** | `ptw` | Page-table walker + `ptw_arb` + `pseudoLRU`. | Use PTE-cache hit to skip a walk. Ports frozen. | cells / LTP |
| **Sargantana** | `tlb` | SV39 CAM TLB + `pseudoLRU`. | Share ASID/VMID compare across page sizes. Ports frozen. | cells / LTP |
| **MegaBOOM** (firtool SV) | `AMOALU` | 17 lines, self-contained, no imports. One 64-bit adder sits on every AMO path, including AND/XOR/MIN. | Compute add / logic / minmax in parallel; the adder is used only for `AMOADD`. `io_mask/cmd/lhs/rhs/out` unchanged. | ABC delay on `io_out` (area may stay flat) |

Package / child closure (staged, not separately snapshotted):

- `lzc` → `cf_math_pkg`
- `div_unit` → `div_4bits`, `drac_pkg`, `riscv_pkg`
- `AMOALU` → none

Source pointers live in `targets.yaml`. A design may also set
`match_instance` so the DUT elaborates with the same parameter setting as a
named parent instantiation (used by `fpnew_top` for Sargantana
`fpu_drac_wrapper.i_fpuv_top`: `EPI_RV64D` + `EPI_INIT`). Struct parameters
are written into the staged header defaults; SVApshot generate receives the
same setting as parent-instantiation context.

## What “better PPA” means here

Yosys from `hackathon/toolchains/oss-cad-suite` is the judge. No commercial
liberty, so we do **not** claim GE or Watts.

- **Area:** `synth; stat` cell + wire count on the DUT top.
- **Delay:** ABC combinational depth / arrival on the primary outputs after the
  same synth.
- **Power:** not measured. Cell count is the proxy.

A candidate wins if **at least one** of area or delay improves and the other
does not regress by more than a small tolerance (see `targets.yaml`
`max_regression`). Functional gold for the loop is the frozen snapshot, not
Spike and not LEC. EQY LEC still runs on a snapshot-clean rewrite so a
bit-level miss can be reported without failing the graph.

Each DUT then runs three port-frozen rewrites from that one snapshot:

| Mode | Intent | Typical snapshot |
| --- | --- | --- |
| **optimise** | Keep interface and function, better PPA | should pass (retries) |
| **expand** | Keep old behavior, add one capability | pass or fail, both useful |
| **trim** | Same ports, drop to the essential path | often fail — that is a result |

The two cases we score as headlines are **better PPA and passing snapshot**
and **failing snapshot on the new design**. The two packaged snapshot-pass
PPA wins so far are `div_unit` optimise (30/30) and `div_unit` trim (13/13);
see `results/div_unit_ppa_wins.md`.

## CHIA graph

Implemented by `chia_svapshot/chia_ppa_loop.py` (launcher: `run_ppa.sh`).
The old seed/prove/score loop in `svapshot_loop.py` is unchanged. Figure:
`hackathon/figures/chia_ppa_loop.svg`.

```
stage_ppa_dut ─▶ vcf_elaborate          ← hard gate (no retry)
                      │
                      ▼
                yosys_ppa (baseline)
                      │
                      ▼
          svapshot_generate_and_freeze
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
      optimise      expand       trim
      (retry)     (record)     (record)
          │           │           │
          ▼           ▼           ▼
     retain + reprove + Yosys PPA + EQY LEC

Headline cases: better PPA with a passing snapshot, or a failing snapshot
on the new RTL. A snapshot-clean rewrite then runs observational EQY LEC:
LEC fail does not fail the loop (it shows LEC is a stricter functional gate).
Ports stay frozen in every mode.
```

VC Formal elaborate is first and is never retried: a DUT that does not
elaborate is out of the loop. Baseline Yosys PPA is recorded before any
rewrite. The only back-edge is reprove → implementation.

| Node | Resource | Role |
| --- | --- | --- |
| `stage_ppa_dut` | `svapshot_cpu` | Copy module + child RTL + packages |
| `vcf_elaborate` | `formal` | Analyze + elaborate (hard gate) |
| `yosys_ppa` | `yosys` | `synth; stat; ltp` — baseline then candidate |
| `svapshot_generate_and_freeze` | `formal` | Generate SVA, freeze proved-non-vacuous |
| `llm_rtl_rewrite` | `llm` | Modest RTL rewrite; port list frozen |
| `retain_snapshot` | `svapshot_cpu` | Keep properties whose signals ⊆ new DUT |
| `svapshot_reprove` | `formal` | Re-prove retained SVA; fail returns to rewrite |
| `score_ppa` | *(none)* | Candidate vs baseline cells / LTP |
| `yosys_lec` | `yosys` | EQY gold-vs-gate after a snapshot-clean rewrite (observational) |

```bash
./run_ppa.sh --design sargantana                 # offline bypass
./run_ppa.sh --real --design sargantana          # VCF + Yosys + Vertex + reprove
./run_ppa.sh --real --design fpnew --design megaboom
```

## New SVApshot stage: retain after refactor

After the rewrite, some snapshot properties name internals that were deleted
or renamed. Those are not valuable and will not compile.

Keep a property iff every identifier it uses is in

```
signals(property) ∩ signals(refactored_RTL)
```

i.e. the property’s signal set is a subset of the rewritten DUT’s ports,
internals, and parameters (SVA built-ins stripped first). Optionally
`mode=touched` further requires a non-empty intersection with the
baseline↔rewrite signal diff, so properties that only mention untouched
ports can be dropped.

Implemented in `src/analysis/retain_after_refactor.py`. Wire it into
`generate_and_freeze` as a post-rewrite filter; do not invent a second
snapshot generator.

## Snapshot library

Every freeze copies `<module>_prop.sv` and `snapshot_metrics.json` into
`chia_svapshot/snapshot_library/<module>/<stamp>/`. `latest` is a symlink to the
best copy. Point `--reuse-snapshot` at that path instead of a disposable run
workspace.

## What we are not doing

- No Vector extension, no Saturn score, no whole-ROB bind.
- No `fpnew_fma` / `exe_stage` / `RenameMapTable` rewrites.
- No claim that Yosys area is a silicon tapeout number.
- No CHIA agent that is allowed to change the DUT’s port list.
