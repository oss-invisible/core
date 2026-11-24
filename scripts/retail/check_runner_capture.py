"""Which tables of the registry a capture regenerates, and which it does not.

    python scripts/retail/check_runner_capture.py CAPTURE_DIR [--json]

CAPTURE_DIR holds items.json and meta.json (a directory under captures/, or the one a runner
capture made). For every `generated` entry of captures/pinned.json the table's generator is run
as if this capture were each of its sources, and its bytes are compared with the committed table:

  regenerates   the generator writes exactly the committed bytes from this capture
  differs       it writes something else: the keys that differ are listed
  cannot        the generator refuses this capture (a value it reads is absent, or two sources
                disagree) and says why

An entry whose `host_needed` names the `github-runner` is runner-capturable: a capture from a
runner has to regenerate it, and the exit status is 1 when one does not. The other entries are
reported too (a runner has no GPU and no Windows 11 user settings, so they may rightly differ) and
never change the exit status. Exit 2: CAPTURE_DIR is not a capture.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_format as cf  # noqa: E402
import gen_tables as gen  # noqa: E402

REGISTRY = cf.CAPTURES / "pinned.json"
RUNNER = "github-runner"


def runner_capturable(entry: Dict[str, Any]) -> bool:
    return RUNNER in str(entry.get("host_needed", ""))


def _differing_keys(committed: bytes, regenerated: bytes) -> List[str]:
    try:
        a, b = json.loads(committed), json.loads(regenerated)
    except ValueError:
        return ["(not JSON)"]
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return ["$"]
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def check(capture_dir: Path, registry: Path = REGISTRY) -> List[Dict[str, Any]]:
    """One result per generated entry of the registry, in registry order."""
    capture_dir = Path(capture_dir)
    entries = json.loads(Path(registry).read_text(encoding="utf-8"))["entries"]
    original = cf.capture_dir
    results: List[Dict[str, Any]] = []
    try:
        for entry in entries:
            if entry["status"] != "generated":
                continue
            name = entry["generator"].split(":", 1)[1]
            fn, _out, sources = gen.GENERATORS[name]
            result: Dict[str, Any] = {"id": entry["id"], "table": name, "output": entry["output"],
                                      "runner_capturable": runner_capturable(entry)}
            cf.capture_dir = lambda rel, sources=sources: capture_dir if rel in sources else original(rel)
            try:
                regenerated = fn(sources)
            except SystemExit as exc:  # a generator refuses by exiting with its reason
                result.update(status="cannot", detail=str(exc.code))
            except (KeyError, TypeError, ValueError) as exc:
                result.update(status="cannot", detail=f"{type(exc).__name__}: {exc}")
            else:
                committed = (cf.REPO / entry["output"]).read_bytes()
                if regenerated == committed:
                    result.update(status="regenerates", detail="byte for byte")
                else:
                    result.update(status="differs", detail=f"keys that differ: {_differing_keys(committed, regenerated)}")
            results.append(result)
    finally:
        cf.capture_dir = original
    return results


def failed(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The results that make the check red: a runner-capturable table that is not regenerated."""
    return [r for r in results if r["runner_capturable"] and r["status"] != "regenerates"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", type=Path)
    ap.add_argument("--json", action="store_true", help="print the results as JSON")
    args = ap.parse_args(argv)
    if not (args.capture / cf.ITEMS_NAME).is_file() or not (args.capture / cf.META_NAME).is_file():
        print(f"{args.capture}: no items.json and meta.json: not a capture", file=sys.stderr)
        return 2
    meta = cf.load_meta(args.capture)
    results = check(args.capture)
    if args.json:
        print(json.dumps({"capture": {"firefox_version": meta["firefox_version"], "host": meta["host"]["kind"],
                                      "date": meta["date"]}, "tables": results}, indent=2, sort_keys=True))
    else:
        print(f"capture: firefox {meta['firefox_version']} on {meta['host']['kind']}, {meta['date']}")
        for r in results:
            mark = {"regenerates": "ok  ", "differs": "DIFF", "cannot": "CANT"}[r["status"]]
            scope = "runner-capturable" if r["runner_capturable"] else "needs another host"
            print(f"{mark} {r['id']:<10} {r['table']:<14} {r['status']}: {r['detail']}  [{scope}]")
    bad = failed(results)
    if bad:
        print("RED: " + ", ".join(f"{r['id']} ({r['status']})" for r in bad) + " is runner-capturable and is not regenerated",
              file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
