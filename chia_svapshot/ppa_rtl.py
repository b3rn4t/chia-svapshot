"""RTL and SVA helpers for the CHIA PPA loop (no Ray / CHIA imports)."""

from __future__ import annotations

import re
from typing import List, Sequence, Set, Tuple

from .ppa_state import PortSig, ReproveResult

DESIGNER_MARKER = "//====DESIGNER-ADDED-SVA====//"

_TYPE_WORDS = frozenset({
    "wire", "logic", "reg", "signed", "unsigned", "integer", "bit", "var",
    "ref", "const",
})
_PORT_RE = re.compile(
    r"\b(?P<dir>input|output|inout)\b(?P<body>[^,;]+)",
)
_MODULE_RE = re.compile(r"\bmodule\s+(\w+)\b")


def module_header(rtl: str, module: str) -> str:
    """Text from ``module <name>`` through the closing ``);`` of the port list."""
    match = re.search(rf"\bmodule\s+{re.escape(module)}\b", rtl)
    if not match:
        return ""
    start = match.start()
    depth = 0
    seen_paren = False
    for index in range(match.end(), len(rtl)):
        char = rtl[index]
        if char == "(":
            depth += 1
            seen_paren = True
        elif char == ")":
            depth -= 1
            if seen_paren and depth == 0:
                end = rtl.find(";", index)
                return rtl[start: end + 1 if end != -1 else index + 1]
    return rtl[start: start + 4000]


def extract_ports(rtl: str, module: str) -> List[PortSig]:
    """ANSI ports of ``module``. Order is the declaration order."""
    header = module_header(rtl, module)
    ports: List[PortSig] = []
    seen: Set[str] = set()
    for match in _PORT_RE.finditer(header):
        body = match.group("body")
        packed = ""
        packed_match = re.search(r"\[([^\]]+)\]", body)
        if packed_match:
            packed = "[" + re.sub(r"\s+", "", packed_match.group(1)) + "]"
        names = [
            ident for ident in re.findall(r"[A-Za-z_]\w*", body)
            if ident not in _TYPE_WORDS
        ]
        if not names:
            continue
        name = names[-1]
        if name in seen:
            continue
        seen.add(name)
        ports.append(PortSig(match.group("dir"), name, packed))
    return ports


def ports_equal(left: Sequence[PortSig], right: Sequence[PortSig]) -> bool:
    """True iff direction, name, packed width, and order all match."""
    return [(p.direction, p.name, p.packed) for p in left] == [
        (p.direction, p.name, p.packed) for p in right
    ]


def port_diff(baseline: Sequence[PortSig], candidate: Sequence[PortSig]) -> str:
    """One-line explanation of a port-list mismatch."""
    left = [(p.direction, p.name, p.packed) for p in baseline]
    right = [(p.direction, p.name, p.packed) for p in candidate]
    if left == right:
        return ""
    if [p[1] for p in left] != [p[1] for p in right]:
        return (
            "port names/order changed: "
            f"{[p[1] for p in left]} -> {[p[1] for p in right]}"
        )
    changed = [
        f"{a[1]} {a[0]}{a[2]} -> {b[0]}{b[2]}"
        for a, b in zip(left, right)
        if a != b
    ]
    return "port widths/directions changed: " + "; ".join(changed)


def extract_sv_module(text: str, module: str) -> str:
    """Pull one ``module``…``endmodule`` out of an LLM reply."""
    fenced = re.search(
        rf"```(?:systemverilog|verilog|sv)?\s*(module\s+{re.escape(module)}\b.*?)```",
        text,
        flags=re.S | re.I,
    )
    blob = fenced.group(1) if fenced else text
    match = re.search(rf"\bmodule\s+{re.escape(module)}\b", blob)
    if not match:
        raise ValueError(f"LLM reply has no module {module}")
    end = list(re.finditer(r"\bendmodule\b", blob[match.start():]))
    if not end:
        raise ValueError(f"LLM reply: module {module} has no endmodule")
    return blob[match.start(): match.start() + end[-1].end()].rstrip() + "\n"


def extract_assertions(prop_text: str) -> List[str]:
    """Assertions after the designer marker, one string each."""
    if DESIGNER_MARKER not in prop_text:
        body = prop_text
    else:
        body = prop_text.split(DESIGNER_MARKER, 1)[1]
    body = re.sub(r"\n\s*endmodule\b.*", "\n", body, flags=re.S)
    chunks = re.split(
        r"(?=^\w+\s*:\s*(?:assert|assume|cover)\s+property)",
        body,
        flags=re.M,
    )
    return [chunk.strip() for chunk in chunks if "assert property" in chunk]


_ASSUME_BLOCK_RE = re.compile(
    r"\n// ---- SVApshot environment assumptions ----\n.*?(?=\nendmodule)",
    flags=re.S,
)


def extract_assumption_block(prop_text: str) -> str:
    """Keep the parent-env assume block across retain/splice."""
    match = _ASSUME_BLOCK_RE.search(prop_text)
    return match.group(0) if match else ""


