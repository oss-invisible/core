"""The retail capture format (retail-capture/1): reading, writing and hashing.

A capture is one visit of the host-gate page (format host-gate/1) to ONE retail
Firefox on ONE host. Its directory is

    captures/firefox-<version>/<host>/<date>-dpr<x>/
        items.json   the page's `items`, every image payload replaced by
                     {w, h, enc, sha256} of the DECODED bytes
        meta.json    where it was taken, with what, and what the raw file was

The bytes of items.json are deterministic: this module is the only writer, so
the same raw capture always gives the same file and the same sha256.
See docs/retail-captures.md.
"""
from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Tuple

REPO = Path(__file__).resolve().parents[2]
CAPTURES = REPO / "captures"
ITEMS_NAME = "items.json"
META_NAME = "meta.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _is_image(value: Any) -> bool:
    return isinstance(value, dict) and "data" in value and "enc" in value and "w" in value and "h" in value


def hash_images(value: Any) -> Tuple[Any, int]:
    """The value with every image payload replaced by its decoded bytes' sha256, and
    how many were replaced. `data` is base64 of the pixels (alpha8, rgba8) or of a PNG file."""
    if _is_image(value):
        raw = base64.b64decode(value["data"], validate=True)
        return {"w": value["w"], "h": value["h"], "enc": value["enc"], "sha256": sha256_bytes(raw)}, 1
    if isinstance(value, dict):
        out, n = {}, 0
        for k, v in value.items():
            out[k], c = hash_images(v)
            n += c
        return out, n
    if isinstance(value, list):
        out_list, n = [], 0
        for v in value:
            r, c = hash_images(v)
            out_list.append(r)
            n += c
        return out_list, n
    return value, 0


def rename_keys(value: Any, renames: Dict[str, str]) -> Tuple[Any, int]:
    """The value with every dict KEY rewritten by the (old -> new) substitutions, and how many
    keys changed. Values are never touched: a capture is what the browser answered."""
    if isinstance(value, dict):
        out, n = {}, 0
        for k, v in value.items():
            new = k
            for old, rep in renames.items():
                new = new.replace(old, rep)
            n += new != k
            out[new], c = rename_keys(v, renames)
            n += c
        return out, n
    if isinstance(value, list):
        out_list, n = [], 0
        for v in value:
            r, c = rename_keys(v, renames)
            out_list.append(r)
            n += c
        return out_list, n
    return value, 0


#: PUBLIC CAPTURES CARRY NO VALUE OF THE CAPTURE HOST THAT NO ONE READS. An item survives only when
#: a generator reads it, when a `by-hand` entry of captures/pinned.json cites it as its measurement,
#: or when it is a constant of Firefox itself (the ICU lists, compiled into every build). Everything
#: else is replaced by PLACEHOLDER, by rule, in the importer; meta.json lists every key replaced.
#:
#: A path is a tuple of fnmatch patterns, one per level: the item key first (item keys are
#: dotted strings, one level), then the keys inside it. KEEP names a subtree that survives; DROP names
#: what is replaced inside a kept subtree (host strings the readers do not need).
#: A table or entry that needs another item adds it to KEEP in the same commit, with its capture.
KEEP = (
    # generators: navigator (R01), WebGPU (R22), media capabilities (R19 encode)
    ("surface.navigator",),
    ("surface.webgpu",),
    ("surface.media.capabilities",),
    # by-hand entries (R10, R20, R23, R26, R38)
    ("surface.webgl1",), ("surface.webgl2",),
    ("control.webgl1",), ("control.webgl2",),
    ("surface.mq",),
    ("surface.computedStyle.a",), ("surface.computedStyle.input",),
    ("control.toDataURL_deBG_shape",),
    # Firefox constants identical in every Firefox: ICU's lists of supported values
    ("surface.intl", "supported_*"),
)
DROP = (
    ("surface.navigator", "hardwareConcurrency"),
    ("surface.webgpu", "adapters", "*", "info"),
    ("surface.webgl[12]", "unmasked"),
    ("surface.webgl[12]", "parameters", "RENDERER"),
    ("surface.webgl[12]", "parameters", "VENDOR"),
    ("surface.webgl[12]", "extension_parameters", "WEBGL_debug_renderer_info"),
)
PLACEHOLDER = "<redacted: capture host>"
PATH_SEP = "|"


