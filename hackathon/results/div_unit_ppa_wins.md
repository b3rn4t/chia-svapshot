# `div_unit` PPA success stories

Two packaged CHIA runs on Sargantana `div_unit` are **better Yosys PPA and a passing snapshot** (`ppa_win_snapshot_pass`). They are the only snapshot-pass PPA wins so far. Yosys cells / LTP are the judge — not GE or Watts. Functional gold is the frozen snapshot, not EQY.

Packaged trees: `div_unit_optimise_win/`, `div_unit_trim_win/`. Index: `INDEX.json`.

## Scoreboard

Baseline (both): **9,257 cells / 7,357 wires / LTP 243**.

| Mode | Model | Cells | Δ cells | Wires | LTP | Snapshot | Headline |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| **Optimise** | Gemini 3.1 Pro | 6,531 | **−29%** (−2,726) | 5,400 | 243 | **30/30** retained proved | better PPA, passing snapshot |
| **Trim** | Gemini 2.5 Pro | 6,556 | **−29%** (−2,701) | 4,786 | **242** (−1) | **13/13** retained proved (17 dropped) | better PPA, passing snapshot |

Optimise workspace: `runs/ppa_div_unit_opt`. Trim workspace: `runs/ppa_sargantana` (also copied under `runs/safe/div_unit_trim_ppa_win`).

## What each rewrite did

**Optimise** keeps every current DIV/REM result (signed/unsigned, 32/64). The LLM took the allowed early-out on divisor 0/1 (and remainder 0): skip the 17/33 radix-4 iterations whose results were already muxed. `div_4bits` stays a leaf. Ports frozen.

**Trim** keeps the same port list but drops REM, signed, and 32-bit paths (unused result fields tied off). Retain then drops the 17 snapshot properties that named deleted internals. The remaining 13 still prove. LTP improves by one level; area is within 25 cells of optimise.

So trim is not “optimise with a different label”: it is a smaller contract on a narrower function, with a slightly better delay and almost the same cell win.

## Snapshot vs LEC

Structural COI on the gold snapshot is **100%** of `div_unit` design signals (30 properties / 31 signals). After trim retain: still 100% of the *remaining* DUT signals (13 properties / 23 candidate signals).

Observational EQY (slang, SAT depth 10, name-matched partitions):

| Mode | Partitions | Failed | What failed |
| --- | ---: | ---: | --- |
| Optimise | 6 / 1,891 | `equivalence unknown` on `remanent_q`, `dividend_quotient_q`, `cycles_counter` | Early-out changes the iteration schedule. `instruction_o` proved equivalent. |
| Trim | 13 / 1,885 | mix of CEX + unknown (`instruction_d`, `divisor_*`, `dividend_*`, `div_zero_d`, counters) | Dropped ops change datapath constants; not a rename.

A LEC fail does not fail the CHIA graph. It records that bit-level combinational equivalence is stricter than the snapshot once microarchitecture (or ISA subset) changes.

## What this is *not*

- Not two independent area numbers on different baselines — same gold RTL.
- Not a claim that trim is a legal RISC-V `div_unit` for Sargantana (it dropped ops). It is a measured “essentials-only” win under a reduced contract.
- Expand on the same snapshot grew to 10,307 cells / LTP 245 with 30/30 still proving — snapshot-pass, no PPA win.
