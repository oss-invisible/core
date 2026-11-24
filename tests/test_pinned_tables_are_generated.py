"""Every value pinned to retail Firefox is generated from a dated capture, or says why it is not.

captures/pinned.json is the registry of those values (rule port-05, docs/retail-captures.md).
This gate holds the whole chain:

  * every row of the retail inventory (R01..R50) has an entry, and an entry names its status;
  * every capture directory is well formed, its items.json is canonical and its sha256 is the
    one the registry (and its own meta.json) names;
  * a `generated` table is what its generator writes from the captures named in the registry,
    byte for byte, and its generator holds no value of its own;
  * a `by-hand` entry names the host a new capture needs and a measurement file that exists,
    or says the file is missing; a `none-page` entry says how it is produced;
  * the same value is written once: the WebGPU limits and features, the navigator strings and
    the encode table are in their data file and nowhere else in the code or the tests.

Red on a tree without the registry: nothing here can be satisfied by the old layout, where the
tables were typed into prefs.py and copied into a test.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

import invisible_core
from invisible_core import _retail

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "captures" / "pinned.json"
RETAIL_DATA = ROOT / "src" / "invisible_core" / "data" / "retail"
ROWS = [f"R{n:02d}" for n in range(1, 51)]
STATUSES = {"generated", "by-hand", "none-page", "pending", "elsewhere"}
HOST_KINDS = {"github-runner", "windows-11-desktop"}


def _load_script(name: str):
    path = ROOT / "scripts" / "retail" / f"{name}.py"
    import sys
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"retail_{name}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


cf = _load_script("capture_format")
gen = _load_script("gen_tables")


def _has_payload(value) -> bool:
    if isinstance(value, dict):
        return "data" in value and "enc" in value or any(_has_payload(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_payload(v) for v in value)
    return False


@pytest.fixture(scope="module")
def registry():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entries(registry):
    return registry["entries"]


def _by_status(entries, status):
    return [e for e in entries if e["status"] == status]


# --------------------------------------------------------------------------- the registry


def test_the_registry_covers_every_row_of_the_inventory(registry, entries):
    assert registry["format"] == "pinned-tables/1"
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "an entry id is used twice"
    assert {e["m5"] for e in entries} == set(ROWS), "a row of the inventory has no entry, or an entry names no row"
    for e in entries:
        assert e["status"] in STATUSES, e["id"]
        assert e["what"] and e["targets"], e["id"]
        assert e["repos"] and set(e["repos"]) <= {"core", "series", "wrapper"}, e["id"]


def test_a_core_value_is_never_marked_elsewhere(entries):
    for e in entries:
        if "core" in e["repos"]:
            assert e["status"] != "elsewhere", f"{e['id']} is written in the core"
        else:
            assert e["status"] == "elsewhere", f"{e['id']} is not in the core and must say which repo has it"
            assert e["repo"] in {"series", "wrapper"}


def test_every_core_target_exists(entries):
    for e in entries:
        if "core" not in e["repos"]:
            continue
        for t in e["targets"]:
            assert (ROOT / t["file"]).is_file(), f"{e['id']}: target {t['file']} does not exist"


# --------------------------------------------------------------------------- the captures


def _capture_dirs():
    return cf.all_capture_dirs()


def test_there_is_a_capture():
    assert _capture_dirs(), "no capture under captures/"


@pytest.mark.parametrize("rel", _capture_dirs())
def test_a_capture_is_well_formed(rel, entries):
    d = ROOT / "captures" / rel
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    items_bytes = (d / "items.json").read_bytes()
    version, host, dated = rel.split("/")
    assert meta["format"] == "retail-capture/1" and meta["items_format"] == "host-gate/1"
    assert version == f"firefox-{meta['firefox_version']}"
    assert host == meta["host"]["kind"] and host in HOST_KINDS
    assert dated == f"{meta['date']}-dpr{('%g' % meta['dpr_asked'])}"
    assert set(meta["host"]) == {"kind", "os_build", "gpu_family"}, "the host is a kind, a build and a GPU FAMILY"
    assert "noise" not in meta and "gpu_renderer" not in meta["host"]
    # deterministic bytes: the only writer reproduces the file from what it parses
    assert items_bytes == cf.dumps_items(json.loads(items_bytes)), "items.json is not in the canonical form"
    assert meta["items"]["sha256"] == cf.sha256_bytes(items_bytes)
    assert meta["items"]["count"] == len(json.loads(items_bytes))
    # the page that took it is in the repository, byte for byte
    page = ROOT / meta["page"]["path"]
    assert cf.sha256_file(page) == meta["page"]["sha256"]
    # no image payload is left: every image is {w, h, enc, sha256}
    assert not _has_payload(json.loads(items_bytes)), "an image payload was not replaced by its sha256"
    # PUBLIC CAPTURES CARRY ONLY WHAT A READER NEEDS: no item survives outside the rule
    # (a generator reads it, a by-hand entry cites it, or it is a Firefox constant)
    items = json.loads(items_bytes)
    assert cf.survivors(items) == [], f"{rel}: values of the capture host outside the rule"
    assert meta["redacted"] == cf.placeholders(items) and meta["items"]["redacted"] == len(meta["redacted"])
    # a kept list whose rows are the capture host's own keeps the rows of the table only, and says so
    assert cf.row_leaks(items) == [], f"{rel}: rows of the capture host outside capture_format.ROW_KEEP"
    assert set(meta["dropped_rows"]) == set(cf.ROW_KEEP), f"{rel}: meta.json does not list the rows it dropped"
    text_all = items_bytes.decode("ascii")
    for vendor in ("Intel", "NVIDIA", "GeForce", "Radeon", "llvmpipe", "ANGLE ("):
        assert vendor not in text_all, f"{rel}: a GPU string ({vendor!r}) outside the family in meta.json"
    # raw capture: hashed and located, never a path of a machine
    raw = meta["raw"]
    assert re.fullmatch(r"[0-9a-f]{64}", raw["sha256"]) and raw["size"] > 0 and raw["kept"]
    # nothing that names a machine, a user or a local path
    for name, blob in (("items.json", items_bytes), ("meta.json", (d / "meta.json").read_bytes())):
        text = blob.decode("ascii")
        assert not re.search(r"[A-Za-z]:\\\\", text), f"{rel}/{name}: a Windows path"
        assert not re.search(r"/(Users|home)/", text), f"{rel}/{name}: a home path"
        assert "AppData" not in text, f"{rel}/{name}: a local path"


def test_the_registry_knows_the_sha256_of_every_capture(entries):
    pinned = {}
    for e in _by_status(entries, "generated"):
        for s in e["sources"]:
            assert pinned.setdefault(s["capture"], s["items_sha256"]) == s["items_sha256"], s["capture"]
    for rel in _capture_dirs():
        assert rel in pinned, f"{rel} is in captures/ and no generated table uses it"
        assert cf.sha256_file(ROOT / "captures" / rel / "items.json") == pinned[rel], (
            f"{rel}/items.json changed: its sha256 is not the registry's")
    for rel in pinned:
        assert (ROOT / "captures" / rel / "items.json").is_file(), f"the registry names {rel}, which is missing"


# --------------------------------------------------------------------------- generated tables


def _generated(entries):
    return _by_status(entries, "generated")


def test_every_generator_is_registered_and_every_data_file_has_one(entries):
    registered = {e["generator"].split(":", 1)[1] for e in _generated(entries)}
    assert registered == set(gen.GENERATORS), "a generator has no registry entry, or an entry names none"
    outputs = {e["output"] for e in _generated(entries)}
    on_disk = {p.relative_to(ROOT).as_posix() for p in RETAIL_DATA.glob("*.json")}
    assert outputs == on_disk, f"data/retail holds a file no generated entry names: {sorted(on_disk ^ outputs)}"


@pytest.mark.parametrize("name", sorted(gen.GENERATORS))
def test_a_generated_table_is_what_its_generator_writes(name, entries):
    fn, out_name, sources = gen.GENERATORS[name]
    entry = next(e for e in _generated(entries) if e["generator"] == f"scripts/retail/gen_tables.py:{name}")
    assert entry["output"] == f"src/invisible_core/data/retail/{out_name}"
    assert [s["capture"] for s in entry["sources"]] == sources
    assert (ROOT / entry["output"]).read_bytes() == fn(sources), (
        f"{entry['output']} is not what the captures give: run `python scripts/retail/gen_tables.py`")
    stem = Path(out_name).stem
    readers = [t for t in entry["targets"] if t["file"].endswith(".py")]
    assert readers, f"{entry['id']}: no code reads {out_name}"
    reading = [t["file"] for t in readers if f'"{stem}"' in (ROOT / t["file"]).read_text(encoding="utf-8")]
    assert reading, f"{entry['id']}: none of {[t['file'] for t in readers]} reads the table {stem!r}"


def test_every_item_a_reader_needs_survives_the_rule(entries):
    """The three readers of a capture: a generator, a by-hand entry's measurement, and nothing else."""
    needed = {}
    for name, keys in gen.READS.items():
        for key in keys:
            needed.setdefault(key, f"generator {name}")
    for e in _by_status(entries, "by-hand"):
        m = e["measurement"]
        if not m.get("missing"):
            for key in m["items_keys"]:
                needed.setdefault(key, f"by-hand entry {e['id']}")
    assert needed
    for rel in _capture_dirs():
        items = cf.load_items(ROOT / "captures" / rel)
        for key, reader in needed.items():
            assert items[key] != cf.PLACEHOLDER, f"{rel}: {key} ({reader}) was redacted: it is read"
            assert cf._rule((key,)) == "keep", f"{key} ({reader}) is not in capture_format.KEEP"


