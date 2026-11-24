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
import re
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


def webgl_extensions(sources: List[str]) -> bytes:
    """R23 (the lists of the pool). The WebGL 1 and WebGL 2 extension names retail Firefox on
    Windows answers, in the order its engine returns them: the same at every display scale. The core
    gives the one pair to every Windows persona (ANGLE's D3D11 set is the same whatever the vendor)."""
    caps = {s: cf.load_items(cf.capture_dir(s)) for s in sources}
    missing = [label for label, key in (("WebGL1", "surface.webgl1"), ("WebGL2", "surface.webgl2"))
               if any(not _has_extension_list(c[key]) for c in caps.values())]
    if missing:  # a host with no GPU answers the page with a word instead of a context's values
        both = " and ".join(missing)
        raise SystemExit(f"the capture has no {both} extension list (no {both} context on this host)")
    return _dumps("webgl_extensions", sources, {
        "webgl1": _same("webgl1.extensions", {s: c["surface.webgl1"]["extensions"] for s, c in caps.items()}),
        "webgl2": _same("webgl2.extensions", {s: c["surface.webgl2"]["extensions"] for s, c in caps.items()}),
    })


def _has_extension_list(item: Any) -> bool:
    return isinstance(item, dict) and isinstance(item.get("extensions"), list)


#: The system fonts a page reads with `font: caption` and the like; they carry one size and one family.
_SHELL_FONTS = ("caption", "icon", "menu", "message-box", "small-caption", "status-bar")


def _css_unescape(ident: str) -> str:
    """A CSS identifier as getComputedStyle serialises it, back to its text: a backslash, hex digits and
    one optional space are the character with that code point."""
    return re.sub(r"\\([0-9a-fA-F]{1,6})[ \t\n]?", lambda m: chr(int(m.group(1), 16)), ident)


def system_fonts(sources: List[str]) -> bytes:
    """R32 (four of its values). The shell font family and size, the family of the form controls and
    the default monospace size, as a page of retail Windows Firefox reads them."""
    caps = {s: cf.load_items(cf.capture_dir(s)) for s in sources}
    shell = _same("systemFonts", {f"{s}|{k}": c["surface.systemFonts"][k][:2]
                                  for s, c in caps.items() for k in _SHELL_FONTS})
    size = shell[1]
    if not size.endswith("px"):
        raise SystemExit(f"system font size {size!r} is not in px")
    return _dumps("system_fonts", sources, {
        "ui_family": shell[0],
        "ui_size": size[:-2],
        "dialog_family": _css_unescape(_same("input font-family", {s: c["surface.computedStyle.input"]["font-family"]
                                                                   for s, c in caps.items()})),
        "monospace_size": int(_same("textarea font-size", {s: c["surface.computedStyle.textarea"]["font-size"]
                                                           for s, c in caps.items()}).removesuffix("px")),
    })


def voices(sources: List[str]) -> bytes:
    """R36 (the en-US set). What `speechSynthesis.getVoices()` lists on retail Windows with the en-US
    voices, in its order, as `name|lang|default|local` (the form the engine parses). The public capture
    keeps the en-US rows only (capture_format.ROW_KEEP); another language is not this table's."""
    rows = _same("voices", {s: cf.load_items(cf.capture_dir(s))["surface.voices"] for s in sources})
    out = []
    for name, lang, local, default, _uri in rows:
        if lang != "en-US":
            raise SystemExit(f"voice {name!r} is {lang!r}: the capture must hold the en-US rows only")
        out.append(f"{name}|{lang}|{int(default)}|{int(local)}")
    return _dumps("voices", sources, {"voices": out})


def storage_quota(sources: List[str]) -> bytes:
    """R37. `navigator.storage.estimate().quota` of a retail Firefox on a disk of 100 GiB or more: the
    group cap, a constant of Firefox. Megabytes, the unit of the sampler's table."""
    quota = _same("storage quota", {s: cf.load_items(cf.capture_dir(s))["surface.storage"]["quota"] for s in sources})
    if quota % (1 << 20):
        raise SystemExit(f"quota {quota} is not a whole number of megabytes")
    return _dumps("storage_quota", sources, {"quota_bytes": quota, "quota_mb": quota >> 20})


#: The items of a capture each generator reads. Nothing else of a capture is needed by code; an item that
#: is neither here nor cited by a by-hand entry is replaced by the importer (capture_format.KEEP).
READS: Dict[str, List[str]] = {
    "navigator": ["surface.navigator"],
    "webgpu": ["surface.webgpu"],
    "media_encode": ["surface.media.capabilities"],
    "webgl_extensions": ["surface.webgl1", "surface.webgl2"],
    "system_fonts": ["surface.systemFonts", "surface.computedStyle.input", "surface.computedStyle.textarea"],
    "voices": ["surface.voices"],
    "storage_quota": ["surface.storage"],
}

#: name -> (function, output file under DATA_DIR, source captures)
GENERATORS: Dict[str, Any] = {
    "navigator": (navigator, "navigator.json", FF159_WIN11),
    "webgpu": (webgpu, "webgpu.json", FF159_WIN11),
    "media_encode": (media_encode, "media_encode.json", FF159_WIN11),
    "webgl_extensions": (webgl_extensions, "webgl_extensions.json", FF159_WIN11),
    "system_fonts": (system_fonts, "system_fonts.json", FF159_WIN11),
    "voices": (voices, "voices.json", FF159_WIN11),
    "storage_quota": (storage_quota, "storage_quota.json", FF159_WIN11),
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
