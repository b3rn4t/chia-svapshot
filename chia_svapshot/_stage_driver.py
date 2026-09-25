"""Run one SVApshot pipeline stage and report the outcome as JSON.

This script is the seam between CHIA and SVApshot. It runs under *SVApshot's*
interpreter (Python 3.9 with the in-tree Python 3 harness scaffolder), not the
CHIA virtualenv, so it must not import anything from ``chia`` or from this
package. CHIA nodes invoke it as a subprocess and parse the single JSON object
it prints between the sentinels below.

Splitting the stages this way is what lets the loop schedule them separately:
``scaffold`` only needs CPU, ``seed`` needs LLM credentials, and ``prove`` needs
a formal-verification licence.

Usage::

    python3 _stage_driver.py <stage> <json-args>
"""

import json
import os
import sys
import traceback

BEGIN = "===CHIA_SVAPSHOT_RESULT_BEGIN==="
END = "===CHIA_SVAPSHOT_RESULT_END==="


def _load_by_path(name, path):
    """Import a module from *path* under *name*.

    The ``sys.modules`` registration is not optional: modules that use
    ``from __future__ import annotations`` (``proof_status`` does) have string
    annotations, and ``dataclasses`` resolves them by looking the defining
    module up in ``sys.modules``. Skip the registration and every dataclass in
    the file fails to build.
    """
    import importlib.util

    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_svapshot(checkout):
    """Import the live ``main.py`` and make its sibling modules importable.

    ``main.py`` does flat imports of its neighbours (``assertion_template``,
    ``proof_status``, ...), so the run root goes on ``sys.path`` first — its
    symlinks resolve to the checkout, which keeps the import path and the
    subprocess working directory describing the same tree.
    """
    core = os.path.join(checkout, "src", "core")
    analysis = os.path.join(checkout, "src", "analysis")
    for entry in (os.getcwd(), checkout, core, analysis):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    sys.argv = ["main.py"]
    return _load_by_path("svapshot_main", os.path.join(core, "main.py"))


def stage_scaffold(sv, a):
    """Harness scaffolding: testbench dir, file lists, property/bind skeletons.

    Mirrors steps 1 through 1.7 of ``run_single_module_flow`` so that a failure
    is attributed to a specific sub-step instead of the whole pipeline.
    """
    steps = {}
    steps["scaffold"] = bool(
        sv.run_scaffold(a["rtl_module"], a["sources"], a["includes"], a["module_type"])
    )
    if not steps["scaffold"]:
        return False, steps

    steps["fix_prop_bind"] = bool(sv.validate_and_fix_property_bind_files(a["rtl_module"]))
    if not steps["fix_prop_bind"]:
        return False, steps

    if not a.get("disable_package_detection"):
        steps["packages"] = bool(
            sv.enhanced_create_manual_sub_and_inject_packages(
                a["rtl_module"], a["sources"], a["includes"]
            )
        )
        if not steps["packages"]:
            return False, steps

    if a.get("formal_tool") == "vcformal":
        steps["fpv_vcf_tcl"] = bool(
            sv.generate_fpv_vcf_tcl(a["rtl_module"], a["module_type"])
        )
        if not steps["fpv_vcf_tcl"]:
            return False, steps

    return True, steps


def stage_seed(sv, a):
    """Produce the initial assertion set and reset the testbench.

    ``rtl`` asks the direct RTL + SVA-rules generator for seed properties;
    ``file`` reads a pre-existing list; ``empty`` writes nothing and lets the
    agent start from the harness skeleton alone, which is the offline path.
    """
    steps = {}
    source = a.get("assertion_source", "rtl")

    if source == "rtl":
        steps["initial_generation"] = bool(
            sv.run_initial_assertion_generation(a["rtl_module"], a["llm_model"])
        )
        if not steps["initial_generation"]:
            return False, steps
    elif source == "file":
        steps["initial_file"] = bool(sv.read_initial_file(a["initial_file"]))
        if not steps["initial_file"]:
            return False, steps
    else:
        with open(os.path.join(os.getcwd(), "initial_assertions"), "w") as handle:
            handle.write(a.get("seed_text", ""))
        steps["seeded_empty"] = True

    steps["base"] = bool(sv.create_base_from_initial_assertions(a["rtl_module"]))
    if not steps["base"]:
        # SVApshot's seed parser only recognises concurrent `name: assert
        # property (...)`. A purely combinational DUT such as div_4bits has no
        # clock to sample on, so its properties are immediate assertions inside
        # always_comb and the parser rejects the whole file. Splice the text in
        # at the same marker the parser would have used.
        steps["base_verbatim"] = _splice_base(a)
        if not steps["base_verbatim"]:
            return False, steps

    steps["reset_tb"] = bool(sv.run_reset_tb(a["rtl_module"]))
    return steps["reset_tb"], steps


DESIGNER_MARKER = "//====DESIGNER-ADDED-SVA====//"


