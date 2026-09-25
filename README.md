# SVApshot × CHIA

Runs [SVApshot 0.1](https://github.com/b3rn4t/svapshot) — LLM-driven SVA
snapshot generation — as a [CHIA](https://docs.chialoops.ai) loop on a local
Ray instance or a cluster.

The 0.1 generator submodule does **not** vendor DUT RTL. You clone (or unpack)
a design tree on the host and point the loop at it. The worked example is
Sargantana’s `div_4bits`; any tree `dut_prep` can index works the same way.

## Host values you fill in

Replace every `<…>`.

| Variable / path | Required when | Notes |
| --- | --- | --- |
| `--source-root` / DUT tree | snapshot loop (real **or** the scaffold stage of offline) | Default in code is `$SVAPSHOT_ROOT/benchmarks/sargantana`, which is **not** in 0.1. Clone the core (or another DUT) yourself. |
| `VC_FORMAL_HOME` | `--real` prove / PPA elaborate | Synopsys tree on **this** machine |
| `SNPSLMD_LICENSE_FILE` | `--real` VC Formal | `port@host` or licence file |
| `NVIDIA_API_KEY` | `--prove-mode agent` with NVIDIA models | |
| `GOOGLE_CLOUD_PROJECT` | PPA default model `gemini-2.5-pro` (Vertex) | plus `gcloud` ADC on the host |
| `GOOGLE_CLOUD_LOCATION` | Vertex (optional) | defaults to `global` |
| `CURSOR_API_KEY` / `OPENAI_API_KEY` | if you switch the model to those backends | |
| `hackathon/toolchains/oss-cad-suite` | `--real` PPA (Yosys / EQY) | created by `hackathon/scripts/fetch_formal.sh` |
| `chia_env/` | always (CHIA needs Python ≥ 3.10) | local launchers use `$PWD/chia_env`; `cluster.yaml` sources `$SVAPSHOT_ROOT/chia_env` — create a venv in **both** places or point the yaml at this repo’s venv |

## Setup

```bash
git clone --recurse-submodules git@github.com:b3rn4t/chia-svapshot.git
cd chia-svapshot

export SVAPSHOT_ROOT="$PWD/svapshot"

# DUT (example: Sargantana). Not shipped; pick a host path.
git clone <sargantana-git-url> /path/to/sargantana
export DUT_ROOT=/path/to/sargantana

# Formal (real runs)
export VC_FORMAL_HOME=/path/to/vc_formal
export SNPSLMD_LICENSE_FILE=27000@licence-host

# LLM — pick the backend you will actually call
export NVIDIA_API_KEY=
export GOOGLE_CLOUD_PROJECT=                 # Vertex / PPA gemini-*
# export GOOGLE_CLOUD_LOCATION=global

# CHIA interpreter (separate from system Python 3.9 used inside SVApshot)
python3.11 -m venv chia_env
./chia_env/bin/pip install chia-loop
export CHIA_PYTHON="$PWD/chia_env/bin/python"
# For `chia up cluster.yaml` only:
python3.11 -m venv svapshot/chia_env
./svapshot/chia_env/bin/pip install chia-loop

# PPA real runs: Yosys + EQY (writes hackathon/toolchains/oss-cad-suite)
./hackathon/scripts/fetch_formal.sh
export PATH="$PWD/hackathon/toolchains/oss-cad-suite/bin:$PATH"
```

## Snapshot loop

```bash
# Offline: the scaffolder is real; seed/prove are replayed. Still needs --source-root
# so scaffolding can see RTL. No licence, no API key.
./run_local.sh --source-root "$DUT_ROOT" --iterations 3

# Real prove, hand-written seeds, no LLM
./run_local.sh --real --source-root "$DUT_ROOT" \
  --prove-mode formal --seed-file seeds/div_4bits.sva

# Real, SVApshot LLM repair (NVIDIA example)
./run_local.sh --real --source-root "$DUT_ROOT" \
  --prove-mode agent --assertion-source rtl
```

Cluster:

```bash
export THIS_MACHINE=$(hostname -I | awk '{print $1}')
chia up cluster.yaml          # workers require VC_FORMAL_HOME in the environment
chia job submit --working-dir . -- python svapshot_loop.py \
  --ray-address auto --source-root "$DUT_ROOT"
```

## PPA loop

Needs the same DUT tree (`hackathon/targets.yaml` expands
`${SVAPSHOT_ROOT}/benchmarks/sargantana/...` unless you edit those paths or
place a clone there).

```bash
# Optional: make the yaml default work without editing
mkdir -p "$SVAPSHOT_ROOT/benchmarks"
ln -s /path/to/sargantana "$SVAPSHOT_ROOT/benchmarks/sargantana"

./run_ppa.sh --design sargantana                 # offline bypass
./run_ppa.sh --real --design sargantana          # VCF + Yosys + Vertex + reprove
./run_ppa.sh --real --design ptw --reuse-snapshot snapshot_library/ptw/latest
```

`run_ppa.sh` defaults `SVAPSHOT_LLM_MODEL` to `gemini-2.5-pro` (needs
`GOOGLE_CLOUD_PROJECT`). Override with `export SVAPSHOT_LLM_MODEL=…` to use
another backend.

After a snapshot is frozen, each DUT runs three port-frozen rewrites:
**optimise**, **expand**, **trim**. Snapshot-clean rewrites also get
observational EQY LEC (a LEC fail is recorded, not a loop failure). Copies
land in `snapshot_library/<module>/`.

## Layout

| Path | What it is |
| --- | --- |
| `svapshot/` | Git submodule: [SVApshot 0.1](https://github.com/b3rn4t/svapshot) |
| `svapshot_loop.py` | Snapshot-quality loop (seed → prove → score) |
| `chia_ppa_loop.py` | PPA loop: VCF elab → Yosys → SVApshot → rewrite ↔ reprove → EQY |
| `run_local.sh` / `run_ppa.sh` | Local Ray launchers (`--real` disables bypass) |
| `cluster.yaml` | Logical workers: `svapshot_cpu`, `llm`, `formal` |
| `chia_svapshot/_stage_driver.py` | One SVApshot stage under the generator’s interpreter |
| `seeds/div_4bits.sva` | Hand-written seeds for the example DUT |

## How the snapshot graph lines up

```
stage_rtl_dut ─▶ prepare_run_root ─▶ svapshot_scaffold ─▶ patch_package_closure
                                                                    │
                                                                    ▼
                                                            svapshot_seed
                                                                    │
                                                                    ▼
    score_snapshot ◀── collect_snapshot ◀── svapshot_prove / svapshot_formal_check
```

| Node | Resource |
| --- | --- |
| stage / scaffold / patch | `svapshot_cpu` |
| `svapshot_seed` | `llm` |
| prove / formal-check | `formal` (one VC Formal licence per unit) |

`formal: 1` in `cluster.yaml` is what stops a wide loop over-subscribing a
single licence.

## Notes for the SVApshot side

1. **Package closure stops at one level.** `patch_package_closure` / `dut_prep.py`
   walk the real import and `pkg::` closure.
2. **Seed parsing assumes concurrent assertions.** Immediate `always_comb`
   properties are spliced at the designer marker instead.
3. **Harness paths are CWD-relative.** `workspace.py` gives each run its own root.
