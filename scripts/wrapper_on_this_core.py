#!/usr/bin/env python3
"""The wrapper's own tests that stand on this core, run against this core.

The core is pinned by the wrapper and published BEFORE it, so for a release of the
core nothing used to run the wrapper's tests on the code about to be published:
the wrapper's CI installs the PINNED core from the index. This is the job that
does: `wrapper-on-this-core` of .github/workflows/ci.yml installs the wrapper's
`main`, puts THIS checkout's core over the pin, and runs the wrapper test files
that touch the core's Juggler client directly.

Which files is COMPUTED here, never typed (`computed_list`): a test file of the
wrapper that imports `invisible_core.juggler`, or that builds a `Lifecycle`, an
`InjectedScript` or a `Connection` by hand. A new wrapper test of that kind joins
the run the day it is written; nobody keeps a list.

    wrapper_on_this_core.py list    --wrapper DIR
        the files, one per line (sorted).
    wrapper_on_this_core.py compare --wrapper DIR (--main-script PATH | --main-has-no-script)
        exit 1 unless the list is not empty and holds every file the list of MAIN's
        copy of this script gives on the same tree. The list of main is computed in
        the same job, on the same wrapper, by the copy of this script that main has:
        a change of the patterns in a branch cannot shorten the run without being
        seen, and a test the wrapper deleted leaves both lists alike. What main's copy
        is can be said only by the workflow, which checks main out: a copy that cannot
        be read is red; "main has no copy" is declared with --main-has-no-script, and
        only for the pull request that adds this file.
    wrapper_on_this_core.py wrapper-ref --event NAME [--ref-type TYPE] [--body-file PATH] [--proof-file PATH]
        which commit or branch of the wrapper the job checks out: `main`, always, except
        on a pull request whose body DECLARES another with one line, `wrapper-ref: <branch>`
        (see `choose_wrapper_ref`): the branch must exist in the wrapper's repository; and
        except on a tag or a manual run (a RELEASE event), where it is the commit
        ci/wrapper-proof.json declares. Prints the ref; writes it and the reason to
        $GITHUB_OUTPUT (`ref=`) and to $GITHUB_STEP_SUMMARY. A malformed line, two lines, a
        missing branch or a bad proof file is exit 1 with the reason. On a pull request
        and a push the judge and the list to compare with are still main's.
    wrapper_on_this_core.py previous-release --published FILE (--version V | --pyproject FILE)
        the tag (`v<version>`) of the highest release in FILE's `released` that is lower
        than V (or than the version of the pyproject): what a release is compared with.
        Red if there is none. Used for the core and for the wrapper.
    wrapper_on_this_core.py proof-version --event NAME --ref-type TYPE --ref-name NAME
        the version of the core a release event proves: the tag without its `v`, or this
        tree's `_version.py` on a manual run. `tree-version` prints the latter.
    wrapper_on_this_core.py read-wrapper-proof --file PATH
        the commit ci/wrapper-proof.json declares (40 lower-case hex), or exit 1. The file is exactly
        {"wrapper_commit": "<40 hex>", "series_run": <int>}: both keys, no other. `read-series-run` prints the run.
    wrapper_on_this_core.py write-wrapper-proof --plan release-plan.json --seal seal.json --out PATH
        writes the proof file from the `release-plan` artifact of a candidate run of the series; refuses a plan that is not a
        candidate's, or whose series_commit is not the seal's.
    wrapper_on_this_core.py artifact-id --artifacts FILE | check-release-plan --file PATH --seal PATH --run FILE [--zip FILE]
        ci.yml's check of the series run the proof file names (see `check_release_plan`): that run concluded success, and its
        `release-plan` names the same wrapper commit and the seal's series commit.
    wrapper_on_this_core.py check-wrapper-proof --file PATH --wrapper DIR --version V
        exit 1 unless the wrapper at DIR is the declared commit (its HEAD) and its
        pyproject.toml pins exactly `invisible_core==V`.
    wrapper_on_this_core.py engine
        the engine the seal of the installed core names, which must be PUBLISHED: if
        its release or the asset of this system is not there it is exit 1 with the
        reason, never a skip. Prints the path of the fetched binary as the last line.
    wrapper_on_this_core.py plan    --wrapper DIR --os linux|windows --parts N [--collect]
        prints the plan the legs share: the estimated seconds of each part and what it
        runs. Without --collect a long file gets no node list (it is shown whole).
    wrapper_on_this_core.py run     --wrapper DIR --binary PATH --os linux|windows --part N --parts N
        first checks that the reason of every exclusion of the system still holds (red,
        naming the entry, if not: "the reason is gone: run it, remove the exclusion"), then
        runs the part the way the wrapper's own e2e runner does
        (invisible_core.testing.e2e: the same command, four browsers at once, the
        wall-clock tests alone at the end) and judges the junit: red on a failure, on
        a SKIPPED test, on a pass that ran nothing, or on a file of the part of which
        no test ran.

Which part runs what is a PLAN (`plan_parts`), the same on every leg: the seconds each
test file took are in scripts/wrapper_test_seconds.json (estimated from the logs of
the wrapper's e2e, for balancing the legs only: it never decides the verdict). A file
over SPLIT_OVER_SECONDS is cut into chunks of its test nodes (the ids come from
`pytest --collect-only` in the leg); the units (a whole file, or a chunk) are then
handed to the parts longest first, each to the part with the least estimated time.

Standard library only, except `engine` and `run`, which import the installed core.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

__all__ = ["WrapperRefError", "ProofError", "PROOF_FILE", "is_release_event", "read_wrapper_proof", "previous_release", "proof_problems", "read_proof", "write_wrapper_proof", "check_release_plan", "artifact_id", "validate_plan",
           "MADE_BEFORE_THE_RULE", "stale_allowances",
           "proof_version", "tree_version", "choose_wrapper_ref", "declared_wrapper_refs", "EXCLUDED", "WITNESSES", "witness_problems", "computed_list", "excluded_nodes", "files_of", "judge_junit", "load_seconds",
           "missing_from", "plan_parts", "problems_in_junit"]

#: A wrapper test file that imports the Juggler client of the core.
IMPORTS_JUGGLER = re.compile(
    r"^[ \t]*(?:from[ \t]+invisible_core\.juggler\b"
    r"|import[ \t]+invisible_core\.juggler\b"
    r"|from[ \t]+invisible_core[ \t]+import[ \t]+[^#\n]*\bjuggler\b)",
    re.MULTILINE)
#: A wrapper test file that builds one of the core's objects by hand.
BUILDS_BY_HAND = re.compile(r"\b(?:Lifecycle|InjectedScript|Connection)\(")

#: Vendored upstream suite: it has its own dependencies and is never collected.
_NEVER_COLLECTED = "playwright-upstream"

#: What this run leaves out, and why. A skipped test turns the run red, so a test
#: that cannot run on a system is taken out HERE, by name, with its reason: not
#: left to skip. Each entry is (system or None for both, test file or node id,
#: reason).
EXCLUDED = (
    ("windows", "tests/test_juggler_posix_pipe.py",
     "the POSIX leg of the pipe does not execute on Windows (module-level skipif on win32)"),
    (None, "tests/test_the_working_clone_has_the_workbench_next_to_it.py::test_the_workbench_inventory_and_the_package_cannot_DRIFT",
     "reads a script of the maintainer's workbench, which no clone has (pytest.skip)"),
    (None, "tests/test_juggler_injected.py::test_OUR_CLIENT_DOES_NOT_CROSS_into_the_page_realm",
     "reads a trap page of the maintainer's workbench, which no clone has (pytest.skip)"),
)


# ------------------------------------------------------------------ the list

def computed_list(wrapper: Path) -> list[str]:
    """The test files of the wrapper checked out at ``wrapper`` that stand on the
    core's Juggler client, as paths relative to it with `/`, sorted."""
    wrapper = Path(wrapper)
    tests = wrapper / "tests"
    found = []
    for path in sorted(tests.rglob("test_*.py")):
        rel = path.relative_to(wrapper).as_posix()
        if _NEVER_COLLECTED in rel.split("/"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if IMPORTS_JUGGLER.search(text) or BUILDS_BY_HAND.search(text):
            found.append(rel)
    return found


def missing_from(computed: list[str], reference: list[str]) -> list[str]:
    """The files of ``reference`` that ``computed`` no longer has."""
    return sorted(set(reference) - set(computed))


def _excluded_for(system: str) -> list[str]:
    return [target for only, target, _ in EXCLUDED if only in (None, system)]


# ---------------------------------------------------------------- the witnesses
#
# An exclusion says "this cannot run here, and this is why". A reason can stop being
# true (the engine gate opens, the wrapper learns to run the test) and the exclusion
# would stay for ever, hiding a test that now could run. So every EXCLUDED entry has a
# WITNESS: a check, on the wrapper checkout, that its reason still holds. A witness
# that does not hold is RED, naming the entry: run it, remove the exclusion.

def _read(wrapper: Path, rel: str) -> str:
    path = Path(wrapper) / rel
    if not path.is_file():
        raise _Gone(f"{rel} is not in the wrapper any more")
    return path.read_text(encoding="utf-8", errors="replace")


class _Gone(Exception):
    """The reason of an exclusion no longer holds (the message says what changed)."""


def _function_body(text: str, name: str, rel: str) -> str:
    m = re.search(rf"^def {re.escape(name)}\(.*?(?=^(?:def |@|class )|\Z)", text, re.MULTILINE | re.DOTALL)
    if not m:
        raise _Gone(f"{rel} has no test {name} any more")
    return m.group(0)


def _w_module_skipif_win32(rel: str):
    def check(wrapper: Path, engine_tag: str | None) -> None:
        if not re.search(r"^pytestmark\s*=\s*pytest\.mark\.skipif\(\s*sys\.platform\s*==\s*[\"']win32[\"']",
                         _read(wrapper, rel), re.MULTILINE):
            raise _Gone(f"{rel} no longer skips the whole module on win32")
    return check


def _w_workbench(rel: str, test_name: str, skip_reason: str, absent: str, depth: int):
    """The test skips when a file of the maintainer's workbench is not there, and no clone has it."""
    def check(wrapper: Path, engine_tag: str | None) -> None:
        body = _function_body(_read(wrapper, rel), test_name, rel)
        if f'pytest.skip("{skip_reason}")' not in body:
            raise _Gone(f"{test_name} no longer skips when the workbench is absent")
        if Path(absent).name not in body:
            raise _Gone(f"{test_name} no longer reads {absent}")
        # `Path(__file__).resolve().parents[N]` of tests/<file>: N = 1 is the wrapper itself.
        base = Path(wrapper).resolve().parents[depth - 2]
        if (base / absent).exists():
            raise _Gone(f"{base / absent} exists in this clone: the test runs now")
    return check


#: entry (its target in EXCLUDED) -> the check of its reason.
WITNESSES = {
    "tests/test_juggler_posix_pipe.py": _w_module_skipif_win32("tests/test_juggler_posix_pipe.py"),
    "tests/test_the_working_clone_has_the_workbench_next_to_it.py::test_the_workbench_inventory_and_the_package_cannot_DRIFT":
        _w_workbench("tests/test_the_working_clone_has_the_workbench_next_to_it.py", "test_the_workbench_inventory_and_the_package_cannot_DRIFT",
                     "the workbench is not next to this checkout", "scripts/inventario_voce6.py", 3),
    "tests/test_juggler_injected.py::test_OUR_CLIENT_DOES_NOT_CROSS_into_the_page_realm":
        _w_workbench("tests/test_juggler_injected.py", "test_OUR_CLIENT_DOES_NOT_CROSS_into_the_page_realm",
                     "the trap page lives in the workbench, not here", "tests/gates/observable_crossings.py", 3),
}


def witness_problems(wrapper: Path, system: str, engine_tag: str | None) -> list[str]:
    """One line for every exclusion of ``system`` whose reason does not hold (or has no witness)."""
    out = []
    for only, target, _reason in EXCLUDED:
        if only not in (None, system):
            continue
        witness = WITNESSES.get(target)
        if witness is None:
            out.append(f"{target}: excluded with no witness that its reason still holds")
            continue
        try:
            witness(Path(wrapper), engine_tag)
        except _Gone as exc:
            out.append(f"{target}: the reason is gone: run it, remove the exclusion ({exc})")
    return out


def excluded_nodes(system: str) -> tuple[list[str], list[str]]:
    """(whole files, node ids) this system leaves out."""
    targets = _excluded_for(system)
    return ([t for t in targets if "::" not in t], [t for t in targets if "::" in t])


# ------------------------------------------------------------------ the plan

#: A test file the table has no row for is taken to have run this many seconds in
#: parallel (and none serial).
DEFAULT_SECONDS = 30.0
#: A file whose parallel seconds are over this is cut into chunks of its nodes.
SPLIT_OVER_SECONDS = 90.0
#: The browsers at once of a leg (the `--procs` of the run): a parallel second of
#: the table counts 1/PARALLEL_WIDTH of a second of a leg, a serial one counts 1.
PARALLEL_WIDTH = 4
SECONDS_TABLE = Path(__file__).with_name("wrapper_test_seconds.json")


def load_seconds(system: str, path: Path | None = None) -> dict:
    """The table of this system: {test file: {"parallel": s, "serial": s, "nodes": {...}}}."""
    with open(path or SECONDS_TABLE, encoding="utf-8") as fh:
        return json.load(fh)[system]


def _unit_seconds(parallel: float, serial: float) -> float:
    return parallel / PARALLEL_WIDTH + serial


def _is_excluded(node: str, nodes_out: list[str]) -> bool:
    # --deselect matches by prefix, so a parametrized test is left out whole.
    return any(node == n or node.startswith(n + "[") for n in nodes_out)


def _split_file(rel: str, row: dict, nodes: list[str], k: int) -> list[dict]:
    """``nodes`` of a file cut into ``k`` chunks balanced by the seconds the table has
    for each node (an unknown node: the average of the file's known ones)."""
    prefix = rel + "::"
    par = {prefix + n: sec for n, sec in row.get("nodes", {}).items()}
    ser = {prefix + n: sec for n, sec in row.get("serial_nodes", {}).items()}
    known = list(par.values()) + list(ser.values())
    average = sum(known) / len(known) if known else DEFAULT_SECONDS

    def cost(node: str) -> float:
        if node in ser:
            return ser[node]
        return par.get(node, average) / PARALLEL_WIDTH

    k = max(1, min(k, len(nodes)))
    chunks = [{"nodes": [], "seconds": 0.0} for _ in range(k)]
    for node in sorted(nodes, key=lambda n: (-cost(n), n)):
        target = min(chunks, key=lambda c: c["seconds"])  # the first of equals: deterministic
        target["nodes"].append(node)
        target["seconds"] += cost(node)
    return [c for c in chunks if c["nodes"]]


def plan_parts(files: list[str], system: str, parts: int, collect, table: dict | None = None) -> list[dict]:
    """What each of the ``parts`` legs runs; the same on every leg.

    ``collect(file)`` gives the node ids of a file (`pytest --collect-only -q`); it is
    asked only for the files the table has as long. Returns one dict per part, in the
    order of the parts: {"files": whole files, "nodes": node ids of the chunks,
    "seconds": the estimate}. Left out first: the files and nodes EXCLUDED here.
    """
    table = load_seconds(system) if table is None else table
    whole, nodes_out = excluded_nodes(system)
    kept = sorted(f for f in files if f not in whole)

    rows = {f: table.get(f, {"parallel": DEFAULT_SECONDS, "serial": 0.0}) for f in kept}
    secs = {f: _unit_seconds(r["parallel"], r.get("serial", 0.0)) for f, r in rows.items()}
    target = sum(secs.values()) / parts

    units: list[dict] = []
    for f in kept:
        if f in table and rows[f]["parallel"] > SPLIT_OVER_SECONDS:
            nodes = sorted(n for n in collect(f) if not _is_excluded(n, nodes_out))
            if nodes:
                k = max(2, math.ceil(secs[f] / target)) if target > 0 else 2
                units += [{"file": f, **c} for c in _split_file(f, rows[f], nodes, min(k, parts))]
                continue
        units.append({"file": f, "nodes": None, "seconds": secs[f]})

    out = [{"files": [], "nodes": [], "seconds": 0.0} for _ in range(parts)]
    for u in sorted(units, key=lambda u: (-u["seconds"], u["file"], u["nodes"] or [])):
        part = min(out, key=lambda p: p["seconds"])
        if u["nodes"] is None:
            part["files"].append(u["file"])
        else:
            part["nodes"] += u["nodes"]
        part["seconds"] += u["seconds"]
    for part in out:
        part["files"].sort()
        part["nodes"].sort()
        part["seconds"] = round(part["seconds"], 1)
    return out


def files_of(part: dict) -> list[str]:
    """Every test file a part touches: the whole ones and those of its node ids."""
    return sorted(set(part["files"]) | {n.split("::", 1)[0] for n in part["nodes"]})


def collect_nodes(wrapper: Path, env: dict):
    """A ``collect`` for plan_parts: the node ids of a file, as the legs see them."""
    def collect(rel: str) -> list[str]:
        # `-m` overrides the wrapper's addopts (`-m 'not slow and not e2e'`), which would
        # deselect every e2e node: the legs list what they run, all of it.
        cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
               "-m", "e2e or not e2e", rel]
        done = subprocess.run(cmd, cwd=wrapper, env=env, capture_output=True, text=True)
        nodes = [ln.strip() for ln in done.stdout.splitlines() if ln.startswith(rel + "::")]
        if done.returncode != 0 or not nodes:
            # A leg that fell back to the whole file would run what another leg runs too.
            raise RuntimeError(f"cannot list the nodes of {rel} (pytest exit {done.returncode}):\n"
                               f"{done.stdout[-2000:]}\n{done.stderr[-2000:]}")
        return nodes
    return collect


