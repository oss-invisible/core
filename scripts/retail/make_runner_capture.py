"""Turn what one runner run produced into a capture directory of the format.

    python scripts/retail/make_runner_capture.py --raw RAW.json --build build.json --wire wire.json \
        --date 2026-11-24 --dpr-label 1 --raw-kept "<where the full raw file is kept>"

Writes captures/firefox-<version>/github-runner/<date>-dpr<x>/ with items.json and meta.json (made by
import_host_gate.py, so the format and the public redaction rule are the importer's), the wire.json the
capture server recorded, and in meta.json what only a runner run knows: the runner image, the build id read from
application.ini, the installer's sha256 and its SHA256SUMS line, and the key the signature was verified with.

The runner's OS build and image come from the environment of the job (ImageOS, ImageVersion) and from the
operating system; a machine name, a user or a path is never written.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_format as cf  # noqa: E402
import import_host_gate  # noqa: E402

PAGE_PATH = "captures/pages/host-gate-2.html"
HOST = "github-runner"
#: A runner has no GPU: the family says so (the registry reads the family, never a string).
GPU_FAMILY = "none"
WIRE_NAME = "wire.json"


class CaptureError(Exception):
    pass


def runner_facts(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    env = os.environ if env is None else env
    return {"image_os": env.get("ImageOS"), "image_version": env.get("ImageVersion"), "os_build": platform.version()}


def make(raw: Path, build: Dict[str, Any], wire: Path, date: str, dpr_label: str, raw_kept: str,
         env: Optional[Dict[str, str]] = None) -> Path:
    raw_doc = json.loads(raw.read_bytes())
    if raw_doc.get("format") != "host-gate/1":
        raise CaptureError(f"{raw.name} is not a host-gate/1 result")
    if float(raw_doc["meta"]["devicePixelRatio"]) != float(dpr_label):
        raise CaptureError(f"the page read a display scale of {raw_doc['meta']['devicePixelRatio']}, not {dpr_label}")
    facts = runner_facts(env)
    version = build["firefox_version"]
    argv: List[str] = [str(raw), "--firefox-version", version, "--host", HOST, "--date", date, "--dpr-label", dpr_label,
                       "--raw-kept", raw_kept, "--build-id", build["build_id"], "--build-id-source", build["build_id_source"],
                       "--installer-url", build["url"], "--installer-sha256", build["installer_sha256"],
                       "--gpu-family", GPU_FAMILY, "--os-build", facts["os_build"], "--page-path", PAGE_PATH]
    if import_host_gate.main(argv) != 0:
        raise CaptureError("the importer refused the capture")
    out = cf.CAPTURES / f"firefox-{version}" / HOST / f"{date}-dpr{dpr_label}"
    meta = cf.load_meta(out)
    meta["installer"]["sums_line"] = build["sums_line"]
    meta["installer"]["signature"] = build["signature"]
    meta["runner"] = {"image_os": facts["image_os"], "image_version": facts["image_version"]}
    (out / cf.META_NAME).write_bytes(cf.dumps_meta(meta))
    shutil.copyfile(wire, out / WIRE_NAME)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--build", type=Path, required=True, help="the build.json firefox_build.py wrote")
    ap.add_argument("--wire", type=Path, required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--dpr-label", required=True)
    ap.add_argument("--raw-kept", required=True)
    args = ap.parse_args(argv)
    try:
        out = make(args.raw, json.loads(args.build.read_text(encoding="utf-8")), args.wire, args.date,
                   args.dpr_label, args.raw_kept)
    except (CaptureError, KeyError, OSError) as exc:
        print(f"make_runner_capture: {exc}", file=sys.stderr)
        return 1
    print(f"capture_dir={out.relative_to(cf.REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