def splice_assertions(prop_text: str, assertions: Sequence[str]) -> str:
    """Replace the designer-marker asserts; keep any environment assumes."""
    block = "\n\n".join(assertions)
    assume = extract_assumption_block(prop_text)
    if DESIGNER_MARKER in prop_text:
        head, tail = prop_text.split(DESIGNER_MARKER, 1)
        end = re.search(r"\nendmodule\b", tail)
        suffix = tail[end.start():] if end else "\nendmodule\n"
        return head + DESIGNER_MARKER + "\n" + block + assume + "\n" + suffix
    extracted = extract_assertions(prop_text)
    if not extracted:
        raise ValueError("property file has no assertions to splice")
    needle = extracted[0].strip()
    idx = prop_text.find(needle)
    if idx < 0:
        raise ValueError("cannot locate first assertion in property file")
    end = re.search(r"\nendmodule\b", prop_text[idx:])
    suffix = prop_text[idx + end.start():] if end else "\nendmodule\n"
    return prop_text[:idx] + block + assume + suffix


def proved_names(metrics: dict) -> Set[str]:
    """Property names SVApshot marked proved-non-vacuous."""
    props = (metrics or {}).get("properties") or {}
    if not props:
        qual = (metrics or {}).get("qualification") or {}
        props = qual.get("properties") or {}
    names: Set[str] = set()
    for name, status in props.items():
        if status in ("proved_non_vacuous", "proven", "proven_non_vacuous"):
            names.add(name)
    return names


def failed_count(metrics: dict) -> int:
    """How many properties the formal tool reported as failing."""
    qual = (metrics or {}).get("qualification") or {}
    if "failing_property_mismatch" in qual or "failing" in qual:
        return int(qual.get("failing_property_mismatch") or 0) + int(
            qual.get("failing") or 0
        ) + int(qual.get("failing_missing_assumption") or 0)
    props = (metrics or {}).get("properties") or qual.get("properties") or {}
    return sum(
        1
        for status in props.values()
        if status in ("failed", "failing", "cex", "failing_property_mismatch")
    )


def reprove_passed(
    *,
    kept: int,
    snapshot_worthy: int,
    failed: int,
    ports_ok: bool,
) -> Tuple[bool, str]:
    """Pass only when ports are frozen and every retained assertion still proves."""
    if not ports_ok:
        return False, "rewrite changed the DUT port list"
    if kept <= 0:
        return False, "no retained assertions to reprove"
    if failed > 0:
        return False, f"{failed} retained properties failed"
    if snapshot_worthy < kept:
        return False, f"{snapshot_worthy}/{kept} retained properties still proved"
    return True, f"all {kept} retained properties proved"


REWRITE_MODES = ("optimise", "expand", "trim")
REWRITE_ALIASES = {"refactor": "optimise", "optimize": "optimise"}


def canonical_rewrite_mode(mode: str) -> str:
    """Map legacy ``refactor`` / US spelling onto ``optimise``."""
    return REWRITE_ALIASES.get((mode or "").strip(), (mode or "").strip())


def is_optimise_mode(mode: str) -> bool:
    return canonical_rewrite_mode(mode) == "optimise"

HEADLINE_OUTCOMES = {
    "ppa_win_snapshot_pass": "better PPA and passing snapshot",
    "snapshot_fail": "failing snapshot on new design",
    "snapshot_clean_lec_fail": "snapshot-clean rewrite failed LEC",
}


def classify_mode_outcome(
    *,
    rewrite_ok: bool,
    snapshot_ok: bool,
    ppa_win: bool,
) -> str:
    """Label one rewrite-mode result.

    The two cases we care about are ``ppa_win_snapshot_pass`` (better Yosys
    cells/LTP and the frozen contract still proves) and ``snapshot_fail``
    (the new RTL broke retained properties).
    """
    if not rewrite_ok:
        return "rewrite_rejected"
    if not snapshot_ok:
        return "snapshot_fail"
    if ppa_win:
        return "ppa_win_snapshot_pass"
    return "snapshot_pass_no_ppa"


def headline_for(outcome: str) -> str:
    """Human label, or empty when the outcome is not a scored case."""
    return HEADLINE_OUTCOMES.get(outcome, "")


def lec_headline(
    *,
    snapshot_ok: bool,
    equivalent: bool,
    inconclusive: bool,
    skipped: bool,
) -> str:
    """Illustrative LEC label. Empty unless the rewrite was snapshot-clean."""
    if skipped or not snapshot_ok:
        return ""
    if inconclusive:
        return ""
    if equivalent:
        return ""
    return HEADLINE_OUTCOMES["snapshot_clean_lec_fail"]


def rewrite_feedback(reprove: ReproveResult) -> str:
    """Text handed back to the RTL implementation node."""
    return (
        f"Reprove failed: {reprove.reason}. "
        f"proved={reprove.snapshot_worthy} failed={reprove.failed} "
        f"total={reprove.total}. "
        "Keep the original port list. Restore behaviour the snapshot checks."
    )


def expand_path(path: str, mapping: dict) -> str:
    import os
    import string

    return os.path.expanduser(string.Template(path).safe_substitute(mapping))
