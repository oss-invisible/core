"""scripts/ci_regressions.py is the checker of the regression registry (rule ci-14): `check` is red on every schema problem (each field, a
duplicate id, a narrower os with no why_os), on a row lost or changed against main, on a rename with no `replaced` or with one whose old id
is still collected, on an id that pytest does not collect, on a row that no suite covers and on a gtest id that no declared file holds, and
only then; `candidate` is red on a row with no result, a failed or skipped case, and ignores a row of the other os. The test files are small
real trees in tmp_path (pytest really collects them); the junit files are written by hand. No network."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci_regressions.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _load():
    spec = importlib.util.spec_from_file_location("ci_regressions_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


reg = _load()

RULE = "Rule: ci-14 regression registry"
X = "tests/test_x.py::test_y"
P = "tests/test_x.py::test_p"
FIX = "oss-invisible/core@" + "a" * 40


def row(test=X, **kw):
    base = {"test": test, "defect": "a click was delivered twice", "fix": FIX,
            "red_on_old": "oss-invisible/core/actions/runs/123456", "red_kind": "test-on-base",
            "pr": "oss-invisible/core#12", "added": "2026-11-25"}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


@pytest.fixture
def tree(tmp_path):
    """A small repository: tests/test_x.py has test_y and a parametrized test_p; a suite file covers it."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "\n".join(["import pytest", "", "def test_y():", "    pass", "", "@pytest.mark.parametrize('v', ['a', 'b'])", "def test_p(v):",
                   "    pass", ""]), encoding="utf-8")
    (tmp_path / "tests" / "test_z.py").write_text("def test_q():\n    pass\n", encoding="utf-8")
    return tmp_path


def _write(path, data):
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def run_check(tree, capsys, rows, main=None, suites=None, declared=(), tree_root=None, root=None):
    argv = ["check", "--registry", _write(tree / "r.json", rows), "--root", str(root or tree), "--python", sys.executable]
    if main is not None:
        argv += ["--main-registry", _write(tree / "m.json", main) if main != "absent" else str(tree / "nothing.json")]
    if suites is not None:
        argv += ["--suites", _write(tree / "suites.json", suites)]
    for d in declared:
        argv += ["--declared", d]
    if tree_root is not None:
        argv += ["--tree-root", str(tree_root)]
    code = reg.main(argv)
    return code, capsys.readouterr().out


def red(out, *needles):
    lines = [ln for ln in out.splitlines() if ln.startswith("FAIL ")]
    assert lines and all(ln.endswith(RULE) and ". Fix: " in ln for ln in lines), out
    for n in needles:
        assert any(n in ln for ln in lines), (n, out)
    return lines


# -------------------------------------------------------------------- schema


def test_a_valid_registry_is_green(tree, capsys):
    code, out = run_check(tree, capsys, [row(X), row(P, os=["linux"], why_os="needs a shell")])
    assert code == 0 and out.startswith("OK ci_regressions check: 2 row(s)"), out


def test_an_empty_registry_is_green(tree, capsys):
    code, out = run_check(tree, capsys, [])
    assert code == 0 and "0 row(s)" in out


@pytest.mark.parametrize("field,value", [
    ("test", "not a test id"), ("test", 7), ("test", "gtest:NoDot"), ("test", "tests/test_x.py"),
    ("defect", ""), ("defect", "two\nlines"), ("defect", 3),
    ("fix", "oss-invisible/core@abc"), ("fix", "oss-invisible/core@" + "A" * 40), ("fix", "core@" + "a" * 40),
    ("red_on_old", "oss-invisible/core/actions/runs/x"), ("red_on_old", "https://github.com/o/r/actions/runs/1"),
    ("red_kind", "other"), ("pr", "oss-invisible/core/12"), ("pr", 12),
    ("added", "25/11/2026"), ("added", "2026-13-40"), ("os", []), ("os", ["macos"]), ("os", "linux"), ("os", ["linux", "linux"]),
    ("replaced", {"old": X, "why": "w"}), ("replaced", "x"), ("replaced", {"old": "zzz", "why": "w", "pr": "o/r#1"}),
    ("replaced", {"old": X, "why": "", "pr": "o/r#1"}), ("replaced", {"old": X, "why": "w", "pr": "o/r"}),
    ("surprise", 1),
])
def test_a_bad_field_is_red(tree, capsys, field, value):
    code, out = run_check(tree, capsys, [row(**{field: value})])
    assert code == 1
    red(out, field if field != "surprise" else "unknown field")


