# Poster draft — Snapshot-gated RTL rewrite

**Working title.** Snapshot-gated RTL rewrite: *optimise*, *expand*, and *trim* under a frozen SVA contract.

**Venue note.** Fits a workshop poster (AI for Chip Design / similar) or an A0 print. Numbers below are from packaged CHIA runs (`hackathon/results/`) plus the first `fpnew_top` optimise pass. Yosys cell count and LTP are the PPA judge — not GE or Watts.

**Authors.** *TBD.*

**Figures.** `../figures/chia_ppa_loop.svg` (tools) and `../figures/chia_ppa_loop_topology.svg` (Ray node colors).

---

## One-sentence claim

A CHIA loop can freeze an SVApshot contract once, then run three port-frozen LLM rewrites — **optimise** (same function, better PPA), **expand** (keep the old function, add one capability), **trim** (essentials only) — and score each rewrite by Yosys *and* by whether the snapshot still proves.

Two headline outcomes are both useful: **better PPA with a passing snapshot**, and **a failing snapshot on the new RTL**.

---

## Why three modes

| Mode | Intent | What a “win” looks like |
| --- | --- | --- |
| **Optimise** | Same ports, same function, cheaper or shorter | Snapshot should pass (retries allowed). PPA win is the prize. |
| **Expand** | Keep every current result; add one capability on the same ports | Pass *or* fail is evidence. Area may grow. |
| **Trim** | Same ports; drop to the essential path | Often fails the old contract — that is the measurement. |

**Optimise has a second axis on hierarchical DUTs** (`fpnew_top`):

| Scope | What the LLM may emit |
| --- | --- |
| **Top** | Only `fpnew_top.sv`. Children (opgroup, FMA, DivSqrt, …) stay leaves. |
| **Hierarchy** | Top plus children. More PPA room, weaker “the snapshot still talks about the same internals” story. |

The live Sargantana-config run (`match_instance` = `fpu_drac_wrapper.i_fpuv_top`, in-tree C910 `ct_vfdsu_*`) is **top-scope optimise**. A paired hierarchy pass is not packaged yet — leave a blank column on the print until it finishes.

---

## Method (one column)

1. **Stage** the DUT + packages (`match_instance` folds parent `#()` defaults when set).
2. **VC Formal elaborate** — hard gate, no retry.
3. **Yosys baseline** — `synth; stat; ltp`.
4. **SVApshot generate and freeze** — Gemini drafts SVA; VCF proves; keep proved-non-vacuous (cap 50).
5. For each mode: **Gemini rewrite** (ports frozen) → **retain** (signals ⊆ new DUT) → **VCF reprove** (fail returns to rewrite on optimise) → **Yosys candidate** → **score** → observational **EQY LEC** if the snapshot passed.

Functional gold for the loop is the frozen snapshot, not LEC. A LEC fail is recorded, not a graph failure.

---

## Main results

Yosys cells and longest topological path (LTP). Snapshot column is retained properties that proved after the rewrite. Packaged cases: `hackathon/results/INDEX.json`. Model is Gemini 3.1 Pro unless noted.

### `div_unit` (Sargantana divider) — baseline 9,257 cells / LTP 243

| Mode | Cells | Δ cells | LTP | Snapshot | Headline |
| --- | ---: | ---: | ---: | --- | --- |
| **Optimise** | 6,531 | **−29%** | 243 | 30/30 pass | better PPA and passing snapshot |
| **Expand** | 10,307 | +11% | 245 | 30/30 pass | snapshot holds; no PPA win |
| **Trim** | 6,556 | **−29%** | 242 | 13/13 pass (17 dropped) | better PPA and passing snapshot |

Trim used Gemini 2.5 Pro (`ppa_sargantana`). Optimise/expand used 3.1 Pro (`ppa_div_unit_opt`).

### `ptw` (page-table walker) — baseline 2,813 cells / LTP 22

| Mode | Cells | Δ cells | LTP | Snapshot | Headline |
| --- | ---: | ---: | ---: | --- | --- |
| **Optimise** | 2,858 | +1.6% | 22 | 43/43 pass | snapshot holds; no PPA win |
| **Expand** | 3,148 | +12% | 22 | **0/43 fail** | failing snapshot on new design |
| **Trim** | 582 | **−79%** | **13** | **0/37 fail** | failing snapshot on new design |

Trim is the intended “drop virtualization / PTE cache” cut: large PPA, contract dies. Expand added a last-leaf replay and the snapshot did not survive.

### `fpnew_top` — optimise, top vs hierarchy

| Setting | Scope | Baseline | Candidate | Snapshot | Status |
| --- | --- | --- | --- | --- | --- |
| Default cvFPU params, `ct_vfdsu_top` blackbox | **Top** rewrite | 99,959 / LTP 200 | 100,544 / LTP 198 | 17/30 fail after 3 retries | Packaged as *failing snapshot on new design*. Score called a delay win; area did not improve. |
| Sargantana `i_fpuv_top` (`EPI_RV64D` + `EPI_INIT`), real C910 DivSqrt | **Top** | 74,070 / LTP 108 | — | generate in flight | Live `ppa_fpnew_top_sarg` |
| Either setting | **Hierarchy** | — | — | — | **Not run yet.** Same snapshot, children writable. |

Do not mix the 99,959 and 74,070 baselines: different Features / Implementation / DivSqrt RTL.

---

## What we are *not* claiming

- Yosys cells ≠ ASIC area. LTP ≠ closed timing.
- A passing snapshot is not bit-level equivalence (EQY is stricter and observational).
- `mul_unit` generate yielded 0/50 snapshot-worthy properties — out of the result table.
- Early `tlb` / `ptw` rewrite attempts that broke `endmodule` are tooling bugs, not mode results.

---

## Takeaways (three bullets for the footer)

1. **`div_unit` has two snapshot-pass PPA wins.** Optimise −29% cells at 30/30; trim −29% cells / −1 LTP at 13/13 after dropping 17 properties. Same baseline. Write-up: `../results/div_unit_ppa_wins.md`.
2. **Expand and trim are measurements, not just optimisations.** `ptw` expand/trim fail the snapshot on purpose; `ptw` optimise held 43/43 and missed PPA (+1.6%).
3. **Hierarchy vs top is the next FPU question.** Top-only `fpnew_top` so far fails to keep the snapshot while barely moving PPA. Hierarchy is the missing cell on this poster.

---

## Print checklist

- [ ] Fill authors / affiliation / QR to repo
- [ ] Swap in hierarchy `fpnew_top` row when that pass finishes
- [ ] Swap in Sargantana-config `fpnew_top` optimise/expand/trim
- [ ] Optional: one RTL snippet (div early-out) next to the −29% cell
- [ ] Compile `poster.html` to PDF (browser print, A0 landscape) or move copy into a `tikzposter` later