def _matches(pattern, path) -> bool:
    return len(pattern) == len(path) and all(fnmatch.fnmatchcase(k, p) for k, p in zip(path, pattern))


def _prefix_of(pattern, path) -> bool:
    """`path` is `pattern`'s own first levels, or longer (the subtree under it)."""
    return len(path) >= len(pattern) and all(fnmatch.fnmatchcase(k, p) for k, p in zip(path, pattern))


def _leads_to(pattern, path) -> bool:
    """`path` is a strict ancestor of `pattern` (a container on the way to a kept subtree)."""
    return len(path) < len(pattern) and all(fnmatch.fnmatchcase(k, p) for k, p in zip(path, pattern))


def _rule(path):
    """'drop', 'keep', 'descend' or 'redact' for the node at `path`."""
    if any(_matches(d, path) for d in DROP):
        return "drop"
    if any(_prefix_of(k, path) for k in KEEP):
        return "keep"
    if any(_leads_to(k, path) for k in KEEP):
        return "descend"
    return "redact"


def _may_hold_drop(path) -> bool:
    return any(_leads_to(d, path) for d in DROP)


def redact(items: Dict[str, Any]) -> list:
    """Apply the rule to `items` IN PLACE; return the paths replaced (PATH_SEP-joined, sorted)."""
    done: list = []

    def visit(container, key, path):
        node = container[key]
        what = _rule(path)
        if what in ("drop", "redact") or (what == "descend" and not isinstance(node, dict)):
            container[key] = PLACEHOLDER
            done.append(PATH_SEP.join(path))
        elif isinstance(node, dict) and (what == "descend" or _may_hold_drop(path)):
            for k in list(node):
                visit(node, k, path + (k,))

    for key in list(items):
        visit(items, key, (key,))
    return sorted(done)


def survivors(items: Any, path=()) -> list:
    """Paths of values that are neither the placeholder nor allowed by the rule: must be empty."""
    if path:
        what = _rule(path)
        if what in ("drop", "redact"):
            return [] if items == PLACEHOLDER else [PATH_SEP.join(path)]
        if what == "keep" and not _may_hold_drop(path):
            return []
    if isinstance(items, dict):
        out: list = []
        for k, v in items.items():
            out += survivors(v, path + (k,))
        return out
    return [PATH_SEP.join(path)] if path and _rule(path) != "keep" else []


def placeholders(items: Any, path=()) -> list:
    """Paths whose value is the placeholder (what meta.json `redacted` must list)."""
    if items == PLACEHOLDER:
        return [PATH_SEP.join(path)]
    out: list = []
    if isinstance(items, dict):
        for k, v in items.items():
            out += placeholders(v, path + (k,))
    return sorted(out)


def dumps_items(items: Dict[str, Any]) -> bytes:
    """Deterministic bytes: one item per line, keys sorted, compact values, UTF-8,
    a single final newline. Floats are written by Python's repr, which round-trips."""
    lines = []
    for key in sorted(items):
        lines.append(json.dumps(key, ensure_ascii=True) + ":" +
                     json.dumps(items[key], ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return ("{\n" + ",\n".join(lines) + "\n}\n").encode("utf-8")


def dumps_meta(meta: Dict[str, Any]) -> bytes:
    return (json.dumps(meta, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")


def load_items(capture_dir: Path) -> Dict[str, Any]:
    return json.loads((capture_dir / ITEMS_NAME).read_text(encoding="utf-8"))


def load_meta(capture_dir: Path) -> Dict[str, Any]:
    return json.loads((capture_dir / META_NAME).read_text(encoding="utf-8"))


def capture_dir(relative: str) -> Path:
    """`firefox-159.0/windows-11-desktop/2026-10-31-dpr1` -> its directory."""
    return CAPTURES / relative


def all_capture_dirs():
    """Every directory that holds an items.json, as paths relative to captures/ (POSIX)."""
    return sorted(p.parent.relative_to(CAPTURES).as_posix() for p in CAPTURES.glob("firefox-*/*/*/" + ITEMS_NAME))