@pytest.mark.parametrize("field", ["defect", "fix", "red_on_old", "red_kind", "pr", "added"])
def test_a_missing_field_is_red(tree, capsys, field):
    code, out = run_check(tree, capsys, [row(**{field: None})])
    assert code == 1
    red(out, "field %r is missing" % field)


def test_every_valid_form_of_every_field_is_green(tree, capsys):
    rows = [row(X, red_on_old="oss-invisible/core/actions/runs/9/job/10", red_kind="defect-run", os=["windows", "linux"]),
            row("tree:tests/test_x.py::test_p[a]"), row("gtest:Suite.Name"), row("gtest:Suite.Name/3"), row("xpcshell:dom/x/test_a.js")]
    code, out = run_check(tree, capsys, rows)
    assert code == 0, out


def test_the_registry_that_is_not_an_array_is_red(tree, capsys):
    code, out = run_check(tree, capsys, {"test": X})
    assert code == 1
    red(out, "not a JSON array")


def test_a_registry_that_is_not_json_is_red(tree, capsys):
    (tree / "r.json").write_text("[", encoding="utf-8")
    code = reg.main(["check", "--registry", str(tree / "r.json"), "--root", str(tree)])
    assert code == 1
    red(capsys.readouterr().out, "not valid JSON")


def test_a_row_that_is_not_an_object_is_red(tree, capsys):
    code, out = run_check(tree, capsys, [X])
    assert code == 1
    red(out, "not an object")


def test_a_duplicate_id_is_red(tree, capsys):
    code, out = run_check(tree, capsys, [row(X), row(X, defect="another")])
    assert code == 1
    red(out, "appears twice")


def test_a_narrower_os_needs_a_why(tree, capsys):
    code, out = run_check(tree, capsys, [row(X, os=["linux"])])
    assert code == 1
    red(out, "'why_os' is missing")
    code, out = run_check(tree, capsys, [row(X, os=["linux"], why_os="  ")])
    assert code == 1
    red(out, "'why_os' is missing")
    code, out = run_check(tree, capsys, [row(X, os=["windows"], why_os="a Windows-only API")])
    assert code == 0, out
    code, out = run_check(tree, capsys, [row(X, os=["linux", "windows"])])
    assert code == 0, out


# -------------------------------------------------------------------- against main


def test_the_registry_that_grows_is_green(tree, capsys):
    code, out = run_check(tree, capsys, [row(X), row(P)], main=[row(X)])
    assert code == 0 and "1 row(s) of main kept" in out, out


def test_a_row_lost_against_main_is_red(tree, capsys):
    code, out = run_check(tree, capsys, [row(X)], main=[row(X), row(P)])
    assert code == 1
    red(out, "the row of %s is lost" % P)


def test_a_field_changed_against_main_is_red(tree, capsys):
    code, out = run_check(tree, capsys, [row(X, defect="changed")], main=[row(X)])
    assert code == 1
    red(out, "a row of main changed (defect)")


def test_a_field_added_against_main_is_red(tree, capsys):
    code, out = run_check(tree, capsys, [row(X, os=["linux"], why_os="x")], main=[row(X)])
    assert code == 1
    red(out, "changed (os, why_os)")


def test_a_main_registry_that_is_not_on_main_yet_is_skipped_and_said(tree, capsys):
    code, out = run_check(tree, capsys, [row(X)], main="absent")
    assert code == 0 and "no registry on main yet" in out, out


def test_a_main_registry_that_is_broken_is_red(tree, capsys):
    (tree / "m.json").write_text("{}", encoding="utf-8")
    code = reg.main(["check", "--registry", _write(tree / "r.json", [row(X)]), "--main-registry", str(tree / "m.json"),
                     "--root", str(tree), "--python", sys.executable])
    assert code == 1
    red(capsys.readouterr().out, "main registry")


def test_a_renamed_test_without_replaced_is_red(tree, capsys):
    old = "tests/test_x.py::test_gone"
    code, out = run_check(tree, capsys, [row(X)], main=[row(old)])
    assert code == 1
    red(out, "the row of %s is lost" % old)


def test_a_renamed_test_with_replaced_and_the_old_id_gone_is_green(tree, capsys):
    old = "tests/test_x.py::test_gone"
    rep = {"old": old, "why": "the test was split", "pr": "oss-invisible/core#30"}
    code, out = run_check(tree, capsys, [row(X, replaced=rep)], main=[row(old)])
    assert code == 0, out