def test_a_generator_reads_only_the_items_it_declares(monkeypatch):
    for name, (fn, _out, sources) in gen.GENERATORS.items():
        seen = set()

        class Tracking(dict):
            def __getitem__(self, key):
                seen.add(key)
                return super().__getitem__(key)

        real = gen.cf.load_items
        monkeypatch.setattr(gen.cf, "load_items", lambda d, real=real: Tracking(real(d)))
        fn(sources)
        monkeypatch.undo()
        assert seen and seen <= set(gen.READS[name]), f"{name} reads {sorted(seen - set(gen.READS[name]))}"


def test_a_generator_reads_its_values_from_the_captures_only():
    # the generator file may order and spell; it holds no retail value of its own
    text = (ROOT / "scripts" / "retail" / "gen_tables.py").read_text(encoding="utf-8")
    for needle in ("Win32", "Windows NT", "maxTextureDimension", "bgra8unorm", "5.0 (Windows)", "16384",
                   "Segoe UI", "MS Shell Dlg", "Microsoft David", "10737418240", "EXT_clip_control", "OVR_multiview2"):
        assert needle not in text, f"gen_tables.py holds {needle!r}: it belongs to a capture"


# --------------------------------------------------------------------------- by hand and none-page


def test_a_by_hand_entry_names_its_host_and_its_measurement(entries):
    for e in _by_status(entries, "by-hand"):
        assert e["host_needed"], f"{e['id']}: no host named"
        m = e["measurement"]
        if m.get("missing"):
            assert m["file"] is None and m["evidence"], f"{e['id']}: a missing measurement says what exists"
            continue
        path = ROOT / m["file"]
        assert path.is_file(), f"{e['id']}: measurement file {m['file']} does not exist"
        items = json.loads(path.read_bytes())
        for key in m["items_keys"]:
            assert key in items, f"{e['id']}: {key} is not in {m['file']}"
        assert m["covers"], e["id"]
        if "capture_plan" in e:
            assert e["capture_plan"] in {"runner-page", "runner-server"}


