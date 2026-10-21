# FogManager (`fogmgr`) — Implementation Plan

A Python application that **manages volumetric (OpenVDB) datasets** from multiple
sources and **exposes a stable Python API** for other applications to query them.

Three goals, in priority order:

1. Orchestrate downloads of the VfxDB volumetric dataset
   (https://huggingface.co/datasets/ryogishiki/VfxDB — 4.61 TB, ~9k sequences,
   ~1M OpenVDB frames, 11 categories) by wrapping the dataset's official
   downloader. We orchestrate; we never reimplement the download spec.
2. Import the user's own generated volumetric data (OpenVDB frames with the same
   category/sequence/sample shape) so it is queryable uniformly alongside VfxDB.
   Data **generation stays external** to this app — the manager only ingests.
3. Expose a stable, versioned **Python library API** (CLI is a thin wrapper over
   it) for other Python applications to query data, status, and file paths.

## Confirmed decisions (do not relitigate)

- **Imported data mirrors the VfxDB on-disk layout** (`category_index.json` +
   VfxDB row schema), so the same catalog readers serve both sources. No internal
   SQLite catalog.
- **API form: Python library only** for now. HTTP is explicitly out of scope but
   the layering must make it additive later.
- **Portable: Windows and Linux/WSL.** Developed on WSL2; must run on both.
- **Imported data shape:** OpenVDB frames arranged as
   `<Category>/<sequence>/<sample>.vdb`. Not arbitrary file types, no richer
   per-sample metadata than the VfxDB row schema allows (extend later if needed).

## Example machine facts (from original planning; re-verify at implementation)

The original plan targeted a Windows machine: `D:\repos\VolumeVFDB` (empty),
`D:\data\vfxdb` data root, `D:\hf-cache\vfxdb` HF cache, ~67 GB free on D:,
Python 3.14.7 miniconda, `zstandard` present, `huggingface_hub`/`rich` missing.
Current dev environment is WSL2 (`/home/jschaupp/repos/FogManager`). The app
must not hardcode either — all paths come from config; see Portability.

## Verified facts (live HF repo + spec, checked 2026-10-19)

- Dataset public, ungated. Default revision `8b2abbf32854c30ef45b7f518c90f064b541f60f`.
- `category_index.json` per category: `{"category", "num_samples", "samples": [row]}`, row = `{id, base_id, seq, folder, vdb_path, meta_path, size_bytes, mtime}`. **`size_bytes` present per row** (~20–29 MB/VDB observed) → exact local space math is possible. `deleted_bad_io_sample` is added locally after install.
- `tools/download_extract_data.py` is a **bootstrap**: it fetches siblings `vfxdb_downloader.py` / `vfxdb_tui.py` from `main` (regardless of `--revision`) next to itself, honors `--cache-dir` even for that fetch → **we must pass `--cache-dir` on every invocation** or code blobs land on the system drive. Requires Python 3.10+.
- Official CLI: `[data_root]` (default `data/vdbset` — always pass absolute), mutually exclusive `--preset smoke|medium|full` | `--percentage P` | `--category NAME` (repeatable, comma-splits) **with** `--max-samples N`; `--include-bad`, `--revision`, `--cache-dir` in any mode; bare invocation = metadata-only install. Exit codes: 130 Ctrl-C, 1 error (prints stage/path/cause, no traceback), 2 non-TTY TUI. It runs its **own space check with margins** and prints a plan summary before downloading.
- Selection is deterministic: tars walked in first-appearance order of each sequence in `category_index.json`; whole tars until cumulative *non-IO-bad* rows ≥ `--max-samples`. State: `<data_root>/.vfxdb/state.json` (pinned revision — a root pinned to a different revision is hard-rejected). IO-bad catalog: `meta/io_bad_vdbs.meta.json` (17,416 records; RisingFlame has exactly 1 bad sample — good smoke-test target).
- Indexes are never cropped — presence on disk is the source of truth for "what's downloaded".
- Deps: `huggingface_hub[cli]>=0.36.0`, `rich`, `zstandard`. (Our app does NOT import rich; only the official tool uses it.)

## Architecture — three layers