def test_a_renamed_test_with_replaced_and_the_old_id_still_collected_is_red(tree, capsys):
    rep = {"old": P, "why": "the test was split", "pr": "oss-invisible/core#30"}
    code, out = run_check(tree, capsys, [row(X, replaced=rep)], main=[row(P)])
    assert code == 1
    red(out, "it replaces %s, and that id is still collected" % P)


def test_a_replaced_row_that_also_changes_another_field_is_red(tree, capsys):
    old = "tests/test_x.py::test_gone"
    rep = {"old": old, "why": "split", "pr": "oss-invisible/core#30"}
    code, out = run_check(tree, capsys, [row(X, replaced=rep, defect="other")], main=[row(old)])
    assert code == 1
    red(out, "also changed defect")


def test_a_replaced_old_file_that_is_gone_counts_as_gone(tree, capsys):
    old = "tests/test_deleted.py::test_old"
    rep = {"old": old, "why": "moved", "pr": "oss-invisible/core#30"}
    code, out = run_check(tree, capsys, [row(X, replaced=rep)], main=[row(old)])
    assert code == 0, out


# -------------------------------------------------------------------- collected


def test_an_uncollected_id_is_red_and_does_not_hide_the_others(tree, capsys):
    code, out = run_check(tree, capsys, [row(X), row("tests/test_x.py::test_missing"), row("tests/test_none.py::test_a")])
    assert code == 1
    lines = red(out, "test_missing", "test_none.py")
    assert len(lines) == 2 and not any("(%s)" % X in ln for ln in lines), out


def test_a_parametrized_id_is_collected_with_and_without_brackets(tree, capsys):
    code, out = run_check(tree, capsys, [row(P), row(P + "[a]")])
    assert code == 0, out
    code, out = run_check(tree, capsys, [row(P + "[zzz]")])
    assert code == 1
    red(out, "not collected")


def test_a_file_that_does_not_import_is_red(tree, capsys):
    (tree / "tests" / "test_x.py").write_text("import nothing_here_at_all\n\ndef test_y():\n    pass\n", encoding="utf-8")
    code, out = run_check(tree, capsys, [row(X)])
    assert code == 1
    red(out, "collect")


def test_a_tree_id_is_collected_in_the_tree_root_when_given(tree, capsys, tmp_path_factory):
    other = tmp_path_factory.mktemp("other")
    (other / "tests").mkdir()
    (other / "tests" / "test_w.py").write_text("def test_v():\n    pass\n", encoding="utf-8")
    code, out = run_check(tree, capsys, [row("tree:tests/test_w.py::test_v")], tree_root=other)
    assert code == 0, out
    code, out = run_check(tree, capsys, [row("tree:tests/test_w.py::test_nope")], tree_root=other)
    assert code == 1
    red(out, "other tree")
    code, out = run_check(tree, capsys, [row("tree:tests/test_w.py::test_nope")])  # no --tree-root: not checked here
    assert code == 0, out


# -------------------------------------------------------------------- gtest, xpcshell


def test_a_gtest_id_in_a_declared_file_is_green_and_absent_is_red(tree, capsys):
    decl = tree / "tests.txt"
    decl.write_text("TEST(Suite, Name)\nSuite.Name\nxpcshell: dom/x/test_a.js\n", encoding="utf-8")
    code, out = run_check(tree, capsys, [row("gtest:Suite.Name"), row("xpcshell:dom/x/test_a.js")], declared=[str(decl)])
    assert code == 0, out
    code, out = run_check(tree, capsys, [row("gtest:Suite.Other"), row("xpcshell:dom/x/test_b.js")], declared=[str(decl)])
    assert code == 1
    assert len(red(out, "Suite.Other", "test_b.js")) == 2
    code, out = run_check(tree, capsys, [row("gtest:Suite.Other")])  # no --declared: not checked
    assert code == 0, out


# -------------------------------------------------------------------- suites


SUITE = [{"name": "unit", "tests": ["tests/test_x.py"]}, {"name": "one", "tests": ["tests/test_z.py::test_q"]}]


def test_a_row_in_a_suite_is_green(tree, capsys):
    code, out = run_check(tree, capsys, [row(X), row(P + "[a]"), row("tests/test_z.py::test_q")], suites=SUITE)
    assert code == 0, out