# ----------------------------------------------------------------- the judge

def _classname_of(rel: str) -> str:
    return rel[:-3].replace("/", ".")


def problems_in_junit(junit_paths: list[Path], files: list[str]) -> list[str]:
    """Every reason the run of ``files`` is red, read from the junit it wrote."""
    problems: list[str] = []
    total = 0
    seen: set[str] = set()
    for jp in junit_paths:
        if not Path(jp).exists():
            continue  # a pass that collected nothing writes none: judged by the total
        root = ET.parse(jp).getroot()
        for case in root.iter("testcase"):
            cls, name = case.get("classname") or "", case.get("name") or ""
            if not cls:
                problems.append(f"{jp.name}: a testcase with no classname (not xunit2): {name}")
                continue
            skipped = case.find("skipped")
            total += 1
            for f in files:
                c = _classname_of(f)
                if cls == c or cls.startswith(c + "."):
                    seen.add(f)
            if case.find("failure") is not None or case.find("error") is not None:
                problems.append(f"FAILED {cls}::{name}")
            elif skipped is not None and skipped.get("type") == "pytest.rerun":
                problems.append(f"RERUN {cls}::{name}: a test passed only at a later attempt is not green")
            elif skipped is not None:
                reason = (skipped.get("message") or "").strip()
                problems.append(f"SKIPPED {cls}::{name}: {reason}")
    if total == 0:
        problems.append("no test ran at all: a run that ran nothing has not passed")
    for f in files:
        if f not in seen:
            problems.append(f"no test of {f} ran: it was deselected, not collected, or the run died first")
    return problems


