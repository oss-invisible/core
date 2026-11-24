"""Regenerate the pinned tables that a retail capture decides.

    python scripts/retail/gen_tables.py            write every generated table
    python scripts/retail/gen_tables.py --check    fail when a committed table is not what the captures give
    python scripts/retail/gen_tables.py webgpu     only that one

One generator per table. A generator reads the captures named in
captures/pinned.json (status `generated`) and returns the bytes of ONE file under
src/invisible_core/data/retail/. It holds no value of its own: what a page of retail
Firefox answered is the table, and a rule here only chooses the order and the spelling
the core reads. A value no capture carries is never typed in a generator: the table
stays `by-hand` in pinned.json instead.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_format as cf  # noqa: E402

DATA_DIR = cf.REPO / "src" / "invisible_core" / "data" / "retail"
GENERATOR_FILE = "scripts/retail/gen_tables.py"

#: The five windows-11-desktop captures of retail Firefox 159, one per display scale.
FF159_WIN11 = [
    "firefox-159.0/windows-11-desktop/2026-10-31-dpr1",
    "firefox-159.0/windows-11-desktop/2026-10-31-dpr1.25",
    "firefox-159.0/windows-11-desktop/2026-10-31-dpr1.5",
    "firefox-159.0/windows-11-desktop/2026-10-31-dpr1.75",
    "firefox-159.0/windows-11-desktop/2026-10-31-dpr2",
]


def _dumps(name: str, sources: List[str], body: Dict[str, Any]) -> bytes:
    doc = {"_generated": {"generator": f"{GENERATOR_FILE}:{name}", "captures": sources, "edit": "never by hand"}}
    doc.update(body)
    return (json.dumps(doc, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def _same(what: str, values: Dict[str, Any]) -> Any:
    """The one value every capture agrees on; disagreement is an error, never a pick."""
    distinct = {json.dumps(v, sort_keys=True) for v in values.values()}
    if len(distinct) != 1:
        raise SystemExit(f"{what}: the captures disagree: {sorted(distinct)}")
    return next(iter(values.values()))


def navigator(sources: List[str]) -> bytes:
    """R01. The identity strings of the navigator, the User-Agent with its version as a
    placeholder: the version of a session is the sealed engine's, never the capture's."""
    caps = {s: cf.load_items(cf.capture_dir(s))["surface.navigator"] for s in sources}
    versions = {s: cf.load_meta(cf.capture_dir(s))["firefox_version"] for s in sources}
    version = _same("firefox_version", versions)
    ua_version = ".".join(version.split(".")[:2])  # Firefox puts MAJOR.MINOR only in the UA
    ua = _same("userAgent", {s: c["userAgent"] for s, c in caps.items()})
    if ua.count(ua_version) != 2:
        raise SystemExit(f"userAgent {ua!r} does not carry {ua_version} exactly twice (rv: and Firefox/)")
    return _dumps("navigator", sources, {
        "user_agent_template": ua.replace(ua_version, "{version}"),
        "platform": _same("platform", {s: c["platform"] for s, c in caps.items()}),
        "oscpu": _same("oscpu", {s: c["oscpu"] for s, c in caps.items()}),
        "app_version": _same("appVersion", {s: c["appVersion"] for s, c in caps.items()}),
    })


#: Firefox adds this one to the features itself; the core declares the rest.
_ADDED_BY_FIREFOX = "core-features-and-limits"


def webgpu(sources: List[str]) -> bytes:
    """R22. The features and limits of the D3D12 adapter: the same for every adapter variant
    the page asked for (default, low-power, high-performance, forced fallback) and at every
    display scale, so they are constants of Firefox's backend, declared once."""
    seen: Dict[str, Any] = {}
    for s in sources:
        adapters = cf.load_items(cf.capture_dir(s))["surface.webgpu"]["adapters"]
        for variant, adapter in adapters.items():
            if adapter is not None:
                seen[f"{s}|{variant}"] = {"features": sorted(adapter["features"]), "limits": adapter["limits"]}
    one = _same("webgpu adapter", seen)
    return _dumps("webgpu", sources, {
        "features": [f for f in one["features"] if f != _ADDED_BY_FIREFOX],
        "limits": [[k, one["limits"][k]] for k in sorted(one["limits"])],
    })


#: The WebRTC video codecs, in the order the core declares them, and the mime type the
#: engine reads for each (the page asks by the WebRTC name).
_ENCODE_ROWS = [("video/avc", "video/H264"), ("video/vp8", "video/VP8"),
                ("video/vp9", "video/VP9"), ("video/av1", "video/AV1")]


def media_encode(sources: List[str]) -> bytes:
    """R19 (encode half). What retail Windows ENCODES for WebRTC: a codec the page's
    encodingInfo calls supported, `sw` when it is not power efficient (no hardware encoder)."""
    rows = []
    for mime, rtc in _ENCODE_ROWS:
        got = _same(rtc, {s: cf.load_items(cf.capture_dir(s))["surface.media.capabilities"]["webrtc-encode." + rtc]
                          for s in sources})
        supported, _smooth, power_efficient = got
        if supported is not True or power_efficient is not False:
            raise SystemExit(f"{rtc}: encodingInfo {got}: a hardware encoder needs its own rule and capture")
        rows.append(f"{mime}|sw")
    return _dumps("media_encode", sources, {"encode_support": rows})


#: The items of a capture each generator reads. Nothing else of a capture is needed by code; an item that
#: is neither here nor cited by a by-hand entry is replaced by the importer (capture_format.KEEP).
READS: Dict[str, List[str]] = {
    "navigator": ["surface.navigator"],
    "webgpu": ["surface.webgpu"],
    "media_encode": ["surface.media.capabilities"],
}

#: name -> (function, output file under DATA_DIR, source captures)
GENERATORS: Dict[str, Any] = {
    "navigator": (navigator, "navigator.json", FF159_WIN11),
    "webgpu": (webgpu, "webgpu.json", FF159_WIN11),
    "media_encode": (media_encode, "media_encode.json", FF159_WIN11),
}


def build(name: str) -> bytes:
    fn, _out, sources = GENERATORS[name]
    return fn(sources)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="generators to run (default: all)")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 when a committed table differs")
    args = ap.parse_args(argv)
    bad = 0
    for name in args.names or sorted(GENERATORS):
        data = build(name)
        path = DATA_DIR / GENERATORS[name][1]
        if args.check:
            same = path.is_file() and path.read_bytes() == data
            print(f"{'ok  ' if same else 'DIFF'} {name} -> {path.relative_to(cf.REPO).as_posix()}")
            bad += not same
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            print(f"wrote {path.relative_to(cf.REPO).as_posix()} ({len(data)} bytes)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
