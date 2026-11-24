"""Import one raw host-gate/1 capture as a retail capture directory.

    python scripts/retail/import_host_gate.py RAW.json \
        --firefox-version 159.0 --host windows-11-desktop --date 2026-10-31 --dpr-label 1.25 \
        --raw-kept "<where the full raw file is kept>" [--build-id ...] [--os-build ...]

Writes captures/firefox-<version>/<host>/<date>-dpr<label>/{items.json,meta.json}
(see docs/retail-captures.md). The raw file is read, never copied: its images
become {w, h, enc, sha256}. Nothing that names the machine, a user or a path is
copied into meta.json: only the fields below.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_format as cf  # noqa: E402

HOST_KINDS = ("github-runner", "windows-11-desktop")
PAGE_RELATIVE = "captures/pages/host-gate-1.html"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw", type=Path)
    ap.add_argument("--firefox-version", required=True)
    ap.add_argument("--host", required=True, choices=HOST_KINDS)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD of the capture")
    ap.add_argument("--dpr-label", required=True, help="the display scale asked for, as in the directory name (1, 1.25, ...)")
    ap.add_argument("--raw-kept", required=True, help="where the full raw capture is kept, in words (never a local path)")
    ap.add_argument("--build-id", default=None)
    ap.add_argument("--build-id-source", default=None)
    ap.add_argument("--installer-url", default=None)
    ap.add_argument("--installer-sha256", default=None)
    ap.add_argument("--gpu-family", default=None, help="the GPU as a family (e.g. 'Intel integrated'), never the exact string")
    ap.add_argument("--os-build", default=None, help="OS build as recorded; omitted when the capture did not record it")
    ap.add_argument("--raw-sha256-before-rewrite", default=None,
                    help="when RAW is a file whose item keys the archive already rewrote: the sha256 of the file before it")
    ap.add_argument("--keys-rewritten-in-raw", type=int, default=0,
                    help="how many occurrences the archive rewrote in RAW (counted in meta.items.renamed_keys)")
    ap.add_argument("--page", type=Path, default=cf.REPO / PAGE_RELATIVE)
    ap.add_argument("--served-page", type=Path, default=None,
                    help="the page file the capture was actually served, when the repository copy differs from it "
                         "only by --rename-key")
    ap.add_argument("--rename-key", action="append", default=[], metavar="OLD=NEW",
                    help="rewrite OLD to NEW inside item KEYS (never values); used to keep the name of a "
                         "studied system out of this repository. Repeatable.")
    args = ap.parse_args(argv)

    raw_bytes = args.raw.read_bytes()
    raw = json.loads(raw_bytes)
    if raw.get("format") != "host-gate/1":
        print(f"not a host-gate/1 capture: {raw.get('format')!r}", file=sys.stderr)
        return 2
    items, n_images = cf.hash_images(raw["items"])
    renames = dict(r.split("=", 1) for r in args.rename_key)
    items, n_renamed = cf.rename_keys(items, renames)
    dropped_rows = cf.drop_rows(items)  # the rows of a kept list that are the capture host's own
    redacted = cf.redact(items)  # the public rule: only what a reader needs survives
    items_bytes = cf.dumps_items(items)

    meta = {
        "format": "retail-capture/1",
        "items_format": raw["format"],
        "firefox_version": args.firefox_version,
        "build_id": args.build_id,
        "build_id_source": args.build_id_source,
        "installer": {"url": args.installer_url, "sha256": args.installer_sha256},
        "host": {
            "kind": args.host,
            "os_build": args.os_build,
            "gpu_family": args.gpu_family,
        },
        "dpr": raw["meta"]["devicePixelRatio"],
        "dpr_asked": float(args.dpr_label),
        "date": args.date,
        "page": {"path": PAGE_RELATIVE, "sha256": cf.sha256_file(args.page),
                 "served_sha256": cf.sha256_file(args.served_page) if args.served_page else None},
        "user_agent": raw["meta"].get("userAgent"),
        "items": {"count": len(items), "images": n_images, "renamed_keys": n_renamed + args.keys_rewritten_in_raw,
                  "redacted": len(redacted),
                  "sha256": cf.sha256_bytes(items_bytes)},
        "redacted": redacted,
        "dropped_rows": dropped_rows,
        "noise_keys": sorted(raw.get("noise", {})),
        "raw": {"sha256": cf.sha256_bytes(raw_bytes), "size": len(raw_bytes), "kept": args.raw_kept},
    }
    if args.raw_sha256_before_rewrite:
        meta["raw"]["sha256_before_rewrite"] = args.raw_sha256_before_rewrite
    out = cf.CAPTURES / f"firefox-{args.firefox_version}" / args.host / f"{args.date}-dpr{args.dpr_label}"
    out.mkdir(parents=True, exist_ok=True)
    (out / cf.ITEMS_NAME).write_bytes(items_bytes)
    (out / cf.META_NAME).write_bytes(cf.dumps_meta(meta))
    print(f"{out.relative_to(cf.REPO).as_posix()}  items.json {len(items_bytes)} bytes  "
          f"sha256 {meta['items']['sha256']}  ({len(items)} items, {n_images} images)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