def judge_junit(junit_paths: list[Path], files: list[str], codes: list[int]) -> list[str]:
    problems = problems_in_junit(junit_paths, files)
    # 5 is "nothing was collected": a pass of the run that had no test of its kind.
    bad = [c for c in codes if c not in (0, 5)]
    if bad:
        problems.append(f"pytest ended with code(s) {bad}")
    return problems


# ------------------------------------------------------------------ commands

# ------------------------------------------------------- the wrapper branch to try
#
# The job tries the wrapper's `main` against this core. A pull request of the core that
# changes an API the wrapper uses can name the wrapper branch that has the matching
# change, with ONE line of its body:
#
#     wrapper-ref: <branch>
#
# Grammar: the line starts with `wrapper-ref:` (nothing before it, lower case), ONE space,
# then the branch, and nothing else on the line (a trailing CR is ignored: GitHub's web
# form writes CRLF). The branch is made of letters, digits and `. _ - /`; it does not
# start with `-`, `.` or `/`, does not end with `/`, `.` or `.lock`, and has no `..`
# and no `//`. A line that starts with `wrapper-ref` followed by optional blanks and `:`
# in any case but does not fit is an ERROR, not an ignored line; so are two such lines.
# Only a pull request is read: on a push (main) the ref is `main` and the body, if there
# is one, is not looked at (a tag or a manual run takes the commit of ci/wrapper-proof.json,
# below); a merge group (the merge queue) has no body: `main`.

