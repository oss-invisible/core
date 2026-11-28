#!/usr/bin/env python3
"""CI regressions - the registry of regression tests and its checker (stdlib only, Python >= 3.11).

Rule ci-14: every defect that was seen once is closed by a test that failed on the old code, and that test is on a list nobody can
shorten. Each repository has its own registry (the wrapper and the core: ci/regressions.json; the series: tools/regressions.json), a JSON
array with one row per regression test, in the repository where the test lives (its fix may be in another one). The checker is shared:
every repository reads it from the core's main, with its own registry of main, so the judge is never the branch being judged.

A row:

    {"test": "<id>", "os": ["linux", "windows"], "why_os": "<why a narrower list>", "defect": "<one line>",
     "fix": "<owner>/<repo>@<40 hex>", "red_on_old": "<owner>/<repo>/actions/runs/<digits>[/job/<digits>]",
     "red_kind": "test-on-base" | "defect-run", "pr": "<owner>/<repo>#<digits>", "added": "YYYY-MM-DD",
     "replaced": {"old": "<old id>", "why": "<one line>", "pr": "<owner>/<repo>#<digits>"}}

`os` and `why_os` and `replaced` are optional; `os` defaults to both, and a list narrower than both needs a non-empty `why_os`.
A test id is one of: a pytest node id (`tests/test_x.py::test_y`, a parametrized `[..]` allowed), `tree:<path>::<name>` (pytest in
another tree), `gtest:<Suite>.<Name>`, `xpcshell:<path>`.

    ci_regressions.py check --registry R [--main-registry M] [--root DIR] [--suites FILE] [--python PY] [--tree-root DIR]
                            [--declared FILE ...]
        schema (every field, no duplicate id); the registry only grows against M (a row of M is in R with every field equal; the one
        exception is a row whose `test` changed, when R's row has `replaced.old` equal to M's test, every other field equal, and the old
        id is no longer collected - only pytest ids and tree: ids can be checked for that, the other kinds are accepted); when M does
        not exist (the file is not on main yet) the check says so in its OK line and skips this; every pytest id is collected by ONE
        `PY -m pytest --collect-only` run from --root (a parametrized row without brackets matches its cases; when pytest aborts on ids
        it does not know, those ids are red and the run is repeated for the rest, so one problem never hides another); tree: ids are
        collected the same way in --tree-root when it is given; gtest: and xpcshell: ids (the text after the prefix) must appear as a
        substring of at least one --declared file when any is given; when --suites is given (rows with a "tests" list of files or node
        ids, like ci/required-suites.json) every pytest row must be covered by an entry of some suite: the file itself, a node id that
        is a prefix of the row (the row itself, or followed by `::` or `[`), or a directory entry ending with `/`.

    ci_regressions.py candidate --registry PATH[=PREFIX] [--registry ...] --junit-dir DIR --os linux|windows [--due-in OWNER/REPO=GITDIR ...]
        every row whose `os` holds --os must have a result in the junit files under DIR (every *.xml, recursively), and every case of
        it must have passed. A pytest id matches the node id of a case (a row without brackets also matches the cases
        `<id>[...]`); a tree: id matches after the prefix is cut; `gtest:Suite.Name` and `xpcshell:path` match a testcase whose
        `<classname>.<name>` or whose `name` equals the text after the prefix. --registry PATH=PREFIX: every pytest or tree: id of
        that registry is looked up as PREFIX + id, wherever its fix is (the junit of a series that runs several trees names the files
        under a folder). A missing result, a failure, a skip or a rerun is red.

        --due-in names, for each repository, the checkout (GITDIR, a git repository) of the commit this run uses: the core at its ref, the
        wrapper at the commit of the plan, the series at its sha. A row is DUE when its `fix` (owner/repo@sha) is equal to, or an ancestor of,
        HEAD of the GITDIR given for that owner/repo (`git merge-base --is-ancestor`); a due row is judged exactly as above. A row that is not
        due is printed by name ("<test>: not due in this run: <repo> at <sha12> does not contain the fix <sha12>"), is never counted as
        passed and does not make the run red: the run does not contain the code that the test proves. What cannot be decided is red, never
        "not due": --due-in is used and the row's repository has none; the GITDIR is not a git repository or has no HEAD; the fix commit does
        not exist in its repository (a typo, a lost commit); the clone is shallow (`git rev-parse --is-shallow-repository`) and does not
        hold the fix, or holds it without it being an ancestor of HEAD (a shallow history proves an ancestry, never its absence).
        Without --due-in every row is due, as before.

Every problem prints one line and the exit is 1; one `OK` line and exit 0 otherwise; a bad usage exits 2:

    FAIL <where>: <what>. Fix: <how>. Rule: ci-14 regression registry
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_skips  # noqa: E402 - the judge's reader, from this file's own folder

RULE = "ci-14 regression registry"
OSES = ("linux", "windows")
KEYS = {"test", "os", "why_os", "defect", "fix", "red_on_old", "red_kind", "pr", "added", "replaced"}
REQUIRED = ("test", "defect", "fix", "red_on_old", "red_kind", "pr", "added")
RED_KINDS = ("test-on-base", "defect-run")
_PYTEST = re.compile(r"^[\w./-]+\.py::\S+$")
_TREE = re.compile(r"^tree:[\w./-]+\.py::\S+$")
_GTEST = re.compile(r"^gtest:\w+\.[\w/]+$")
_XPC = re.compile(r"^xpcshell:[\w./-]+$")
_FIX = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
_RUN = re.compile(r"^[\w.-]+/[\w.-]+/actions/runs/\d+(/job/\d+)?$")
_PR = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _line(where: str, what: str, fix: str) -> str:
    return "FAIL %s: %s. Fix: %s. Rule: %s" % (where, what, fix, RULE)


def kind_of(test: object) -> str | None:
    if not isinstance(test, str):
        return None
    if _PYTEST.match(test):
        return "pytest"
    for name, rx in (("tree", _TREE), ("gtest", _GTEST), ("xpcshell", _XPC)):
        if rx.match(test):
            return name
    return None


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value


# ---------------------------------------------------------------- the registry


def validate_row(row: object, where: str) -> list[str]:
    if not isinstance(row, dict):
        return [_line(where, "the row is not an object", "write the row as a JSON object with the fields of the format")]
    out: list[str] = []
    for key in sorted(set(row) - KEYS):
        out.append(_line(where, "unknown field %r" % key, "remove it; the format has only %s" % ", ".join(sorted(KEYS))))
    for key in REQUIRED:
        if key not in row:
            out.append(_line(where, "field %r is missing" % key, "add it"))
    test = row.get("test")
    if "test" in row and kind_of(test) is None:
        out.append(_line(where, "field 'test' is not a test id (%r)" % (test,),
                         "use tests/test_x.py::test_y, tree:<path>::<name>, gtest:<Suite>.<Name> or xpcshell:<path>"))
    if "defect" in row and not _text(row["defect"]):
        out.append(_line(where, "field 'defect' is empty or not one line", "write the defect in one line"))
    for key, rx, form in (("fix", _FIX, "<owner>/<repo>@<40 hex>"), ("red_on_old", _RUN, "<owner>/<repo>/actions/runs/<digits>[/job/<digits>]"),
                          ("pr", _PR, "<owner>/<repo>#<digits>")):
        if key in row and not (isinstance(row[key], str) and rx.match(row[key])):
            out.append(_line(where, "field %r is not of the form %s (%r)" % (key, form, row[key]), "write it as %s" % form))
    if "red_kind" in row and row["red_kind"] not in RED_KINDS:
        out.append(_line(where, "field 'red_kind' is %r" % (row["red_kind"],), "use one of %s" % ", ".join(RED_KINDS)))
    if "added" in row:
        added = row["added"]
        ok = isinstance(added, str) and bool(_DATE.match(added))
        if ok:
            try:
                datetime.date.fromisoformat(added)
            except ValueError:
                ok = False
        if not ok:
            out.append(_line(where, "field 'added' is not a date YYYY-MM-DD (%r)" % (added,), "write the day the row was added"))
    both = True
    if "os" in row:
        value = row["os"]
        if not (isinstance(value, list) and value and all(v in OSES for v in value) and len(set(value)) == len(value)):
            out.append(_line(where, "field 'os' is not a non-empty list of linux and windows (%r)" % (value,),
                             "write [\"linux\", \"windows\"] or one of them"))
            both = True
        else:
            both = set(value) == set(OSES)
    if not both and not _text(row.get("why_os")):
        out.append(_line(where, "'os' is narrower than linux and windows and 'why_os' is missing or empty",
                         "write in 'why_os' why the test cannot run on the other system"))
    if "why_os" in row and not isinstance(row["why_os"], str):
        out.append(_line(where, "field 'why_os' is not a string", "write a one-line reason"))
    if "replaced" in row:
        rep = row["replaced"]
        if not (isinstance(rep, dict) and set(rep) == {"old", "why", "pr"}):
            out.append(_line(where, "field 'replaced' is not an object with old, why and pr", "write {\"old\": ..., \"why\": ..., \"pr\": ...}"))
        else:
            if kind_of(rep["old"]) is None:
                out.append(_line(where, "replaced.old is not a test id (%r)" % (rep["old"],), "write the id the row had before"))
            if not _text(rep["why"]):
                out.append(_line(where, "replaced.why is empty or not one line", "say in one line why the test was replaced"))
            if not (isinstance(rep["pr"], str) and _PR.match(rep["pr"])):
                out.append(_line(where, "replaced.pr is not of the form <owner>/<repo>#<digits>", "write the pull request that replaced it"))
    return out


def load_registry(path: str, label: str | None = None) -> tuple[list[dict] | None, list[str]]:
    """(rows, problems); rows is None when the file cannot be used at all."""
    label = label or str(path)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [_line(label, "the registry is missing or not valid JSON (%s)" % exc, "it must be a JSON array of rows")]
    if not isinstance(data, list):
        return None, [_line(label, "the registry is not a JSON array", "write one row per regression test inside [ ]")]
    problems: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(data, 1):
        name = row.get("test") if isinstance(row, dict) else None
        where = "%s row %d%s" % (label, i, " (%s)" % name if isinstance(name, str) else "")
        problems.extend(validate_row(row, where))
        if isinstance(name, str):
            if name in seen:
                problems.append(_line(where, "the test id appears twice", "keep one row per test"))
            seen.add(name)
    return [r for r in data if isinstance(r, dict)], problems


def _oses(row: dict) -> list[str]:
    value = row.get("os")
    return list(value) if isinstance(value, list) and value else list(OSES)


# ---------------------------------------------------------------- collection


def _split(test: str) -> str:
    """The id a pytest run takes: the id itself, or what follows `tree:`."""
    return test[len("tree:"):] if test.startswith("tree:") else test


def _matches_collected(test: str, collected: set[str]) -> bool:
    return test in collected or ("[" not in test and any(c.startswith(test + "[") for c in collected))


def _run_collect(args: list[str], python: str, cwd: Path) -> subprocess.CompletedProcess | None:
    cmd = [python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "--rootdir", str(cwd), *args]
    try:
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError:
        return None


def collect(ids: list[str], python: str, cwd: Path) -> tuple[set[str], set[str], list[str]]:
    """(the node ids collected, the ids pytest reported as not found, errors).

    ONE run `python -m pytest --collect-only -q` for all the ids. pytest aborts the whole run when it does not know one of them
    (`ERROR: not found: ...`, exit 4): those ids are noted and the run is repeated without them, so the others are still judged.
    pytest prints a parametrized id without its [..]: the bracketed ids of that test are then run one by one."""
    args = sorted(set(ids))
    missing: set[str] = set()
    found: set[str] = set()
    errors: list[str] = []
    for _ in range(len(ids) + 2):
        if not args:
            return found, missing, errors
        run = _run_collect(args, python, cwd)
        if run is None:
            return found, missing, errors + ["could not run %s" % python]
        text = run.stdout + chr(10) + run.stderr
        if run.returncode in (0, 5):
            found |= {ln.strip() for ln in run.stdout.splitlines() if re.match(r"^[^\s:]+\.py::\S", ln.strip())}
            return found, missing, errors
        gone: set[str] = set()
        suspects: set[str] = set()
        for ln in text.splitlines():
            m = re.match(r"^ERROR: (?:file or directory )?not found: (.+)$", ln.strip())
            if not m:
                continue
            tail = m.group(1).replace("\\", "/")
            for a in args:
                if tail == a or tail.endswith("/" + a):
                    gone.add(a)
                elif "[" in a and (tail == a.split("[", 1)[0] or tail.endswith("/" + a.split("[", 1)[0])):
                    suspects.add(a)
        if not gone and not suspects:
            last = [ln for ln in text.splitlines() if ln.strip()][-3:]
            return found, missing, errors + ["pytest could not collect (exit %d): %s" % (run.returncode, " | ".join(last))]
        for a in sorted(suspects - gone):
            alone = _run_collect([a], python, cwd)
            if alone is not None and alone.returncode in (0, 5):
                found |= {ln.strip() for ln in alone.stdout.splitlines() if re.match(r"^[^\s:]+\.py::\S", ln.strip())}
            else:
                gone.add(a)
        missing |= gone
        args = [a for a in args if a not in gone and a not in suspects]
    return found, missing, errors



def _file_gone(test: str, root: Path) -> bool:
    return not (root / _split(test).split("::", 1)[0]).is_file()


def collect_state(tests: list[str], python: str, root: Path) -> tuple[set[str], set[str], list[str]]:
    """collect() for ids whose file exists under root; an id whose file is gone is not found without asking pytest."""
    present = [t for t in tests if not _file_gone(t, root)]
    gone = {t for t in tests if _file_gone(t, root)}
    collected, missing, errors = collect(present, python, root)
    return collected, missing | gone, errors


# ---------------------------------------------------------------- check


def _suite_covers(entry: str, test: str) -> bool:
    file = test.split("::", 1)[0]
    if entry == file or entry == test:
        return True
    if entry.endswith("/"):
        return file.startswith(entry)
    return test.startswith(entry + "::") or test.startswith(entry + "[")


def check(registry: str, main_registry: str | None, root: Path, suites: str | None, python: str, tree_root: Path | None,
          declared: list[str]) -> tuple[list[str], str]:
    rows, problems = load_registry(registry)
    notes: list[str] = []
    if rows is None:
        return problems, ""
    by_test = {r["test"]: r for r in rows if isinstance(r.get("test"), str)}

    # 1. only grows against main
    renames: list[tuple[str, str]] = []  # (old id, new id) accepted subject to the old id being gone
    if main_registry is not None:
        if not Path(main_registry).exists():
            notes.append("no registry on main yet")
        else:
            old_rows, old_problems = load_registry(main_registry, "main registry")
            if old_rows is None:
                problems.extend(old_problems)
            else:
                notes.append("%d row(s) of main kept" % len(old_rows))
                for old in old_rows:
                    test = old.get("test")
                    if not isinstance(test, str):
                        continue
                    new = by_test.get(test)
                    if new is not None:
                        if new != old:
                            changed = sorted(k for k in set(new) | set(old) if new.get(k) != old.get(k))
                            problems.append(_line("%s row (%s)" % (registry, test), "a row of main changed (%s)" % ", ".join(changed),
                                                  "the registry only grows: restore the fields; a replaced test is a new row with 'replaced'"))
                        continue
                    heir = next((r for r in rows if isinstance(r.get("replaced"), dict) and r["replaced"].get("old") == test), None)
                    if heir is None:
                        problems.append(_line("%s" % registry, "the row of %s is lost against main" % test,
                                              "put the row back; a test that was renamed or replaced keeps its row with 'replaced'"))
                        continue
                    a = {k: v for k, v in old.items() if k not in ("test", "replaced")}
                    b = {k: v for k, v in heir.items() if k not in ("test", "replaced")}
                    if a != b:
                        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
                        problems.append(_line("%s row (%s)" % (registry, heir["test"]),
                                              "the row that replaces %s also changed %s" % (test, ", ".join(changed)),
                                              "a replacement changes only 'test' and 'replaced'"))
                        continue
                    renames.append((test, heir["test"]))

    # 2. collected
    pytest_ids = [t for t in by_test if kind_of(t) == "pytest"]
    tree_ids = [t for t in by_test if kind_of(t) == "tree"]
    old_pytest = [o for o, _ in renames if kind_of(o) == "pytest"]
    if pytest_ids or old_pytest:
        collected, missing, errors = collect_state(pytest_ids + old_pytest, python, root)
        for err in errors:
            problems.append(_line("collect", err, "fix the error pytest printed"))
        for t in pytest_ids:
            if not errors and (t in missing or not _matches_collected(t, collected)):
                problems.append(_line("%s row (%s)" % (registry, t), "the test is not collected by pytest",
                                      "the id must name a test that exists; fix the id or restore the test"))
        for old, new in renames:
            if kind_of(old) == "pytest" and not errors and old not in missing and _matches_collected(old, collected):
                problems.append(_line("%s row (%s)" % (registry, new), "it replaces %s, and that id is still collected" % old,
                                      "delete the old test, or keep the old row and add the new one as a new row"))
    if tree_ids and tree_root is not None:
        ids = [_split(t) for t in tree_ids]
        collected, missing, errors = collect_state(ids, python, tree_root)
        for err in errors:
            problems.append(_line("collect (tree)", err, "fix the error pytest printed"))
        for t in tree_ids:
            if not errors and (_split(t) in missing or not _matches_collected(_split(t), collected)):
                problems.append(_line("%s row (%s)" % (registry, t), "the test is not collected in the other tree",
                                      "the path after tree: must name a test of --tree-root"))
    for old, new in renames:
        if kind_of(old) == "tree" and tree_root is not None:
            collected, missing, errors = collect_state([_split(old)], python, tree_root)
            if not errors and _split(old) not in missing and _matches_collected(_split(old), collected):
                problems.append(_line("%s row (%s)" % (registry, new), "it replaces %s, and that id is still collected" % old,
                                      "delete the old test, or keep the old row and add the new one as a new row"))

    # gtest / xpcshell ids against the declared files
    if declared:
        texts: list[str] = []
        for d in declared:
            try:
                texts.append(Path(d).read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                problems.append(_line(str(d), "the declared file is not readable (%s)" % exc, "pass a file that exists"))
        for t in by_test:
            if kind_of(t) in ("gtest", "xpcshell"):
                needle = t.split(":", 1)[1]
                if not any(needle in text for text in texts):
                    problems.append(_line("%s row (%s)" % (registry, t), "the id is in none of the declared files",
                                          "the test must be declared where the engine's tests are listed"))

    # 3. in a suite
    if suites is not None:
        try:
            data = json.loads(Path(suites).read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("not a JSON array")
            entries = [e for s in data if isinstance(s, dict) for e in (s.get("tests") or []) if isinstance(e, str)]
        except (OSError, ValueError) as exc:
            problems.append(_line(str(suites), "the suites file is missing or unusable (%s)" % exc, "it must be a JSON array of suites with a 'tests' list"))
            entries = None
        if entries is not None:
            for t in pytest_ids:
                if not any(_suite_covers(e, t) for e in entries):
                    problems.append(_line("%s row (%s)" % (registry, t), "the test is in no suite: no round runs it",
                                          "add its file to a suite of %s" % suites))

    summary = "%d row(s)" % len(rows)
    if notes:
        summary += "; " + "; ".join(notes)
    return problems, summary


# ---------------------------------------------------------------- candidate


def split_registry(item: str) -> tuple[str, str]:
    """`PATH` or `PATH=PREFIX` (the last `=` splits) -> (path, prefix)."""
    path, eq, prefix = item.rpartition("=")
    return (path, prefix) if eq else (item, "")


def _git(gitdir: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", gitdir, *args], capture_output=True, text=True, timeout=120)


def due_state(fix: str, due_in: dict[str, str]) -> tuple[bool | None, str]:
    """(True, "") when the row is due; (False, note) when it is not; (None, reason) when it cannot be decided (red)."""
    repo, _, sha = fix.partition("@")
    gitdir = due_in.get(repo)
    if gitdir is None:
        return None, "no --due-in for %s, the repository of its fix %s" % (repo, sha[:12])
    head = _git(gitdir, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    if head.returncode != 0:
        return None, "--due-in %s=%s is not a git repository with a HEAD" % (repo, gitdir)
    head_sha = head.stdout.strip()
    shallow = _git(gitdir, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    if _git(gitdir, "cat-file", "-e", sha + "^{commit}").returncode != 0:
        if shallow:
            return None, "%s at %s is a shallow clone that does not hold the fix %s: it cannot be decided whether the run contains it" % (repo, head_sha[:12], sha[:12])
        return None, "the fix %s does not exist in %s: a typo or a lost commit" % (sha[:12], repo)
    ancestry = _git(gitdir, "merge-base", "--is-ancestor", sha, head_sha).returncode
    if ancestry == 0:
        return True, ""
    if ancestry == 1 and not shallow:
        return False, "%s at %s does not contain the fix %s" % (repo, head_sha[:12], sha[:12])
    if ancestry == 1:
        return None, "%s at %s is a shallow clone: its history cannot say that it lacks the fix %s" % (repo, head_sha[:12], sha[:12])
    return None, "the ancestry of the fix %s in %s cannot be read (git exit %d)" % (sha[:12], repo, ancestry)


def parse_due_in(items: list[str]) -> dict[str, str]:
    """`OWNER/REPO=GITDIR` (repeatable) -> {repo: gitdir}; raises ValueError on a malformed or repeated item."""
    due: dict[str, str] = {}
    for item in items:
        repo, eq, gitdir = item.partition("=")
        if not eq or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not gitdir:
            raise ValueError("--due-in must look like OWNER/REPO=GITDIR, not %r" % item)
        if repo in due:
            raise ValueError("--due-in names %s twice" % repo)
        due[repo] = gitdir
    return due


def candidate(registries: list[str], junit_dir: str, os_name: str, due_in: dict[str, str] | None = None,
              notes: list[str] | None = None) -> tuple[list[str], str]:
    problems: list[str] = []
    not_due = 0
    rows: list[tuple[dict, str]] = []  # (row, the prefix of the registry it sits in)
    for item in registries:
        path, prefix = split_registry(item)
        loaded, probs = load_registry(path)
        problems.extend(probs)
        rows.extend((r, prefix) for r in loaded or [])
    root = Path.cwd()
    cases: list[ci_skips.Case] = []
    xmls = sorted(Path(junit_dir).rglob("*.xml")) if Path(junit_dir).is_dir() else []
    if not xmls:
        problems.append(_line(str(junit_dir), "no junit file under the folder", "the candidate must write its junit files there and not skip the step"))
    for path in xmls:
        try:
            tree_root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            problems.append(_line(str(path), "the junit is missing or not readable (%s)" % exc, "the leg must write it whole"))
            continue
        for element in tree_root.iter("testcase"):
            case = ci_skips.Case(element, root, str(path))
            case.classname = element.get("classname", "")
            cases.append(case)
    groups = ci_skips.group_by_nodeid(cases)

    judged = 0
    for row, prefix in rows:
        test = row.get("test")
        kind = kind_of(test)
        if kind is None or os_name not in _oses(row):
            continue
        if due_in is not None:
            due, why = due_state(str(row.get("fix", "")), due_in)
            if due is None:
                problems.append(_line("%s (os %s)" % (test, os_name), "it cannot be decided whether the run contains its fix: " + why,
                                      "give --due-in the checkout of the commit this run uses, a full clone, and fix the sha of the row"))
                continue
            if not due:
                not_due += 1
                if notes is not None:
                    notes.append("%s: not due in this run: %s" % (test, why))
                continue
        judged += 1
        found: list[ci_skips.Case]
        if kind in ("pytest", "tree"):
            wanted = prefix + _split(test)
            found = list(groups.get(wanted, []))
            if "[" not in wanted:
                found += [c for nodeid, g in groups.items() if nodeid.startswith(wanted + "[") for c in g]
        else:
            name = test.split(":", 1)[1]
            found = [c for c in cases if "%s.%s" % (c.classname, c.name) == name or c.name == name]
        where = "%s (os %s)" % (test, os_name)
        if not found:
            problems.append(_line(where, "no result in this candidate", "the round that runs this test must run in the candidate and write its junit under the folder"))
            continue
        bad = [c for c in found if not c.passed or c.rerun]
        if bad:
            first = bad[0]
            problems.append(_line(where, "it did not pass in this candidate (%s, %s)" % (first.outcome, first.source),
                                  "a regression test must pass: fix the code, not the test or the registry"))
    summary = "%d regression test(s) passed for %s in %d junit file(s)" % (judged, os_name, len(xmls))
    if due_in is not None:
        summary += "; %d not due in this run (listed above, not counted)" % not_due
    return problems, summary


# ---------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ci_regressions.py", description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("check")
    c.add_argument("--registry", required=True)
    c.add_argument("--main-registry", default=None)
    c.add_argument("--root", type=Path, default=None)
    c.add_argument("--suites", default=None)
    c.add_argument("--python", default=sys.executable)
    c.add_argument("--tree-root", type=Path, default=None)
    c.add_argument("--declared", action="append", default=[])
    k = sub.add_parser("candidate")
    k.add_argument("--registry", action="append", required=True, help="PATH or PATH=PREFIX")
    k.add_argument("--junit-dir", required=True)
    k.add_argument("--os", dest="os_name", required=True, choices=OSES)
    k.add_argument("--due-in", action="append", default=None, metavar="OWNER/REPO=GITDIR",
                   help="the checkout of the commit this run uses for that repository (repeatable); a row whose fix it does not contain is not due")
    args = ap.parse_args(argv)
    if args.mode == "check":
        root = args.root if args.root is not None else Path.cwd()
        problems, summary = check(args.registry, args.main_registry, root, args.suites, args.python, args.tree_root, args.declared)
        ok = "OK ci_regressions check: %s" % summary
    else:
        if any(not split_registry(item)[0] for item in args.registry):
            ap.error("--registry must look like PATH or PATH=PREFIX")
        try:
            due_in = parse_due_in(args.due_in) if args.due_in is not None else None
        except ValueError as exc:
            ap.error(str(exc))
        notes: list[str] = []
        problems, summary = candidate(args.registry, args.junit_dir, args.os_name, due_in, notes)
        for line in notes:
            print(line)
        ok = "OK ci_regressions candidate: %s" % summary
    for line in problems:
        print(line)
    if problems:
        return 1
    print(ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