def test_a_none_page_entry_says_how_it_is_produced(entries):
    for e in _by_status(entries, "none-page"):
        made = e["produced_today"]
        assert made["kind"] in {"script", "test", "by-hand"} and made["ref"], e["id"]
        if made["kind"] in {"script", "test"}:
            assert (ROOT / made["ref"]).is_file(), f"{e['id']}: {made['ref']} does not exist"


def test_no_entry_is_pending(entries):
    """Every table of _fpforge is generated, by hand (with its host) or none-page (with how it is made)."""
    assert not _by_status(entries, "pending"), [e["id"] for e in _by_status(entries, "pending")]


# --------------------------------------------------------------------------- one place per value


def _python_files():
    for sub in ("src", "tests", "scripts"):
        for p in (ROOT / sub).rglob("*.py"):
            if p != Path(__file__).resolve():
                yield p


def _needles():
    webgpu = _retail.table("webgpu")
    nav = _retail.table("navigator")
    for feature in webgpu["features"]:
        yield "webgpu", f'"{feature}"'
    for name, _value in webgpu["limits"]:
        yield "webgpu", f'"{name}'
    for row in _retail.table("media_encode")["encode_support"]:
        yield "media_encode", f'"{row}"'
    yield "navigator", f'"{nav["app_version"]}"'
    yield "navigator", f'"{nav["oscpu"]}"'
    yield "navigator", f'"{nav["platform"]}"'
    yield "navigator", nav["user_agent_template"].split("{version}")[0]
    extensions = _retail.table("webgl_extensions")
    for key in ("webgl1", "webgl2"):
        yield "webgl_extensions", ",".join(extensions[key])
        yield "webgl_extensions", ", ".join(f'"{n}"' for n in extensions[key])
    for row in _retail.table("voices")["voices"]:
        yield "voices", f'"{row}"'
    fonts = _retail.table("system_fonts")
    for key in ("ui_family", "dialog_family"):
        yield "system_fonts", f'= "{fonts[key]}"'
    yield "system_fonts", f'FONT_UI_SIZE = "{fonts["ui_size"]}"'
    yield "system_fonts", f'FONT_MONOSPACE_SIZE = {fonts["monospace_size"]}'


def test_a_retail_value_is_written_once():
    needles = sorted(set(_needles()))
    assert len(needles) > 50
    found = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        for table, needle in needles:
            if needle in text:
                found.append((table, needle, rel))
    assert not found, f"retail values typed outside their data file: {found[:10]}"