def test_a_row_in_no_suite_is_red(tree, capsys):
    (tree / "tests" / "test_z.py").write_text("def test_q():\n    pass\n\ndef test_r():\n    pass\n", encoding="utf-8")
    code, out = run_check(tree, capsys, [row("tests/test_z.py::test_r")], suites=SUITE)
    assert code == 1
    red(out, "no round runs it")


def test_a_directory_entry_covers_the_files_under_it(tree, capsys):
    code, out = run_check(tree, capsys, [row(X)], suites=[{"tests": ["tests/"]}])
    assert code == 0, out


def test_a_broken_suites_file_is_red(tree, capsys):
    code, out = run_check(tree, capsys, [row(X)], suites={"tests": []})
    assert code == 1
    red(out, "suites file")


# -------------------------------------------------------------------- candidate


def _junit(path, cases):
    body = "".join('<testcase classname="%s" name="%s" time="0.1">%s</testcase>' % c for c in cases)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="s">%s</testsuite></testsuites>' % body,
                          encoding="utf-8")


PASS = ""
FAIL = '<failure message="f">x</failure>'
SKIP = '<skipped type="pytest.skip" message="s"/>'


def run_candidate(tmp_path, capsys, rows, *, os="linux", extra=()):
    rows_file = _write(tmp_path / "rows.json", rows)
    code = reg.main(["candidate", "--registry", rows_file, "--junit-dir", str(tmp_path / "junit"), "--os", os, *extra])
    return code, capsys.readouterr().out


def test_a_candidate_with_the_test_passed_is_green(tmp_path, capsys):
    _junit(tmp_path / "junit" / "a.xml", [("tests.test_x", "test_y", PASS)])
    code, out = run_candidate(tmp_path, capsys, [row(X)])
    assert code == 0 and out.startswith("OK ci_regressions candidate: 1 regression test(s)"), out


def test_a_candidate_that_has_no_result_is_red(tmp_path, capsys):
    _junit(tmp_path / "junit" / "a.xml", [("tests.test_x", "test_other", PASS)])
    code, out = run_candidate(tmp_path, capsys, [row(X)])
    assert code == 1
    red(out, "no result in this candidate")


def test_a_candidate_with_no_junit_is_red(tmp_path, capsys):
    (tmp_path / "junit").mkdir()
    code, out = run_candidate(tmp_path, capsys, [row(X)])
    assert code == 1
    red(out, "no junit file")


def test_a_failure_in_one_junit_and_a_pass_in_another_is_red(tmp_path, capsys):
    _junit(tmp_path / "junit" / "a.xml", [("tests.test_x", "test_y", PASS)])
    _junit(tmp_path / "junit" / "sub" / "b.xml", [("tests.test_x", "test_y", FAIL)])
    code, out = run_candidate(tmp_path, capsys, [row(X)])
    assert code == 1
    red(out, "did not pass", "failed")


def test_a_skipped_test_is_red(tmp_path, capsys):
    _junit(tmp_path / "junit" / "a.xml", [("tests.test_x", "test_y", SKIP)])
    code, out = run_candidate(tmp_path, capsys, [row(X)])
    assert code == 1
    red(out, "skipped")


def test_a_row_for_windows_only_is_ignored_on_linux_and_judged_on_windows(tmp_path, capsys):
    _junit(tmp_path / "junit" / "a.xml", [("tests.test_x", "test_other", PASS)])
    only = row(X, os=["windows"], why_os="a Windows-only API")
    code, out = run_candidate(tmp_path, capsys, [only], os="linux")
    assert code == 0 and "0 regression test(s)" in out, out
    code, out = run_candidate(tmp_path, capsys, [only], os="windows")
    assert code == 1
    red(out, "no result")


def test_a_parametrized_row_needs_every_case_to_pass(tmp_path, capsys):
    _junit(tmp_path / "junit" / "a.xml", [("tests.test_x", "test_p[a]", PASS), ("tests.test_x", "test_p[b]", PASS)])
    code, out = run_candidate(tmp_path, capsys, [row(P)])
    assert code == 0, out
    _junit(tmp_path / "junit" / "a.xml", [("tests.test_x", "test_p[a]", PASS), ("tests.test_x", "test_p[b]", FAIL)])
    code, out = run_candidate(tmp_path, capsys, [row(P)])
    assert code == 1
    red(out, "failed")


