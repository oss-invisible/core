# Retail captures

Every value of the core that was measured on retail Firefox (or on Windows) is
written in one place, and that place is generated from a dated capture by a
script. This page is the format of a capture, the registry that lists every
pinned value, and the way a table is regenerated. The gate is
`tests/test_pinned_tables_are_generated.py`.

## What a capture is

One visit of the host-gate page (`captures/pages/host-gate-1.html`, format
`host-gate/1`) to one retail Firefox on one host. The page returns, as JSON,
everything a page can read that depends on Firefox's code and the host and not
on a session: navigator, screen, media capabilities, WebGPU, WebGL, system
colours and fonts, and about three thousand text and canvas measurements.

```
captures/
  pinned.json                                     the registry (below)
  pages/host-gate-1.html                          the page that takes a capture
  firefox-<version>/<host>/<date>-dpr<x>/
    items.json                                    the answers
    meta.json                                     where, with what, and what the raw file was
```

* `<host>` is `github-runner` or `windows-11-desktop`. A GitHub Windows runner has
  no GPU, a display scale of 1 and no user settings, so it captures only what is
  born in Firefox's code. What depends on Windows 11, on a GPU or on another
  display scale is captured on a Windows 11 desktop, by hand: such an entry of the
  registry says which host it needs.
* `<x>` is the display scale the capture asked for (`1`, `1.25`, `1.5`, `1.75`,
  `2`); `meta.json` also holds the scale the page read.

### items.json

The `items` of the host-gate/1 result, with every image payload
(`{w, h, enc, data}`, `data` being base64 of the pixels or of a PNG) replaced by
`{w, h, enc, sha256}` where `sha256` is the digest of the **decoded** bytes. The
raw file is therefore a few megabytes and `items.json` below one: the digests are
enough to compare a later capture, and the raw file stays in the measurement
archive named in `meta.json`.

The bytes are deterministic: one item per line, keys sorted, compact values,
ASCII (`\uXXXX` escapes), one final newline. `scripts/retail/capture_format.py` is
the only writer, and the test re-serialises every file to check it. `.gitattributes`
keeps captures and the generated tables out of line-ending conversion, so the same
bytes are checked on Linux and on a Windows checkout.

### meta.json

| field | meaning |
|---|---|
| `format`, `items_format` | `retail-capture/1`, `host-gate/1` |
| `firefox_version`, `build_id`, `build_id_source` | the retail version; its BuildID when known, and where that came from |
| `installer` | `{url, sha256}` of the installer the retail was installed from, when known (else `null`) |
| `host` | `{kind, os_build, gpu_family}`: the host kind, the OS build when the capture recorded it (else `null`) and the GPU as a family (for example `Intel integrated`), never the exact string |
| `dpr`, `dpr_asked` | the display scale the page read, and the one asked for |
| `date` | day of the capture |
| `page` | `{path, sha256, served_sha256}`: the page in this repository, and the file that was actually served when they differ (see below) |
| `items` | `{count, images, renamed_keys, redacted, sha256}`: `sha256` is the digest of `items.json` |
| `redacted` | every key (path inside an item, joined by a vertical bar) replaced by `<redacted: capture host>` by the rule below |
| `noise_keys` | the names of the values that vary between two launches of the same build (a random PNG chunk, timings); their values are not kept |
| `raw` | `{sha256, size, kept}`: the digest of the full raw capture and where it is kept |

### What a public capture keeps

A capture is taken from a person's machine and published, so it carries no value of that host that
nothing reads. The importer keeps an item only when (a) a generator reads it
(`gen_tables.READS`), (b) a `by-hand` entry of the registry cites it as its measurement, or (c) it
is a constant of Firefox itself (ICU's `supported_*` lists). Everything else, among it the time
zone, the UTC offsets and local date strings, the voices, the core count, the screen and window
sizes, the installed fonts, the canvas and text measurements and the GPU strings, is replaced by
`<redacted: capture host>`, by the rule `capture_format.KEEP` / `DROP`, and listed in
`meta.json`. A table or an entry that needs another item adds it to `KEEP` in the same commit, with
a re-import. The raw capture, which has everything, stays in the private archive named in
`meta.raw.kept`. `tests/test_pinned_tables_are_generated.py` asserts that no value outside the rule
survives and that every item a reader needs does.

When a name of a studied system is part of a probe name, the importer rewrites it
inside item **keys** (never values) and the page copy carries the same rewrite (its one object key is quoted, so the script stays valid);
`page.served_sha256` is the digest of the page as it was served.

### Importing a capture

```
python scripts/retail/import_host_gate.py RAW.json --firefox-version 159.0 \
    --host windows-11-desktop --date 2026-10-31 --dpr-label 1.25 \
    --raw-kept "<where the full raw file is kept>" [--build-id ...] [--os-build ...]
```

It prints the sha256 of the new `items.json`. A capture is never edited by hand.

## The registry: captures/pinned.json

One entry per pinned value of the core. `m5` is the row of the inventory of values
pinned to the retail (R01 to R50); a row with two tables has two entries. `status`:

| status | meaning |
|---|---|
| `generated` | `generator` regenerates `output` from `sources` (capture directories with the sha256 of their `items.json`). The committed bytes are what it writes |
| `by-hand` | the value is typed in the code. `measurement` names the capture file that bears on it (with `covers` and `differs`), or says it is missing and what evidence exists; `host_needed` names the host a new capture needs; `capture_plan` says when a GitHub runner could capture it |
| `none-page` | no page can read it. `produced_today` says how: a script, a test, or by hand |
| `pending` | a table of `_fpforge/`, handled by the second piece |
| `elsewhere` | written in another repository (`repo`) |

A table is `generated` only when regenerating it from the capture gives exactly the
value the core has. Where the capture gives another value, or does not carry a row,
the table is not changed: it stays `by-hand` and the difference is written in
`measurement.differs`.

## Generated tables

`scripts/retail/gen_tables.py` holds one generator per table; each reads captures
and returns the bytes of one file under `src/invisible_core/data/retail/`, which
the module that needs it loads through `invisible_core._retail.table(name)`:

| table | file | read by |
|---|---|---|
| R01 navigator identity | `navigator.json` | `constants.py` (UA template, platform, oscpu, appVersion) |
| R19 encode support | `media_encode.json` | `prefs._WIN_ENCODE_SUPPORT` |
| R22 WebGPU features and limits | `webgpu.json` | `prefs._WIN_WEBGPU_FEATURES`, `prefs._WIN_WEBGPU_LIMITS`, and the WebGPU test |

```
python scripts/retail/gen_tables.py            write every table
python scripts/retail/gen_tables.py --check    fail when a committed table is not what the captures give
```

A generator holds no retail value: it chooses an order and a spelling. When two
captures disagree it fails instead of choosing. `scripts/` and `captures/` are not
shipped in the wheel; the generated JSON is.

## Adding or refreshing a value

1. Take the capture on the host the registry names (the page and a Firefox, nothing
   else) and import it.
2. Change or add the generator, run it, and commit the table with the capture.
3. Put the entry in `pinned.json` with the new `items_sha256`.

A value with no capture file is never made `generated` by typing it into a
generator.
