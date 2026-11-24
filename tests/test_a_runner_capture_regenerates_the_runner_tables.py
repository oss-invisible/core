"""A capture from a GitHub runner is a capture of the format, and it regenerates the tables a runner can capture.

Rule port-05: the capture of the official 159 taken on a runner must regenerate, byte for byte, every
`generated` table of captures/pinned.json whose `host_needed` names the github-runner (R01, the navigator).
scripts/retail/make_runner_capture.py turns a run's raw result into the directory; scripts/retail/
check_runner_capture.py says, for a capture directory, which registry tables it regenerates and which differ.

Held here:
  * the directory made from a raw result has the format (items.json canonical and hashed, meta.json with host
    kind `github-runner`, the runner image, the build id, the installer's sha256 and its SHA256SUMS line, the page
    host-gate-2) and carries no path of a machine;
  * the checker says `regenerates` for a capture that gives the committed bytes and `differs` or `cannot` for one
    that does not, is red (exit 1) when a runner-capturable table is not regenerated, and never red for a table
    that needs another host;
  * the gate: every runner capture committed under captures/ regenerates the runner-capturable tables.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from _retail_scripts import ROOT, load

pytestmark = pytest.mark.integration

cf = load("capture_format")
check = load("check_runner_capture")
make = load("make_runner_capture")
WIN11_DPR1 = ROOT / "captures" / "firefox-159.0" / "windows-11-desktop" / "2026-10-31-dpr1"
BUILD = {
    "firefox_version": "159.0", "url": "https://archive.mozilla.org/pub/firefox/releases/159.0/win64/en-US/Firefox%20Setup%20159.0.exe",
    "installer_sha256": "8776887b79925abbdd407fcdfe257d8382b54e24be52a6466426b95a804d6790",
    "sums_line": "8776887b79925abbdd407fcdfe257d8382b54e24be52a6466426b95a804d6790  win64/en-US/Firefox Setup 159.0.exe",
    "signature": {"verified": True, "primary_fingerprint": "14F26682D0916CDD81E37B6D61B7B526D98F0353",
                  "signing_key": "827E658608679618CD349F93678E455D76767AA3"},
    "build_id": "20260101000000", "build_id_source": "core/application.ini of the extracted official installer",
}
ENV = {"ImageOS": "win25", "ImageVersion": "20261101.1.0"}


def _raw(tmp_path: Path, dpr=1, items=None) -> Path:
    """A raw host-gate/1 result whose items are those of the committed Windows 11 capture."""
    doc = {"format": "host-gate/1", "meta": {"devicePixelRatio": dpr, "userAgent": "x"},
           "items": items if items is not None else cf.load_items(WIN11_DPR1), "noise": {"worker_start_ms": 3}}
    path = tmp_path / "dpr1.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.fixture
def made(tmp_path, monkeypatch):
    """A runner capture made into a captures/ of its own (the repository's is never written)."""
    monkeypatch.setattr(cf, "CAPTURES", tmp_path / "captures")
    wire = tmp_path / "wire.json"
    wire.write_text(json.dumps({"format": "retail-wire/1", "http_requests": [], "certificate_requests": [], "tls_connections": []}))
    out = make.make(_raw(tmp_path), BUILD, wire, "2026-11-24", "1", "the workflow artifact of a run", env=ENV)
    return out


# --------------------------------------------------------------------------- the directory made


def test_the_directory_is_named_and_filled_as_the_format_says(made):
    assert made.relative_to(cf.CAPTURES).as_posix() == "firefox-159.0/github-runner/2026-11-24-dpr1"
    assert sorted(p.name for p in made.iterdir()) == ["items.json", "meta.json", "wire.json"]
    items_bytes = (made / "items.json").read_bytes()
    assert items_bytes == cf.dumps_items(json.loads(items_bytes))
    assert items_bytes == (WIN11_DPR1 / "items.json").read_bytes(), "same items in, same canonical bytes out"


def test_the_meta_names_the_runner_the_build_and_the_page(made):
    meta = cf.load_meta(made)
    assert meta["format"] == "retail-capture/1" and meta["items_format"] == "host-gate/1"
    assert meta["host"] == {"kind": "github-runner", "os_build": meta["host"]["os_build"], "gpu_family": "none"}
    assert meta["host"]["os_build"]
    assert meta["runner"] == {"image_os": "win25", "image_version": "20261101.1.0"}
    assert meta["firefox_version"] == "159.0" and meta["build_id"] == "20260101000000"
    assert meta["build_id_source"] == BUILD["build_id_source"]
    assert meta["installer"] == {"url": BUILD["url"], "sha256": BUILD["installer_sha256"],
                                 "sums_line": BUILD["sums_line"], "signature": BUILD["signature"]}
    assert meta["dpr"] == 1 and meta["dpr_asked"] == 1.0 and meta["date"] == "2026-11-24"
    page = ROOT / "captures" / "pages" / "host-gate-2.html"
    assert meta["page"]["path"] == "captures/pages/host-gate-2.html" and meta["page"]["sha256"] == cf.sha256_file(page)
    assert meta["items"]["sha256"] == cf.sha256_file(made / "items.json")
    assert meta["raw"]["kept"] == "the workflow artifact of a run"


def test_the_public_rule_holds_on_what_was_made_and_no_machine_is_named(made):
    items = cf.load_items(made)
    assert cf.survivors(items) == []
    meta = cf.load_meta(made)
    assert meta["redacted"] == cf.placeholders(items)
    for name in ("items.json", "meta.json"):
        text = (made / name).read_text(encoding="ascii")
        assert not re.search(r"[A-Za-z]:\\\\", text) and "AppData" not in text and not re.search(r"/(Users|home|runner)/", text)
    assert "ImageVersion" not in json.dumps(meta)


def test_a_scale_the_page_did_not_read_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "CAPTURES", tmp_path / "captures")
    wire = tmp_path / "wire.json"
    wire.write_text("{}")
    with pytest.raises(make.CaptureError, match="display scale"):
        make.make(_raw(tmp_path, dpr=1.25), BUILD, wire, "2026-11-24", "1", "x", env=ENV)
    assert not (tmp_path / "captures").exists()