def _splice_base(a):
    """Write ``sva/base`` by inserting the seed text at the designer marker."""
    module = a["module"]
    sva_dir = os.path.join(os.getcwd(), "ft_" + module, "sva")
    prop_path = os.path.join(sva_dir, module + "_prop.sv")
    seed_text = a.get("seed_text", "")
    if not os.path.isfile(prop_path) or not seed_text.strip():
        return False

    with open(prop_path, "r") as handle:
        content = handle.read()
    if content.count(DESIGNER_MARKER) != 1:
        return False

    head, tail = content.split(DESIGNER_MARKER)
    with open(os.path.join(sva_dir, "base"), "w") as handle:
        handle.write(head + DESIGNER_MARKER + "\n" + seed_text + "\n" + tail)
    return True


def stage_prove(sv, a):
    """The expensive stage: the LLM repair loop driven by the formal tool."""
    ok = bool(
        sv.run_agent(
            a["rtl_module"],
            a["llm_model"],
            a["module_type"],
            a["execution"],
            a["verbosity"],
            a.get("formal_tool", "jaspergold"),
        )
    )
    return ok, {"agent": ok}


def stage_formal(sv, a):
    """Prove whatever properties are in the property file — no LLM involved.

    ``stage_prove`` runs SVApshot's agent, which needs model credentials and
    rewrites the properties as it goes. This stage is the bottom half of that on
    its own: invoke the formal tool once and qualify the result. It exists so a
    loop can produce a real, auditable snapshot from hand-written or
    agent-supplied properties while the agent itself is unavailable.

    Qualification is delegated to SVApshot's ``proof_status`` module rather than
    re-implemented, so the vacuity and assumption-dependence rules stay in one
    place.
    """
    import subprocess

    module = a["module"]
    tool = a.get("formal_tool", "vcformal")
    script_name = "run_vcf_batch.sh" if tool == "vcformal" else "run_jg_batch.sh"
    script = os.path.join(a["checkout"], "fpv_app_scripts", script_name)
    proj = "vcf_projs" if tool == "vcformal" else "projs"
    log_path = os.path.join(os.getcwd(), proj, module, "vcf.log" if tool == "vcformal" else "jg.log")

    # Detached from any terminal: the tool tree inherits stdin, and a read from
    # a terminal by a background process group stops the whole tree with SIGTTIN
    # part-way through the proof, silently and forever.
    proc = subprocess.run([script, module], cwd=os.getcwd(),
                          stdin=subprocess.DEVNULL, start_new_session=True)

    if not os.path.isfile(log_path):
        return False, {"formal": False, "reason": "no log at %s" % log_path,
                       "returncode": proc.returncode}

    with open(log_path, "r", errors="replace") as handle:
        log_text = handle.read()

    ps = _load_by_path(
        "proof_status",
        os.path.join(a["checkout"], "src", "analysis", "proof_status.py"),
    )
    result = ps.parse_formal_log(log_text, tool, module)
    report_dir = os.path.join(os.getcwd(), proj, module, "reports")
    if tool == "vcformal" and os.path.isdir(report_dir):
        result = ps.merge_reports_into_result(result, ps.load_vcformal_reports(report_dir))

    counts = result.counts_by_qualification()
    total = len(result.records)
    proved = counts.get("proved_non_vacuous", 0)

    metrics = {
        "module": module,
        "method": "svapshot-chia-formal-only",
        "formal_tool": tool,
        "qualification": {
            "total_properties": total,
            "proved_non_vacuous": proved,
            "proved_vacuous": counts.get("proved_vacuous", 0),
            "failing_property_mismatch": counts.get("failing_property_mismatch", 0),
            "failing_missing_assumption": counts.get("failing_missing_assumption", 0),
            "inconclusive": counts.get("inconclusive", 0),
            "snapshot_yield": round(float(proved) / total, 4) if total else 0.0,
        },
        "properties": {
            name: record.qualification.value for name, record in result.records.items()
        },
        "compile_failed": bool(result.summary.compile_failed),
    }

    sva_dir = os.path.join(os.getcwd(), "ft_" + module, "sva")
    if os.path.isdir(sva_dir):
        with open(os.path.join(sva_dir, "snapshot_metrics.json"), "w") as handle:
            json.dump(metrics, handle, indent=2)

    ok = total > 0 and not result.summary.compile_failed
    return ok, {"formal": ok, "metrics": metrics}


STAGES = {
    "scaffold": stage_scaffold,
    "seed": stage_seed,
    "prove": stage_prove,
    "formal": stage_formal,
}


def main():
    stage = sys.argv[1]
    args = json.loads(sys.argv[2])
    result = {"stage": stage, "ok": False, "steps": {}, "error": None}
    try:
        sv = _load_svapshot(args["checkout"])
        ok, steps = STAGES[stage](sv, args)
        result["ok"] = ok
        result["steps"] = steps
    except Exception as exc:  # surfaced to the CHIA node as a failed StageResult
        result["error"] = "{}: {}".format(type(exc).__name__, exc)
        result["traceback"] = traceback.format_exc()

    sys.stdout.flush()
    print(BEGIN)
    print(json.dumps(result))
    print(END)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