def test_the_webgpu_test_reads_the_data_file():
    text = (ROOT / "tests" / "test_webgpu_is_declared.py").read_text(encoding="utf-8")
    assert '_retail.table("webgpu")' in text


def test_the_tables_load_in_the_shape_the_core_reads():
    from invisible_core import constants, prefs

    nav = _retail.table("navigator")
    assert constants.USER_AGENT == nav["user_agent_template"].format(version=constants.UA_VERSION)
    assert constants.PLATFORM_OVERRIDE == nav["platform"] and constants.OSCPU_OVERRIDE == nav["oscpu"]
    assert prefs._NAVIGATOR_OVERRIDES["general.appversion.override"] == nav["app_version"] == constants.APPVERSION_OVERRIDE
    assert prefs._WIN_WEBGPU_FEATURES.split(chr(10)) == _retail.table("webgpu")["features"]
    assert len(prefs._WIN_WEBGPU_LIMITS.split(chr(10))) == 35
    assert prefs._WIN_ENCODE_SUPPORT.split(chr(10)) == _retail.table("media_encode")["encode_support"]
    assert Path(invisible_core.__file__).parent.joinpath("data", "retail", "webgpu.json").is_file()


def test_the_pool_and_the_sampler_hold_no_value_of_a_generated_table():
    """The two data files that used to carry retail values keep their own part only: the Windows personas
    of the GPU pool hold no extension list (the loader puts the generated pair in), and the cells of the
    storage table hold probabilities, not the quota."""
    from invisible_core import _webgl_personas
    from invisible_core._fpforge import _sampler

    data = ROOT / "src" / "invisible_core" / "_fpforge" / "data"
    pool = json.loads((data / "webgl_gpu_pool.json").read_text(encoding="utf-8"))
    for e in pool["win"]:
        assert not {"zoom.stealth.webgl.extensions", "zoom.stealth.webgl2.extensions"} & set(e["prefs"])
    lists = _retail.table("webgl_extensions")
    for persona in _webgl_personas._gpu_pool():
        assert persona["prefs"]["zoom.stealth.webgl.extensions"] == ",".join(lists["webgl1"])
        assert persona["prefs"]["zoom.stealth.webgl2.extensions"] == ",".join(lists["webgl2"])
        keys = list(persona["prefs"])
        assert keys.index("zoom.stealth.webgl.extensions") == keys.index("zoom.stealth.webgl.vendor") + 1
    cells = json.loads((data / "cpt_storage_given_class_tier.json").read_text(encoding="utf-8"))["table"]
    assert all(set(row) == {"prob"} for rows in cells.values() for row in rows)
    quota = _retail.table("storage_quota")["quota_mb"]
    assert {row["value"] for rows in _sampler._CPT_STORAGE.values() for row in rows} == {quota}
    assert {k: [r["prob"] for r in rows] for k, rows in _sampler._CPT_STORAGE.items()} == {
        k: [r["prob"] for r in rows] for k, rows in cells.items()}


def test_the_sampler_reads_the_appversion_from_constants():
    from invisible_core import constants
    from invisible_core._fpforge import _sampler

    assert _sampler._LOCKED["app_version"] is constants.APPVERSION_OVERRIDE


def test_the_voices_and_the_fonts_are_the_tables():
    from invisible_core._fpforge import profile

    voices = _retail.table("voices")["voices"]
    assert profile.VOICES == ",".join(voices) and len(voices) == 5
    assert all(row.split("|")[1] == "en-US" for row in voices), "the table is the en-US set"
    # the set with a second language pack interleaves its three own rows into the same five
    it_rows = profile.VOICES_IT.split(",")
    assert [r for r in it_rows if r in voices] == voices and len(it_rows) == 8
    assert profile.voices_for("en-US") == profile.VOICES
    fonts = _retail.table("system_fonts")
    assert (profile.FONT_UI_FAMILY, profile.FONT_UI_SIZE) == (fonts["ui_family"], fonts["ui_size"])
    assert profile.FONT_DIALOG_FAMILY == fonts["dialog_family"]
    assert profile.FONT_MONOSPACE_SIZE == fonts["monospace_size"] and isinstance(fonts["monospace_size"], int)
    assert isinstance(fonts["ui_size"], str), "Preferences::GetFloat parses the text form"


def test_the_row_rule_removes_the_hosts_own_voices():
    items = {"surface.voices": [["a", "en-US", True, False, "u1"], ["b", "xx", True, False, "u2"],
                                ["c", "en-US", True, False, "u3"]]}
    assert cf.row_leaks(items) == ["surface.voices"]
    assert cf.drop_rows(items).keys() == {"surface.voices"}
    assert [r[0] for r in items["surface.voices"]] == ["a", "c"] and cf.row_leaks(items) == []