def test_a_result_that_is_not_host_gate_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "CAPTURES", tmp_path / "captures")
    raw = tmp_path / "dpr1.json"
    raw.write_text(json.dumps({"format": "other/1", "meta": {"devicePixelRatio": 1}, "items": {}}))
    with pytest.raises(make.CaptureError, match="host-gate/1"):
        make.make(raw, BUILD, raw, "2026-11-24", "1", "x", env=ENV)


# --------------------------------------------------------------------------- which tables it regenerates


def _by_id(results):
    return {r["id"]: r for r in results}


def test_the_checker_says_which_tables_a_capture_regenerates(made):
    results = _by_id(check.check(made))
    assert results["R01"] == {"id": "R01", "table": "navigator", "output": "src/invisible_core/data/retail/navigator.json",
                              "runner_capturable": True, "status": "regenerates", "detail": "byte for byte"}
    assert results["R19.encode"]["runner_capturable"] is False and results["R22"]["runner_capturable"] is False
    assert {r["status"] for r in results.values()} == {"regenerates"}
    assert check.failed(list(results.values())) == []


def test_the_checker_is_green_on_a_capture_that_regenerates_the_runner_tables(made, capsys):
    assert check.main([str(made)]) == 0
    out = capsys.readouterr().out
    assert "R01" in out and "regenerates: byte for byte" in out and "runner-capturable" in out and "needs another host" in out


def _with_navigator(tmp_path: Path, edit) -> Path:
    items = cf.load_items(WIN11_DPR1)
    edit(items)
    target = tmp_path / "copy"
    target.mkdir()
    (target / "items.json").write_bytes(cf.dumps_items(items))
    shutil.copyfile(WIN11_DPR1 / "meta.json", target / "meta.json")
    return target


def test_a_runner_capture_with_another_navigator_differs_and_is_red(tmp_path, capsys):
    target = _with_navigator(tmp_path, lambda items: items["surface.navigator"].update(platform="Linux x86_64"))
    results = _by_id(check.check(target))
    assert results["R01"]["status"] == "differs" and "platform" in results["R01"]["detail"]
    assert [r["id"] for r in check.failed(list(results.values()))] == ["R01"]
    assert check.main([str(target)]) == 1
    assert "RED: R01" in capsys.readouterr().err


def test_a_capture_without_the_navigator_cannot_regenerate_and_is_red(tmp_path):
    target = _with_navigator(tmp_path, lambda items: items.pop("surface.navigator"))
    results = _by_id(check.check(target))
    assert results["R01"]["status"] == "cannot" and "surface.navigator" in results["R01"]["detail"]
    assert check.main([str(target)]) == 1


def test_a_table_that_needs_another_host_never_makes_the_check_red(tmp_path):
    """A runner has no GPU: WebGPU differs there, and that is the registry's own statement, not a failure."""
    target = _with_navigator(tmp_path, lambda items: items["surface.webgpu"].update(adapters={"default": None}))
    results = _by_id(check.check(target))
    assert results["R22"]["status"] in ("differs", "cannot") and results["R22"]["runner_capturable"] is False
    assert results["R01"]["status"] == "regenerates"
    assert check.failed(list(results.values())) == []
    assert check.main([str(target)]) == 0


def test_the_checker_leaves_the_generators_as_it_found_them(made):
    before = cf.capture_dir
    check.check(made)
    assert cf.capture_dir is before
    check.main([str(made / "missing")])
    assert cf.capture_dir is before


def test_a_directory_that_is_not_a_capture_is_exit_2(tmp_path, capsys):
    assert check.main([str(tmp_path)]) == 2
    assert "not a capture" in capsys.readouterr().err


def test_the_json_form_names_the_capture_and_every_table(made, capsys):
    assert check.main([str(made), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["capture"] == {"firefox_version": "159.0", "host": "github-runner", "date": "2026-11-24"}
    assert {t["id"] for t in doc["tables"]} >= {"R01", "R19.encode", "R22"}


def test_only_tables_that_name_the_runner_are_runner_capturable():
    registry = json.loads((ROOT / "captures" / "pinned.json").read_text(encoding="utf-8"))
    generated = [e for e in registry["entries"] if e["status"] == "generated"]
    capturable = {e["id"] for e in generated if check.runner_capturable(e)}
    assert capturable == {e["id"] for e in generated if "github-runner" in e["host_needed"]}
    assert "R01" in capturable and not capturable & {"R19.encode", "R22"}


# --------------------------------------------------------------------------- the gate


def _runner_captures():
    return sorted(p.parent for p in (ROOT / "captures").glob("firefox-*/github-runner/*/items.json"))


def test_every_committed_runner_capture_regenerates_the_runner_capturable_tables():
    """Vacuous until a runner capture is committed; from then on it is the gate of rule port-05 on 159."""
    for directory in _runner_captures():
        results = check.check(directory)
        assert check.failed(results) == [], f"{directory.relative_to(ROOT)}: {check.failed(results)}"
        meta = cf.load_meta(directory)
        assert meta["host"]["kind"] == "github-runner" and meta["installer"]["sums_line"] and meta["build_id"]
        assert meta["page"]["path"] == "captures/pages/host-gate-2.html"