def test_a_gtest_and_an_xpcshell_row_match_the_junit_names(tmp_path, capsys):
    _junit(tmp_path / "junit" / "a.xml", [("Suite", "Name", PASS), ("xpcshell", "dom/x/test_a.js", PASS), ("Suite", "Bad", FAIL)])
    rows = [row("gtest:Suite.Name"), row("xpcshell:dom/x/test_a.js")]
    code, out = run_candidate(tmp_path, capsys, rows)
    assert code == 0, out
    code, out = run_candidate(tmp_path, capsys, rows + [row("gtest:Suite.Bad"), row("gtest:Suite.Absent")])
    assert code == 1
    assert len(red(out, "Suite.Bad", "Suite.Absent")) == 2


def test_a_registry_prefix_maps_its_rows_to_a_folder_of_the_junit(tmp_path, capsys):
    """The prefix belongs to the registry the row sits in, not to the repository of its fix: an engine fix proven by a wrapper test."""
    _junit(tmp_path / "junit" / "a.xml", [("wrapper.tests.test_x", "test_y", PASS)])
    other_fix = row(X, fix="oss-invisible/engine@" + "b" * 40)
    rows_file = _write(tmp_path / "rows.json", [other_fix])
    base = ["candidate", "--junit-dir", str(tmp_path / "junit"), "--os", "linux"]
    assert reg.main(base + ["--registry", rows_file]) == 1
    red(capsys.readouterr().out, "no result")
    assert reg.main(base + ["--registry", rows_file + "=wrapper/"]) == 0
    capsys.readouterr()
    assert reg.main(base + ["--registry", rows_file + "=core/"]) == 1  # another registry's prefix does not apply
    capsys.readouterr()


def test_a_tree_row_is_looked_up_without_its_prefix(tmp_path, capsys):
    _junit(tmp_path / "junit" / "a.xml", [("tests.test_w", "test_v", PASS)])
    code, out = run_candidate(tmp_path, capsys, [row("tree:tests/test_w.py::test_v")])
    assert code == 0, out


def test_a_candidate_reads_several_registries(tmp_path, capsys):
    _junit(tmp_path / "junit" / "a.xml", [("tests.test_x", "test_y", PASS)])
    a = _write(tmp_path / "a.json", [row(X)])
    b = _write(tmp_path / "b.json", [row("tests/test_z.py::test_q")])
    code = reg.main(["candidate", "--registry", a, "--registry", b, "--junit-dir", str(tmp_path / "junit"), "--os", "linux"])
    out = capsys.readouterr().out
    assert code == 1
    red(out, "test_q")


# -------------------------------------------------------------------- usage


def test_a_bad_usage_exits_two(capsys):
    for argv in ([], ["check"], ["candidate", "--registry", "r", "--junit-dir", "d", "--os", "macos"],
                 ["candidate", "--registry", "=wrapper/", "--junit-dir", "d", "--os", "linux"], ["nothing"]):
        with pytest.raises(SystemExit) as exc:
            reg.main(argv)
        assert exc.value.code == 2
    capsys.readouterr()


def test_the_real_registry_of_the_repository_is_valid():
    rows, problems = reg.load_registry(str(ROOT / "ci" / "regressions.json"))
    assert rows is not None and problems == []


# -------------------------------------------------------------------- the workflow


def _tests_job():
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("\n  tests:\n")
    return text[start:text.index("\n  msix-host:", start)]


def test_the_required_tests_job_runs_the_registry_check_from_main_on_linux_only():
    job = _tests_job()
    assert "name: regression registry (ci-14)" in job
    step = job[job.index("name: regression registry (ci-14)"):]
    step = step[:step.index("\n      # ")]  # up to the comment of the next step
    assert "matrix.os == 'ubuntu-latest' && matrix.python == '3.12'" in step, "the check must run once, on Linux"
    assert "git show origin/main:scripts/ci_regressions.py" in step and "git show origin/main:ci/regressions.json" in step
    assert "git show origin/main:scripts/ci_skips.py" in step, "the checker imports ci_skips from its own folder"
    assert "ci_regressions.py\" check" in step and "--registry ci/regressions.json" in step and "--main-registry" in step
    assert "--suites" not in step, "the core has no suite registry file"
    assert "falling back to the copy of this branch" in step, "the fallback must be said"
    assert not (ROOT / "ci" / "required-suites.json").exists()
    assert "needs.tests.result" in WORKFLOW.read_text(encoding="utf-8").split("\n  gate:\n", 1)[1], "tests is part of the verdict"