```
┌─────────────────────────────────────────────────────────┐
│ Interfaces:  cli.py (argparse)      api.py (public API)   │
├─────────────────────────────────────────────────────────┤
│ Core:        config.py (source registry)                │
│              catalog.py  (layout-generic index readers)  │
│              queries.py   (categories/sequences/samples/ │
│                            status across sources)        │
├─────────────────────────────────────────────────────────┤
│ Sources:     base.py   (Source protocol)                 │
│              vfxdb/   (HF adapter: official.py,         │
│                        selection.py, space.py)           │
│              local.py  (importer for generated data)     │
└─────────────────────────────────────────────────────────┘
```

**The rule that keeps this honest:** CLI and `api.py` contain no business logic;
core contains no source-specific logic; source adapters never import the CLI.
Every read command is implemented once in core/queries, exercised through
`api.py`, and merely surfaced by the CLI. VfxDB-specific knowledge (official
tool invocation, tar selection, space estimation, revision pinning, io-bad
catalog) lives *only* in `sources/vfxdb/`.

### Source protocol (`sources/base.py`)

```python
class Source(Protocol):
    name: str
    kind: str                      # "vfxdb_hf" | "local_import"
    can_download: bool             # vfxdb_hf: True; local_import: False
    def list_categories(self) -> list[str]: ...
    def read_index(self, category: str) -> CategoryIndex: ...
    def status(self, category: str | None = None) -> SourceStatus: ...
```

Download-capable sources additionally implement `estimate(selection)`,
`download(selection, dry_run, force)`, `install_metadata()`, `cache_info()`.
Core queries call only the protocol; CLI commands accept `--source NAME` to
scope, default = all registered sources merged (each row/result tagged with
its source name).

