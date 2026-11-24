"""captures/REQUEST.json says which release the next runner capture is of, and nothing else.

    {"firefox_version": "159.0", "dpr": [1]}

The retail-capture workflow runs when a pull request or a push changes this file. A request the workflow could
not run (a beta, a path in place of a version, a second key, a display scale a runner cannot have without a
preference) is refused by scripts/retail/capture_request.py in the first step, before anything is downloaded.
"""
from __future__ import annotations

import json

import pytest

from _retail_scripts import ROOT, load

pytestmark = pytest.mark.unit

req = load("capture_request")
GOOD = {"firefox_version": "159.0", "dpr": [1]}


def test_the_committed_request_is_valid_and_is_159():
    committed = json.loads((ROOT / "captures" / "REQUEST.json").read_text(encoding="utf-8"))
    assert committed == {"firefox_version": "159.0", "dpr": [1]}
    assert req.load(ROOT / "captures" / "REQUEST.json") == GOOD
    assert req.labels(GOOD) == ["1"]


@pytest.mark.parametrize("version", ["159.0", "159.0.1", "150.0", "99.1", "1000.0"])
def test_a_release_version_is_accepted(version):
    assert req.validate({"firefox_version": version, "dpr": [1]})["firefox_version"] == version


@pytest.mark.parametrize("version", ["159", "159.0b1", "159.0esr", "159.0a1", "v159.0", "159.0 ", "../159.0", "159.0/../158.0",
                                     "159.0;x", "", "0.0", "159.0.1.a", None, 159.0, ["159.0"]])
def test_anything_but_a_release_version_is_refused(version):
    with pytest.raises(req.RequestError, match="firefox_version"):
        req.validate({"firefox_version": version, "dpr": [1]})


@pytest.mark.parametrize("dpr", [[1.25], [1.5], [2], [0], [-1], [[1]], ["1"], [True], [None], [1, 1], [1, 2]])
def test_a_scale_a_runner_cannot_have_is_refused(dpr):
    with pytest.raises(req.RequestError, match="dpr"):
        req.validate({"firefox_version": "159.0", "dpr": dpr})


@pytest.mark.parametrize("dpr", [[], "1", 1, None, {}])
def test_dpr_is_a_non_empty_list(dpr):
    with pytest.raises(req.RequestError, match="dpr"):
        req.validate({"firefox_version": "159.0", "dpr": dpr})


def test_a_float_one_is_the_scale_one():
    assert req.validate({"firefox_version": "159.0", "dpr": [1.0]})["dpr"] == [1.0]
    assert req.labels({"firefox_version": "159.0", "dpr": [1.0]}) == ["1"]


def test_missing_and_unknown_keys_are_refused():
    with pytest.raises(req.RequestError, match="lacks"):
        req.validate({"firefox_version": "159.0"})
    with pytest.raises(req.RequestError, match="lacks"):
        req.validate({"dpr": [1]})
    with pytest.raises(req.RequestError, match="does not read"):
        req.validate(dict(GOOD, prefs={"x": 1}))


@pytest.mark.parametrize("value", [[], "159.0", 1, None])
def test_the_request_is_one_object(value):
    with pytest.raises(req.RequestError, match="object"):
        req.validate(value)


def test_a_file_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "REQUEST.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(req.RequestError, match="not JSON"):
        req.load(path)


def test_the_command_line_prints_what_the_workflow_reads(tmp_path, capsys):
    path = tmp_path / "REQUEST.json"
    path.write_text(json.dumps(GOOD), encoding="utf-8")
    assert req.main([str(path)]) == 0
    assert capsys.readouterr().out.splitlines() == ["firefox_version=159.0", "dpr=1"]
    path.write_text(json.dumps({"firefox_version": "159.0", "dpr": [2]}), encoding="utf-8")
    assert req.main([str(path)]) == 2
    assert "dpr" in capsys.readouterr().err
