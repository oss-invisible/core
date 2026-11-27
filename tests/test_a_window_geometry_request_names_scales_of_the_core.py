"""captures/WINDOW_GEOMETRY_REQUEST.json says which release, which display scales, how many launches and which resolutions.

    {"firefox_version": "159.0", "dpr": [1, 1.25, 1.5, 2], "launches": ["a", "b"], "resolutions": [[1920, 1080], [1600, 1200], [2560, 1440]], "round": 6}

`round` is a positive integer, raised to make the workflow run again. A scale must be a key of invisible_core WINDOW_FRAME_BY_DPR (the judge has a row to compare with only there), the launches
are at least two distinct letters (the judge compares them), and the version is a release. scripts/retail/window_geometry/
request.py refuses anything else in the first job, before a Windows runner is started. The table is read from the source of
the checkout by frame_table.py (constants.py loaded by file path, not installed), and must be what the package itself declares.
"""
from __future__ import annotations

import json

import pytest

from _retail_scripts import ROOT, load_window_geometry

pytestmark = pytest.mark.unit

req = load_window_geometry("request")
frame_table = load_window_geometry("frame_table")
GOOD = {"firefox_version": "159.0", "dpr": [1, 1.25, 1.5, 2], "launches": ["a", "b"],
        "resolutions": [[1920, 1080], [1600, 1200], [2560, 1440]], "round": 6}


def test_the_committed_request_is_valid_and_is_the_four_scales_of_159_in_round_6():
    committed = json.loads((ROOT / "captures" / "WINDOW_GEOMETRY_REQUEST.json").read_text(encoding="utf-8"))
    assert committed == GOOD
    assert req.load(ROOT / "captures" / "WINDOW_GEOMETRY_REQUEST.json") == GOOD
    assert req.labels(GOOD) == ["1", "1.25", "1.5", "2"]


def test_the_table_read_from_the_source_is_the_table_of_the_package():
    from invisible_core.constants import WINDOW_FRAME_BY_DPR, declared_maximized_ui

    read = frame_table.load()
    assert set(read) == set(WINDOW_FRAME_BY_DPR)
    for dpr, row in WINDOW_FRAME_BY_DPR.items():
        assert read[dpr] == row
        for avail in (400, 600, 700, 719, 720, 800, 1032):
            assert frame_table.declared_maximized_ui(read[dpr], avail) == declared_maximized_ui(row, avail)


def test_the_source_is_read_by_file_path_without_importing_the_package(tmp_path):
    """The judge job has a checkout and Python: constants.py is executed from its path, its siblings under a private name."""
    import subprocess
    import sys

    code = ("import sys; sys.path.insert(0, %r); import frame_table; t = frame_table.load(); "
            "assert 'invisible_core' not in sys.modules, 'the package was imported'; "
            "print(sorted(t), frame_table.declared_maximized_ui(t[1.5], 672))") % str(ROOT / "scripts" / "retail" / "window_geometry")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=tmp_path, check=True)
    assert out.stdout.strip() == "[1.0, 1.25, 1.5, 2.0] 109"


def test_the_command_line_prints_the_version_and_the_job_matrix(tmp_path, capsys):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(GOOD), encoding="utf-8")
    assert req.main([str(path)]) == 0
    assert capsys.readouterr().out.splitlines() == ["firefox_version=159.0", 'dpr=["1","1.25","1.5","2"]']
    path.write_text(json.dumps(dict(GOOD, dpr=[3])), encoding="utf-8")
    assert req.main([str(path)]) == 2
    assert "dpr" in capsys.readouterr().err


@pytest.mark.parametrize("subset", [[1], [2], [1.25, 1.5], [1.0, 2.0]])
def test_a_subset_of_the_scales_of_the_table_is_accepted(subset):
    assert req.validate(dict(GOOD, dpr=subset))["dpr"] == subset


@pytest.mark.parametrize("dpr", [[1.75], [3], [0], [-1], [True], ["1"], [None], [[1]], [1, 1], [1, 1.0], [], "1", None])
def test_a_scale_that_is_not_in_the_table_is_refused(dpr):
    with pytest.raises(req.RequestError, match="dpr"):
        req.validate(dict(GOOD, dpr=dpr))