Because imported data mirrors the VfxDB layout, `catalog.py` readers are
**shared** and generic. VfxDB-only artifacts (`.vfxdb/state.json`,
`meta/io_bad_vdbs.*`) are **optional** everywhere they're read: the vfxdb
adapter surfaces pinned revision and io-bad info when present; the local
source simply reports none. `status` completeness logic (sequence complete iff
every index row's file exists on disk) works identically for both.

### VfxDB source adapter (`sources/vfxdb/`)

Everything from the original plan moves here unchanged in substance:

- **Tool acquisition** (`fetch-tool`): resolve revision via `HfApi().dataset_info`,
  `hf_hub_download(repo_id="ryogishiki/VfxDB", filename="tools/download_extract_data.py", revision=<sha>, cache_dir=<config>)`,
  copy to `tools/download_extract_data.py` (gitignored), record sha256 provenance
  in the source's config entry. Re-fetch compares sha256, copies only on change.
  Caveat documented: siblings are pulled from `main` by the bootstrap itself —
  not pin-reproducible.
- **Invocation seam** (`official.py`): always
  `subprocess.run([sys.executable, "-I", <tool>, <abs data_root>, "--cache-dir", <abs cache_dir>, ...])`
  — absolute paths (defeats `data/vdbset` default), `-I` per untrusted-input
  posture, list argv, **inherited stdio** so rich progress streams live
  (never captured). Echo the exact command (`subprocess.list2cmdline`) before
  running. Propagate child exit codes verbatim with a one-line interpretation.
  Never pass `--tui`; never write into `<data_root>/.vfxdb`. This is the
  monkeypatch point for tests (`run_official`).
- `selection.py` (documented tar-selection estimator), `space.py` (byte
  aggregation, `get_paths_info` tar sizes cached per revision, disk guard,
  human sizes) — see Space estimation below.

### Local import source (`sources/local.py`)

`fogmgr import NAME PATH [--category NAME ...] [--index-dir PATH] [--re-index]`

- `NAME` = source name to register; `PATH` = root containing
  `<Category>/<sequence>/<sample>.vdb` (the **import contract**, documented in
  README; generation pipelines must emit this shape).
- Walks the tree, `os.stat`s each file for real `size_bytes`/`mtime`, and writes
  a `category_index.json` per touched category **in the VfxDB row schema**:
  `{id, base_id, seq, folder, vdb_path, meta_path, size_bytes, mtime}` —
  synthesized as: `seq` = sequence directory name, `id` = file stem (or
  `<seq>/<stem>` if stems repeat across sequences — decide in implementation
  and document), `vdb_path` = path relative to the category dir,
  `meta_path` = `null` (no sidecar expected), `folder`/`base_id` = best-effort
  mirroring of VfxDB semantics or `null`.
- Index location: written **next to the data** (inside each category dir) by
  default; `--index-dir PATH` relocates them for read-only source trees.
  `--re-index` re-walks and rewrites (imported sources are static — no resume
  state, no revision pinning).
- Registers `{name: NAME, kind: "local_import", data_root: PATH}` in the config
  source registry. No tar logic, no space estimation, no state files.

### Public Python API (`api.py`)

The stable surface other applications import. Semver from day one; breaking
changes bump major. Thin wrapper over core:

```python
from fogmanager.api import Client
client = Client.load(config_path=None)          # None = default resolution
client.sources()                                # -> [SourceInfo]
client.categories(source=None)                  # -> [CategorySummary]
client.sequences(category, source=None)        # -> [SequenceSummary]
client.samples(category, sequence=None, source=None, limit=None)  # -> [Sample]
client.status(source=None, category=None)       # -> StatusReport
client.paths(sample_id, category, source)      # -> filesystem path to .vdb
```

Return values are dataclasses with `.to_dict()`; CLI `--json` output is exactly
those dicts. Raising `FogManagerError` subclasses is part of the contract.

## On-disk organization

VfxDB source (official layout, **owned by the official tool** — we only read
`.vfxdb/state.json`, never write):

```
<data_root>\                        (e.g. D:\data\vfxdb or /data/vfxdb)
├── .vfxdb\state.json               (tool-managed revision pin)
├── meta\io_bad_vdbs.{jsonl,meta.json}
├── <Category>\category_index.json
├── <Category>\index\<sample>.json
└── <Category>\<sequence>\<sample>.vdb
```

Imported source (same shape, index manager-written, `index/` optional):

```
<imports_root>\                     (per-source, from `import` command)
└── <Category>\
    ├── category_index.json         (manager-generated)
    ├── index\<sample>.json         (optional, only if sidecars exist)
    └── <sequence>\<sample>.vdb     (user-generated)
```

Single shared VfxDB root (all download plans accumulate there — no per-plan
sub-roots); imported sources each get their own root registered in config.

## Config

- Location: `platformdirs.user_config_dir("FogManager")` — resolves to
  `%APPDATA%\FogManager` on Windows, XDG config on Linux/WSL. Overrides:
  `--config PATH`, env `FOGMGR_CONFIG`. Machine paths never belong in the repo.
- Schema:

```json
{
  "schema_version": 1,
  "sources": {
    "vfxdb": {
      "kind": "vfxdb_hf",
      "data_root": "<abs path>",
      "cache_dir": "<abs path, never under the repo>",
      "revision": null,
      "tool": {"path": "...", "sha256s": {}, "fetched_revision": "...", "fetched_at": "..."}
    },
    "<NAME>": {"kind": "local_import", "data_root": "<abs path>", "index_dir": null, "imported_at": "..."}
  }
}
```

`init` creates config + the default `vfxdb` source entry. Unknown `kind` →
clear error (forward compatibility).

## File list

```
fogmanager-repo\
├── pyproject.toml, README.md, .gitignore (tools/*, __pycache__, build)
├── fogmanager\
│   ├── __init__.py, __main__.py
│   ├── cli.py              # argparse tree, global --config/--json/--source, dispatch
│   ├── api.py              # STABLE public API (Client + dataclasses + errors)
│   ├── core\
│   │   ├── __init__.py
│   │   ├── config.py       # load/save/validate, platformdirs resolution, source registry
│   │   ├── catalog.py      # layout-generic defensive readers: category_index.json,
│   │   │                   #   optional .vfxdb/state.json, io_bad files, index/*.json
│   │   └── queries.py      # cross-source categories/sequences/samples/status
│   ├── sources\
│   │   ├── __init__.py, base.py      # Source protocol, source registry resolution
│   │   ├── vfxdb\
│   │   │   ├── __init__.py, official.py    # fetch-tool, build_argv(), run_official()
│   │   │   ├── selection.py               # tar-selection ESTIMATOR
│   │   │   └── space.py                   # byte aggregation, tar sizes, disk guard
│   │   └── local.py       # import command implementation
│   ├── deps.py             # import probes, exact pip command, --fix installer
│   └── commands\{init,doctor,sources,fetch_tool,install_metadata,download,import_data,
│                status,list_categories,inspect,cache_info}.py   # thin, delegate to api
├── tools\.gitkeep          # official bootstrap + runtime siblings land here
└── tests\ (unittest; fixtures\make_mini_root.py generates fake vfxdb AND local roots)
```

`dependencies = ["huggingface_hub[cli]>=0.36.0", "platformdirs", "zstandard"]`
(`zstandard` is required at runtime by the official tool — nothing verified
says the bootstrap pip-installs its own deps, so we install it as insurance;
`rich` comes via the `huggingface_hub[cli]` extra. Our code never imports rich
or zstandard). Stdlib `argparse`; plain text output + `--json` on read
commands; `doctor --fix` still probes and installs all three.

## CLI reference

```
fogmgr [--config PATH] [--json] <command> ...
fogmgr init [--data-root PATH] [--cache-dir PATH] [--force]
fogmgr doctor [--fix]                 # deps/tool/metadata report; --fix runs the pip command
fogmgr sources                        # list registered sources and their kinds
fogmgr fetch-tool [--revision REV] [--refresh]
fogmgr install-metadata [--include-bad] [--revision REV]          # vfxdb source
fogmgr download (--category NAME --max-samples N | --preset ... | --percentage P)
               [--include-bad] [--revision REV] [--dry-run] [--force]   # vfxdb source
fogmgr import NAME PATH [--category NAME ...] [--index-dir PATH] [--re-index]
fogmgr status [--source NAME] [--category NAME]
fogmgr list-categories [--source NAME]
fogmgr inspect CATEGORY [--sequence NAME] [--sample ID] [--limit N] [--source NAME]
fogmgr cache-info                                                            # vfxdb source
```

- `download` pre-flight: doctor-lite; early revision-conflict warning vs pinned
  SHA; estimate + space guard if indexes exist (else warn, don't block — the
  official script installs metadata first and re-checks); `--dry-run` prints
  estimate + exact command and stops. `--category` values passed verbatim.
- `status`: one `os.scandir` walk per category (skip `index/`); per-source and
  per-category table of complete/partial/absent sequences, present/total VDB
  files, on-disk bytes, pinned revision (vfxdb only), drive free space, cache
  usage (vfxdb only). Sequence complete iff every index row has its file on
  disk — uniform across sources.
- `inspect` reads only JSONs — category summary, per-sequence rollups, row
  lists, `--sample` renders per-sample meta JSON (schema unpublished —
  raw-dump fallback). Never opens VDBs, never touches the network.
  `list-categories` for the vfxdb source without installed metadata prints the
  hardcoded 11-category upstream table.
- `cache-info`: HF cache bytes (blobs/incomplete) + the safety note — cache is
  the authoritative blob store; deleting it forfeits resume/reuse.

## Space estimation & guard (vfxdb source only)

1. Requires installed indexes (else skipped with warning). Replicate documented
   selection per mode (max-samples rounds up over whole tars; smoke = 2 tars/cat;
   medium/percentage ≈ `ceil(A·P/100)` tars — labeled estimate).
2. `extracted_bytes` = Σ `size_bytes` of rows in selected tars; `remote_bytes` =
   exact tar sizes via `HfApi().get_paths_info("ryogishiki/VfxDB", ["archives/<Cat>/<seq>.tar", ...])`
   batched + cached per revision (fallback: `extracted_bytes + 1 KiB/member`).
   Display: `≈ X extracted + Y cache = Z needed`.
3. Guard: refuse (exit 1) if `Z + 1 GiB > free(data_root_drive)` unless
   `--force`; warn at 80%. Uses `shutil.disk_usage` (portable). The official
   tool re-checks too — but its check may be broken on Windows (statvfs).
   Space model: extracted data + cached tar blobs coexist → `need ≈ 2× extracted`.

## Portability requirements (Windows and Linux/WSL)

- All paths through `pathlib`; no drive letters, backslashes, or drive-specific
  assumptions in our code. Config location via `platformdirs`.
- Our disk checks use `shutil.disk_usage` (portable) — never `os.statvfs`.
  The **statvfs risk applies only to the official VfxDB tool running on
  Windows**; on Linux it is a non-issue. Keep the probe (below) for Windows
  targets.
- subprocess argv as lists (never shell strings); for display use `shlex.join`
  on POSIX, `subprocess.list2cmdline` on Windows (each platform's own quoting
  rules — one is not correct on the other).
- Tests must not assume a path separator or a config location; fixtures build
  paths with `pathlib`.

## Implementation steps

1. **Environment + compatibility probe (first, before app code):** verify the
   current dev environment (Python version ≥3.10, pip, free disk). Manually
   fetch the bootstrap and run a bare invocation against a scratch data root
   with its own `--cache-dir`. Confirm it runs on the target OS (statvfs crash
   is a Windows-only question), note `vdb_path` form, meta tar size, symlink
   behavior. If statvfs crashes on Windows → report upstream, rely on our guard
   (patched local copy only as flagged last resort).
2. Scaffold: pyproject (package `fogmanager`, console script `fogmgr`),
   editable install, `core/config.py` (platformdirs + source registry),
   `api.py` skeleton, `cli.py` dispatch, `init`, `deps.py`, `sources/base.py`.
3. `sources/vfxdb/official.py` + `fetch-tool` + `doctor` + `sources` command.
4. `core/catalog.py` + `install-metadata` (real run against the VfxDB data root).
5. `sources/vfxdb/selection.py` + `space.py` + `download --dry-run`; validate
   estimator against the official script's printed plan for several combos.
6. `core/queries.py` + `status`, `list-categories`, `inspect`, `cache-info`
   (all source-scoped, `--source` flags).
7. `sources/local.py` + `import` command (validate against a small fixture tree
   of generated data, then query it through both CLI and `api.py`).
8. Tests, README (install, CC BY-NC 4.0 license note for VfxDB, trust model,
   2× space tradeoff, **import contract**, **API reference**, portability),
   .gitignore.

## Verification

- **Unit tests** (stdlib unittest), all through `api.Client` against fixtures:
  - Fixture mini-dataset A (vfxdb kind): 2 categories, an IO-bad row, a partial
    sequence with placeholder .vdb files whose sizes match `size_bytes`,
    `EmptyCat` index-only, `.vfxdb/state.json` + io_bad files present.
  - Fixture mini-dataset B (local_import kind): same shape but **no state.json,
    no io_bad files** — status/inspect must work without them.
  - Both `vdb_path` forms; estimator vs hand-computed plans; guard thresholds;
    config round-trip; CLI validation; exact child argv (monkeypatch
    `run_official`); `import` on a generated tree → correct synthesized rows →
    merged multi-source `status`/`inspect` output.
- **E2E smoke (budget-aware):** `init` → `doctor --fix` → `fetch-tool` →
  `install-metadata` → `download --category RisingFlame --max-samples 5
  --dry-run` (smallest category; adjust N to stay in ~10 GB self-imposed
  budget) → real run → `status` matches disk → rerun same command, verify
  cache reuse (fast, no re-download) → `inspect RisingFlame --limit 20` →
  `import` a small hand-made tree → multi-source `status` and an `api.Client`
  query over both → optionally Ctrl-C mid-download to verify exit 130 + resume.

## Risks

- **statvfs in the official tool on Windows** — probed before any code; our
  `shutil.disk_usage` guard is mitigation. Non-issue on Linux.
- **2× space** (extracted + cached blobs) for vfxdb downloads — explicit in
  every estimate.
- **Siblings always fetched from `main`** — not pin-reproducible; sha256s
  recorded, caveat documented.
- Large `--max-samples` values are one unbounded tar away from blowing the
  budget — dry-run estimate is the gate.
- **Layout drift:** VfxDB's row schema is adopted as the canonical import
  schema; if upstream evolves it, the importer and readers must move together.
  Readers are defensive (unknown fields ignored, missing optional files
  tolerated) to soften this.
- **Index writes into user data dirs:** default `import` writes
  `category_index.json` next to the data; `--index-dir` exists for read-only
  trees. Documented.
- **API stability:** `api.py` is semver'd from day one; other applications
  depend on it — never expose argparse or subprocess internals through it.

Confirm at implementation time (README "verified assumptions" list): `vdb_path`
prefix form; `folder` vs `base_id` as true tar key (cross-check `archives/`
listing + io_bad `sequence` values); per-sample JSON schema; tar overhead
calibration; `get_paths_info` batching limits; imported-row `id` synthesis rule
(file stem vs `<seq>/<stem>`); preferred default `index-dir` policy.