WRAPPER_REPO_URL = "https://github.com/oss-invisible/playwright"
WRAPPER_DEFAULT_REF = "main"
_DECLARATION = re.compile(r"^wrapper-ref[ \t]*:", re.IGNORECASE)
_DECLARATION_OK = re.compile(r"^wrapper-ref: ([A-Za-z0-9._/-]+)$")


class WrapperRefError(Exception):
    """The body of the pull request declares a wrapper ref that cannot be used."""


def declared_wrapper_refs(body: str) -> list[str]:
    """The lines of ``body`` that try to declare a wrapper ref (right or wrong), CR cut."""
    return [line.rstrip("\r") for line in (body or "").split("\n") if _DECLARATION.match(line)]


def _branch_name_problem(name: str) -> str | None:
    if name[0] in "-./" or name[-1] in "/." or name.endswith(".lock") or ".." in name or "//" in name:
        return "it is not a valid branch name (no leading `-`, `.` or `/`, no trailing `/` or `.` or `.lock`, no `..`, no `//`)"
    return None


def _ls_remote_heads(branch: str) -> str:
    """What `git ls-remote --heads` of the wrapper's repository says about ``branch``."""
    done = subprocess.run(["git", "ls-remote", "--heads", WRAPPER_REPO_URL, f"refs/heads/{branch}"],
                          capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise WrapperRefError(f"cannot ask {WRAPPER_REPO_URL} for its branches "
                              f"(git ls-remote exit {done.returncode}): {done.stderr.strip()}")
    return done.stdout


def choose_wrapper_ref(event: str, body: str | None, ls_remote=None, ref_type: str | None = None,
                       proof_file=None) -> tuple[str, str]:
    """(ref, why): the branch (for a release, the commit) of the wrapper to check out. Raises ``WrapperRefError``.

    ``ls_remote(branch)`` returns the text of `git ls-remote --heads <wrapper> refs/heads/<branch>`
    (injected by the tests). A tag or a manual run (``is_release_event``) reads ``proof_file``."""
    if is_release_event(event, ref_type):
        sha = read_wrapper_proof(proof_file or PROOF_FILE)
        return sha, f"event `{event}`: the commit {PROOF_FILE} declares, the wrapper release that pins this core (a `wrapper-ref:` line is not read)"
    if event == "merge_group":
        return WRAPPER_DEFAULT_REF, (f"event `merge_group`: a queue run has no pull request body, so the wrapper's {WRAPPER_DEFAULT_REF} "
                                     "(a declared branch was tried on the pull request's own run)")
    if event != "pull_request":
        return WRAPPER_DEFAULT_REF, f"event `{event}`: always the wrapper's {WRAPPER_DEFAULT_REF}, a `wrapper-ref:` line is not read"
    lines = declared_wrapper_refs(body or "")
    if not lines:
        return WRAPPER_DEFAULT_REF, f"no `wrapper-ref:` line in the body of the pull request: the wrapper's {WRAPPER_DEFAULT_REF}"
    if len(lines) > 1:
        raise WrapperRefError("the body of the pull request has " + str(len(lines)) + " `wrapper-ref:` lines, one is allowed: "
                              + " | ".join(repr(x) for x in lines))
    m = _DECLARATION_OK.match(lines[0])
    if not m:
        raise WrapperRefError(f"malformed line {lines[0]!r}: it is `wrapper-ref: <branch>`, one space, the branch "
                              "made of letters, digits and . _ - / and nothing else")
    branch = m.group(1)
    problem = _branch_name_problem(branch)
    if problem:
        raise WrapperRefError(f"malformed line {lines[0]!r}: {problem}")
    out = (ls_remote or _ls_remote_heads)(branch)
    if not any(row.split("\t")[-1].strip() == f"refs/heads/{branch}" for row in out.splitlines() if row.strip()):
        raise WrapperRefError(f"the branch `{branch}` declared by the pull request does not exist in {WRAPPER_REPO_URL}")
    return branch, f"declared by the pull request (`wrapper-ref: {branch}`), found in the wrapper's repository"


def _cmd_wrapper_ref(args) -> int:
    body = Path(args.body_file).read_text(encoding="utf-8", errors="replace") if args.body_file else ""
    try:
        ref, why = choose_wrapper_ref(args.event, body, ref_type=args.ref_type, proof_file=args.proof_file)
    except WrapperRefError as exc:
        print(f"wrapper-ref: {exc}", file=sys.stderr)
        _write_summary(f"### wrapper tried on this core\n\n**RED**: {exc}")
        return 1
    print(ref)
    print(f"wrapper ref: {ref} ({why})", file=sys.stderr)
    _write_summary(f"### wrapper tried on this core\n\nThe wrapper's tests ran on the branch **`{ref}`** of "
                   f"oss-invisible/playwright: {why}.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"ref={ref}\n")
    return 0


# ------------------------------------------------------- the proof of a release (a tag, a manual run)
#
# On a tag the core proves itself against the wrapper at ONE commit, declared in ci/wrapper-proof.json (the
# wrapper release that pins THIS core), never against the wrapper's main: main pins the previous core until the
# wrapper is released, so it is red by construction on the day of the core's release. And what a release is
# compared with is what is already PUBLISHED (the previous release of the tagged tree's PUBLISHED.json), never
# main: a release is cut from a release branch that main does not have.

PROOF_FILE = "ci/wrapper-proof.json"
_SHA40 = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class ProofError(WrapperRefError):
    """The proof of a release cannot be set up or does not hold."""


def is_release_event(event: str, ref_type: str | None) -> bool:
    """A tag push or a manual run: the events that publish (or stand in for it)."""
    return event == "workflow_dispatch" or (event == "push" and ref_type == "tag")


def read_proof(path) -> dict:
    """The proof file at ``path``: exactly {"wrapper_commit": "<40 lower-case hex>", "series_run": <positive int>}."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProofError(f"{path} cannot be read ({type(exc).__name__}): a release names the wrapper commit it is proved with") from exc
    except ValueError as exc:
        raise ProofError(f"{path} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or set(data) != {"wrapper_commit", "series_run"}:
        raise ProofError(f'{path} is not exactly {{"wrapper_commit": "<40 hex>", "series_run": <int>}}')
    sha, run = data["wrapper_commit"], data["series_run"]
    if not isinstance(sha, str) or not _SHA40.fullmatch(sha):
        raise ProofError(f"{path}: wrapper_commit {sha!r} is not 40 lower-case hex characters")
    if isinstance(run, bool) or not isinstance(run, int) or run <= 0:
        raise ProofError(f"{path}: series_run {run!r} is not the id of a run (a positive integer)")
    return {"wrapper_commit": sha, "series_run": run}


def read_wrapper_proof(path) -> str:
    """The commit ``path`` declares."""
    return read_proof(path)["wrapper_commit"]


# The proof file is written from the `release-plan` artifact of the series' candidate run (release.yml of oss-invisible/firefox_antidetect_patch):
# `release-plan.json` = {"run_id": int, "series_commit": "<40hex>", "wrapper_commit": "<40hex>", "core_ref": str, "kind": "candidate"}.
SERIES_REPO = "oss-invisible/firefox_antidetect_patch"
PLAN_ARTIFACT = "release-plan"
PLAN_FILE = "release-plan.json"
#: The one release made before the rule: its series run is a build run that ran the wrapper's tests but has no `release-plan` artifact. Keyed by the
#: series commit of the seal it ships (firefox-70): any other seal, or any other run, needs the artifact.
MADE_BEFORE_THE_RULE = {"063dde5e829e465bd3fccb2857b399d130c6b7d4": 45854283146}


def validate_plan(plan, where: str) -> dict:
    """A `release-plan.json` that a candidate run of the series wrote; raises ``ProofError``."""
    if not isinstance(plan, dict):
        raise ProofError(f"{where} is not a JSON object")
    missing = {"run_id", "series_commit", "wrapper_commit", "core_ref", "kind"} - set(plan)
    if missing:
        raise ProofError(f"{where} has no {sorted(missing)}")
    if isinstance(plan["run_id"], bool) or not isinstance(plan["run_id"], int) or plan["run_id"] <= 0:
        raise ProofError(f"{where}: run_id {plan['run_id']!r} is not the id of a run")
    for key in ("series_commit", "wrapper_commit"):
        if not isinstance(plan[key], str) or not _SHA40.fullmatch(plan[key]):
            raise ProofError(f"{where}: {key} {plan[key]!r} is not 40 lower-case hex characters")
    if not isinstance(plan["core_ref"], str) or not plan["core_ref"]:
        raise ProofError(f"{where}: core_ref is empty")
    if plan["kind"] != "candidate":
        raise ProofError(f"{where}: kind is {plan['kind']!r}, not 'candidate': only a candidate run proved a wrapper with a core")
    return plan


def _seal_series_commit(seal) -> str:
    commit = seal.get("series_commit") if isinstance(seal, dict) else None
    if not isinstance(commit, str) or not _SHA40.fullmatch(commit):
        raise ProofError(f"the seal has no usable series_commit: {commit!r}")
    return commit


def write_wrapper_proof(plan, seal, out) -> dict:
    """Write the proof file ``out`` from a release plan; refuses a plan that is not a candidate's or that was made for another series commit."""
    plan = validate_plan(plan, "the release plan")
    commit = _seal_series_commit(seal)
    if plan["series_commit"] != commit:
        raise ProofError(f"the plan was made for series commit {plan['series_commit']}, the seal of this core names {commit}")
    proof = {"wrapper_commit": plan["wrapper_commit"], "series_run": plan["run_id"]}
    Path(out).write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    return proof


def artifact_id(artifacts) -> str:
    """The id of the live `release-plan` artifact in the listing of a run's artifacts ('' when there is none); two are red."""
    listed = artifacts.get("artifacts") if isinstance(artifacts, dict) else None
    if not isinstance(listed, list):
        raise ProofError("the listing of the run's artifacts has no `artifacts` list")
    live = [a for a in listed if isinstance(a, dict) and a.get("name") == PLAN_ARTIFACT and not a.get("expired")]
    if len(live) > 1:
        raise ProofError(f"the run has {len(live)} artifacts named {PLAN_ARTIFACT}")
    return str(live[0]["id"]) if live else ""


def stale_allowances(seal, allowed=None) -> list[str]:
    """The entries of ``allowed`` (MADE_BEFORE_THE_RULE) that are not for the seal's series commit: the allowance dies at the first release that
    has a plan, so an entry for any other series commit is a leftover, and a red."""
    commit = _seal_series_commit(seal)
    allowed = MADE_BEFORE_THE_RULE if allowed is None else allowed
    return [f"MADE_BEFORE_THE_RULE still names series commit {key} (run {run}) while the seal names {commit}: the allowance ends with its release, remove the entry"
            for key, run in sorted(allowed.items()) if key != commit]


def check_release_plan(proof: dict, seal, run, plan_zip: bytes | None, allowed=None) -> list[str]:
    """What is wrong with the series run the proof file names (``run`` is the API's JSON of that run, ``plan_zip`` the bytes of its
    `release-plan` artifact or None): the run concluded success; its plan names the wrapper commit of the file and the seal's series commit.
    No plan is accepted only for the one release made before the rule (MADE_BEFORE_THE_RULE), and then the run must be on the seal's commit."""
    import io
    import zipfile
    allowed = MADE_BEFORE_THE_RULE if allowed is None else allowed
    problems = stale_allowances(seal, allowed)   # a newer seal refuses the entry, with or without a plan
    number = proof["series_run"]
    commit = _seal_series_commit(seal)
    if not isinstance(run, dict) or run.get("id") != number:
        problems.append(f"the record of the series run is not that of run {number}")
        return problems
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        problems.append(f"the series run {number} did not conclude success: {run.get('status')}/{run.get('conclusion')}")
    if plan_zip is None:
        if allowed.get(commit) != number:
            problems.append(f"the series run {number} has no `{PLAN_ARTIFACT}` artifact, and this release is not the one made before the rule")
        elif run.get("head_sha") != commit:
            problems.append(f"the series run {number} ran {run.get('head_sha')}, not the series commit {commit} of the seal")
        return problems
    try:
        with zipfile.ZipFile(io.BytesIO(plan_zip)) as archive:
            plan = validate_plan(json.loads(archive.read(PLAN_FILE).decode("utf-8")), f"{PLAN_FILE} of run {number}")
    except (zipfile.BadZipFile, KeyError, ValueError, ProofError) as exc:
        problems.append(f"the `{PLAN_ARTIFACT}` artifact of run {number} cannot be read: {exc}")
        return problems
    if plan["run_id"] != number:
        problems.append(f"the plan names run {plan['run_id']}, the file names run {number}")
    if plan["wrapper_commit"] != proof["wrapper_commit"]:
        problems.append(f"the plan of run {number} proved wrapper {plan['wrapper_commit']}, the file names {proof['wrapper_commit']}")
    if plan["series_commit"] != commit:
        problems.append(f"the plan of run {number} is for series commit {plan['series_commit']}, the seal names {commit}")
    return problems


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def previous_release(published, version: str) -> str:
    """The tag (`v<version>`) of the highest entry of ``published["released"]`` below ``version``."""
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ProofError(f"{version!r} is not a version (major.minor.patch)")
    released = published.get("released") if isinstance(published, dict) else None
    if not isinstance(released, list):
        raise ProofError("PUBLISHED.json has no `released` list")
    lower = []
    for entry in released:
        found = entry.get("version") if isinstance(entry, dict) else None
        if not isinstance(found, str) or not _VERSION.fullmatch(found):
            raise ProofError(f"an entry of `released` has no usable version: {found!r}")
        if _version_key(found) < _version_key(version):
            lower.append(found)
    if not lower:
        raise ProofError(f"PUBLISHED.json has no release below {version}: it is not known what was published before")
    return "v" + max(lower, key=_version_key)


def project_version(pyproject_text: str) -> str:
    import tomllib
    version = tomllib.loads(pyproject_text).get("project", {}).get("version")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ProofError(f"the pyproject has no usable [project] version: {version!r}")
    return version


def pinned_core_versions(pyproject_text: str) -> list[str]:
    """The `invisible_core==X` pins among the dependencies of the pyproject; a dependency on the core that is
    not exactly that (a range, a marker, an extra) reads as its own text, so it never equals a version."""
    import tomllib
    found = []
    for dep in tomllib.loads(pyproject_text).get("project", {}).get("dependencies", []):
        compact = re.sub(r"\s+", "", dep)
        if re.match(r"invisible[-_.]core(?![A-Za-z0-9])", compact, re.IGNORECASE):
            m = re.fullmatch(r"invisible[-_.]core==([0-9]+\.[0-9]+\.[0-9]+)", compact, re.IGNORECASE)
            found.append(m.group(1) if m else compact)
    return found


def proof_problems(declared: str, pyproject_text: str | None, head: str | None, version: str) -> list[str]:
    """What is wrong with the wrapper checked out for a release of core ``version``; [] when it is the one."""
    if pyproject_text is None:
        return [f"the wrapper at {declared} has no pyproject.toml"]
    problems = []
    pins = pinned_core_versions(pyproject_text)
    if pins != [version]:
        problems.append(f"the wrapper at {declared} does not ship with this core: it pins invisible_core {pins or 'nothing'}, "
                        f"this core is {version}")
    if head != declared:
        problems.append(f"the wrapper checkout is at {head}, not at the declared commit {declared}")
    return problems


def tree_version(root: Path | None = None) -> str:
    """The version of the tree at ``root``: its `_version.py`, loaded as a file (never imported as the package)."""
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    path = root / "src" / "invisible_core" / "_version.py"
    spec = importlib.util.spec_from_file_location("_tree_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.__version__


def proof_version(event: str, ref_type: str | None, ref_name: str, root: Path | None = None) -> str:
    """The core version a release event proves: the tag without `v`, or the tree's `_version.py` on a manual run."""
    if event == "workflow_dispatch":
        return tree_version(root)
    if event == "push" and ref_type == "tag":
        version = ref_name[1:] if ref_name.startswith("v") else ref_name
        if not _VERSION.fullmatch(version):
            raise ProofError(f"the tag {ref_name!r} is not v<major.minor.patch>")
        return version
    raise ProofError(f"event {event} ({ref_type}) is not a release: it has no version to prove")


def _cmd_previous_release(args) -> int:
    try:
        published = json.loads(Path(args.published).read_text(encoding="utf-8"))
        version = args.version or project_version(Path(args.pyproject).read_text(encoding="utf-8"))
        print(previous_release(published, version))
    except (OSError, ValueError, ProofError) as exc:
        print(f"previous-release: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_proof_version(args) -> int:
    try:
        print(proof_version(args.event, args.ref_type, args.ref_name))
    except (OSError, ProofError) as exc:
        print(f"proof-version: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_tree_version(_args) -> int:
    print(tree_version())
    return 0


def _cmd_read_wrapper_proof(args) -> int:
    try:
        print(read_wrapper_proof(args.file))
    except ProofError as exc:
        print(f"read-wrapper-proof: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_check_wrapper_proof(args) -> int:
    wrapper = Path(args.wrapper)
    try:
        declared = read_wrapper_proof(args.file)
        pyproject = wrapper / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8") if pyproject.is_file() else None
        done = subprocess.run(["git", "-C", str(wrapper), "rev-parse", "HEAD"], capture_output=True, text=True)
        head = done.stdout.strip() if done.returncode == 0 else None
        problems = proof_problems(declared, text, head, args.version)
    except (OSError, ValueError, ProofError) as exc:  # a file that cannot be read or parsed is red, never a pass
        print(f"check-wrapper-proof: {exc}", file=sys.stderr)
        return 1
    if problems:
        print("check-wrapper-proof: " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"the wrapper at {declared} pins invisible_core=={args.version}: it ships with this core")
    return 0


def _json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProofError(f"{path} cannot be read as JSON ({exc})") from exc


def _cmd_read_series_run(args) -> int:
    try:
        print(read_proof(args.file)["series_run"])
    except ProofError as exc:
        print(f"read-series-run: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_write_wrapper_proof(args) -> int:
    try:
        proof = write_wrapper_proof(_json_file(args.plan), _json_file(args.seal), args.out)
    except (OSError, ProofError) as exc:
        print(f"write-wrapper-proof: {exc}", file=sys.stderr)
        return 1
    print(f"{args.out}: wrapper {proof['wrapper_commit']}, series run {proof['series_run']}")
    return 0


def _cmd_artifact_id(args) -> int:
    try:
        print(artifact_id(_json_file(args.artifacts)))
    except ProofError as exc:
        print(f"artifact-id: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_check_release_plan(args) -> int:
    try:
        proof = read_proof(args.file)
        zipped = Path(args.zip).read_bytes() if args.zip else None   # a download that did not land is an error here, not "no plan"
        problems = check_release_plan(proof, _json_file(args.seal), _json_file(args.run), zipped)
    except (OSError, ProofError) as exc:
        print(f"check-release-plan: {exc}", file=sys.stderr)
        return 1
    if problems:
        print("check-release-plan: " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"series run {proof['series_run']}: concluded success"
          + (", and its release plan names this wrapper commit and this seal" if args.zip else ", made before the rule"))
    return 0


def _write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def _cmd_list(args) -> int:
    for f in computed_list(Path(args.wrapper)):
        print(f)
    return 0


def _load_main_copy(path: Path):
    spec = importlib.util.spec_from_file_location("wrapper_on_this_core_main", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _cmd_compare(args) -> int:
    wrapper = Path(args.wrapper)
    mine = computed_list(wrapper)
    print(f"this checkout computes {len(mine)} test file(s) of the wrapper")
    if not mine:
        print("the computed list is EMPTY: the patterns find no wrapper test that stands on "
              "the core's Juggler client, so this job would run nothing", file=sys.stderr)
        return 1
    if args.main_has_no_script:
        print("main has no copy of this script (this is the change that adds it): declared, "
              "nothing to compare with")
        return 0
    try:
        reference = _load_main_copy(Path(args.main_script)).computed_list(wrapper)
    except Exception as exc:  # a copy of main that cannot be read is not "main has none"
        print(f"main's copy of this script cannot be run ({exc!r}): it is not known what the "
              f"list of main is", file=sys.stderr)
        return 1
    print(f"main's copy of this script computes {len(reference)} on the same tree")
    gone = missing_from(mine, reference)
    if gone:
        print("this checkout computes a SHORTER list than main: it lost\n  " + "\n  ".join(gone),
              file=sys.stderr)
        return 1
    return 0


def _cmd_engine(_args) -> int:
    from invisible_core.download import ensure_binary
    from invisible_core.seal import active_seal
    from invisible_core.constants import RELEASE_URL_TEMPLATE

    seal = active_seal()
    key = sys.platform
    asset = seal.asset_for(key, platform.machine())
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/releases/", RELEASE_URL_TEMPLATE)
    owner, repo = m.group(1), m.group(2)
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{seal.tag}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "wrapper-on-this-core"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
            names = {a.get("name") for a in json.load(r).get("assets", [])}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"the seal of this core names the engine {seal.tag}, and {owner}/{repo} has no "
                  f"release by that name: it is NOT PUBLISHED. Publish the engine first; a "
                  f"release of the core waits for it. This job does not skip.", file=sys.stderr)
        else:
            print(f"the release index answered {exc.code} for {seal.tag}: it is not known whether "
                  f"the engine is published", file=sys.stderr)
        return 1
    if asset.name not in names:
        print(f"the engine {seal.tag} is published but has no {asset.name} (the asset of this "
              f"system): NOT PUBLISHED for {key}. This job does not skip.", file=sys.stderr)
        return 1
    print(f"engine {seal.tag}: {asset.name} is published")
    print(ensure_binary())
    return 0


def _cmd_plan(args) -> int:
    wrapper = Path(args.wrapper).resolve()
    collect = collect_nodes(wrapper, dict(os.environ)) if args.collect else (lambda f: [])
    plan = plan_parts(computed_list(wrapper), args.os, args.parts, collect)
    for n, part in enumerate(plan, 1):
        print(f"part {n}/{args.parts} {args.os}: ~{part['seconds']} s, {len(part['files'])} file(s), "
              f"{len(part['nodes'])} node(s)")
        for f in part["files"]:
            print("  " + f)
        for node in part["nodes"]:
            print("  " + node)
    return 0


def _cmd_run(args) -> int:
    from invisible_core.testing import e2e

    wrapper = Path(args.wrapper).resolve()
    files = computed_list(wrapper)
    _, nodes = excluded_nodes(args.os)
    from invisible_core.seal import active_seal
    gone = witness_problems(wrapper, args.os, active_seal().tag)
    if gone:
        print("[wrapper-on-this-core] RED: an exclusion has no reason left:", file=sys.stderr)
        for line in gone:
            print("  " + line, file=sys.stderr)
        return 1
    env = dict(os.environ)  # unlike run_e2e.main, INVISIBLE_CORE_PIN is kept: the pin is what this job overrides
    env["INVPW_BINARY_PATH"] = args.binary
    env["STEALTHFOX_E2E_BINARY"] = args.binary
    env.pop("PYTEST_ADDOPTS", None)
    plan = plan_parts(files, args.os, args.parts, collect_nodes(wrapper, env))
    for n, part in enumerate(plan, 1):
        print(f"[wrapper-on-this-core] plan: part {n}/{args.parts} ~{part['seconds']} s, "
              f"{len(part['files'])} file(s) + {len(part['nodes'])} node(s)")
    chosen = plan[args.part - 1]
    mine = files_of(chosen)
    print(f"[wrapper-on-this-core] {len(files)} files computed, part {args.part} of {args.parts} "
          f"on {args.os}: {len(chosen['files'])} whole file(s), {len(chosen['nodes'])} node(s) "
          f"of the long files, ~{chosen['seconds']} s")
    for only, target, reason in EXCLUDED:
        if only in (None, args.os):
            print(f"[wrapper-on-this-core] left out: {target}: {reason}")
    if not mine:
        print("[wrapper-on-this-core] this part has no file to run", file=sys.stderr)
        return 1

    out = Path(args.junit_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    selections = [(args.procs, "not slow and not e2e")] + e2e.passes(args.procs)
    extra_nodes = ["--deselect=" + n for n in nodes]
    codes, junits = [], []
    for i, (width, selection) in enumerate(selections):
        junit = out / f"pass-{i}.xml"
        junits.append(junit)
        cmd = e2e.command(width, chosen["files"] + chosen["nodes"] + extra_nodes + ["-o", "junit_family=xunit2",
                                                       f"--junitxml={junit}", "-rs"],
                          selection=selection)
        print(f"[wrapper-on-this-core] -m '{selection}', browsers at once: {width}", flush=True)
        print("[wrapper-on-this-core] " + " ".join(cmd), flush=True)
        codes.append(subprocess.run(cmd, cwd=wrapper, env=env).returncode)
        if codes[-1] not in (0, 1, 5):
            break
    problems = judge_junit(junits, mine, codes)
    if problems:
        print("[wrapper-on-this-core] RED:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    print("[wrapper-on-this-core] green: every file of the part ran, nothing failed, nothing skipped")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--wrapper", required=True)
    s = sub.add_parser("compare")
    s.add_argument("--wrapper", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--main-script")
    g.add_argument("--main-has-no-script", action="store_true")
    s = sub.add_parser("wrapper-ref")
    s.add_argument("--event", required=True)
    s.add_argument("--ref-type")
    s.add_argument("--body-file")
    s.add_argument("--proof-file", default=PROOF_FILE)
    s = sub.add_parser("previous-release")
    s.add_argument("--published", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--version")
    g.add_argument("--pyproject")
    s = sub.add_parser("proof-version")
    s.add_argument("--event", required=True)
    s.add_argument("--ref-type", required=True)
    s.add_argument("--ref-name", required=True)
    sub.add_parser("tree-version")
    s = sub.add_parser("read-wrapper-proof")
    s.add_argument("--file", required=True)
    s = sub.add_parser("read-series-run")
    s.add_argument("--file", required=True)
    s = sub.add_parser("write-wrapper-proof")
    s.add_argument("--plan", required=True)
    s.add_argument("--seal", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("artifact-id")
    s.add_argument("--artifacts", required=True)
    s = sub.add_parser("check-release-plan")
    s.add_argument("--file", required=True)
    s.add_argument("--seal", required=True)
    s.add_argument("--run", required=True)
    s.add_argument("--zip")
    s = sub.add_parser("check-wrapper-proof")
    s.add_argument("--file", required=True)
    s.add_argument("--wrapper", required=True)
    s.add_argument("--version", required=True)
    sub.add_parser("engine")
    s = sub.add_parser("plan")
    s.add_argument("--wrapper", required=True)
    s.add_argument("--os", required=True, choices=["linux", "windows"])
    s.add_argument("--parts", type=int, required=True)
    s.add_argument("--collect", action="store_true")
    s = sub.add_parser("run")
    s.add_argument("--wrapper", required=True)
    s.add_argument("--binary", required=True)
    s.add_argument("--os", required=True, choices=["linux", "windows"])
    s.add_argument("--part", type=int, required=True)
    s.add_argument("--parts", type=int, required=True)
    s.add_argument("--procs", default="4")
    s.add_argument("--junit-dir", default="wrapper-on-this-core-junit")
    args = p.parse_args(argv)
    return {"list": _cmd_list, "compare": _cmd_compare, "engine": _cmd_engine, "plan": _cmd_plan,
            "run": _cmd_run, "wrapper-ref": _cmd_wrapper_ref, "previous-release": _cmd_previous_release,
            "proof-version": _cmd_proof_version, "tree-version": _cmd_tree_version,
            "read-wrapper-proof": _cmd_read_wrapper_proof,
            "read-series-run": _cmd_read_series_run, "write-wrapper-proof": _cmd_write_wrapper_proof, "artifact-id": _cmd_artifact_id,
            "check-release-plan": _cmd_check_release_plan, "check-wrapper-proof": _cmd_check_wrapper_proof}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