@pytest.mark.parametrize("version", ["159", "159.0b1", "159.0esr", "v159.0", "../159.0", "", None, 159.0])
def test_anything_but_a_release_version_is_refused(version):
    with pytest.raises(req.RequestError, match="firefox_version"):
        req.validate(dict(GOOD, firefox_version=version))


@pytest.mark.parametrize("launches", [["a"], [], ["a", "a"], ["a", "B"], ["a", "bb"], ["a", 1], ["a", "p"], ["a", "q"], ["a", "m"], "ab", None])
def test_launches_are_at_least_two_distinct_letters_and_not_a_reserved_one(launches):
    with pytest.raises(req.RequestError, match="launches"):
        req.validate(dict(GOOD, launches=launches))


@pytest.mark.parametrize("resolutions", [[], [[1920]], [[1920, 1080, 1]], [[0, 1080]], [[-1, 5]], [[1920.5, 1080]], [["1920", "1080"]],
                                         [[True, 1]], [[1920, 1080], [1920, 1080]], "1920x1080", [1920, 1080], None])
def test_resolutions_are_distinct_pairs_of_positive_integers(resolutions):
    with pytest.raises(req.RequestError, match="resolution"):
        req.validate(dict(GOOD, resolutions=resolutions))


def test_missing_and_unknown_keys_and_a_non_object_are_refused():
    with pytest.raises(req.RequestError, match="lacks"):
        req.validate({k: v for k, v in GOOD.items() if k != "launches"})
    with pytest.raises(req.RequestError, match="unknown"):
        req.validate(dict(GOOD, prefs={}))
    with pytest.raises(req.RequestError, match="object"):
        req.validate([GOOD])


def test_a_file_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("{no", encoding="utf-8")
    with pytest.raises(req.RequestError, match="not JSON"):
        req.load(path)


@pytest.mark.parametrize("value", [0, -1, 1.5, "2", None, True, [2]])
def test_the_round_is_a_positive_integer(value):
    with pytest.raises(req.RequestError, match="round"):
        req.validate(dict(GOOD, round=value))


def test_the_request_needs_its_round_and_any_positive_integer_is_one():
    with pytest.raises(req.RequestError, match="lacks"):
        req.validate({k: v for k, v in GOOD.items() if k != "round"})
    assert req.validate(dict(GOOD, round=3))["round"] == 3


def test_the_popup_frame_values_are_declared_a_model_from_firefox_151_until_round_5_measures_them():
    """Comments only: the values are unchanged (popup_ui 72/90/106/139, popup_position 4); constants.py says what they are."""
    from invisible_core.constants import WINDOW_FRAME_BY_DPR

    text = (ROOT / "src" / "invisible_core" / "constants.py").read_text(encoding="utf-8")
    flat = " ".join(line.strip().lstrip("#:").strip() for line in text.splitlines())
    popup_ui = flat.split("popup_ui: int")[0].rsplit("A popup: from the window top", 1)[1]
    popup_position = flat.split("popup_position: int")[0].rsplit("Where a popup opened without left/top", 1)[1]
    for field, comment in (("popup_ui", popup_ui), ("popup_position", popup_position)):
        for needle in ("MODEL", "Firefox 151", "workbench", "retail 159", "round 5"):
            assert needle in comment, (field, needle)
    table = flat.split("The window frame per display scale.")[1].split("WINDOW_FRAME_BY_DPR: dict")[0]
    for needle in ("popup_ui, popup_position", "NOT a retail 159", "MODEL", "Firefox 151", "round 5"):
        assert needle in table, needle
    assert [WINDOW_FRAME_BY_DPR[d].popup_ui for d in (1.0, 1.25, 1.5, 2.0)] == [72, 90, 106, 139]
    assert {WINDOW_FRAME_BY_DPR[d].popup_position for d in WINDOW_FRAME_BY_DPR} == {4}
