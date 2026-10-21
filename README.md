# FogManager (`fogmgr`)

A Python application that **manages volumetric (OpenVDB) datasets** from
multiple sources and **exposes a stable, versioned Python API** for other
applications to query them.

Three goals, in priority order:

1. **Orchestrate downloads of the [VfxDB](https://huggingface.co/datasets/ryogishiki/VfxDB)
   volumetric dataset** (~4.6 TB, ~9k sequences, ~1M OpenVDB frames) by wrapping the
   dataset's *official* downloader. FogManager orchestrates; it never
   reimplements the download spec.
2. **Import your own generated volumetric data** (OpenVDB frames in the same
   category/sequence/sample shape) so it is queryable uniformly alongside
   VfxDB. Data generation stays external — the manager only ingests.
3. **Expose a stable Python library API** (`fogmanager.api`); the CLI is a
   thin wrapper over it.

## Install

```bash
# conda env "down" (Python >= 3.10) or any Python 3.10+
pip install -e .
```

Runtime dependencies: `huggingface_hub[cli]>=0.36.0`, `platformdirs`,
`rich`, `zstandard`. Rich and zstandard are required by the *official VfxDB
tool*, not by FogManager itself; they are installed as insurance because the
official bootstrap does not install its own runtime deps. (Note: with
`huggingface_hub` 2.x the `[cli]` extra no longer exists; `rich` is listed
explicitly, which is why it appears separately.)

## Quick start

```bash
fogmgr init --data-root /data/vfxdb --cache-dir /data/hf-cache
fogmgr doctor --fix              # probe deps/tool/metadata; --fix pip-installs
fogmgr fetch-tool                # fetch the official bootstrap (sha256 recorded)
fogmgr install-metadata          # bare official invocation = metadata-only install
fogmgr list-categories           # 11 upstream categories once metadata exists
fogmgr download --category RisingFlame --max-samples 5 --dry-run   # estimate + command, no side effects
fogmgr download --category RisingFlame --max-samples 5             # real run
fogmgr status                    # per-source/per-category completeness
fogmgr inspect RisingFlame --limit 20
fogmgr import mysims /data/my-vdbs                 # see "import contract"
fogmgr cache-info                # HF cache usage + safety note
```

Global flags `--config PATH` and `--json` may appear before or after the
subcommand. Config resolution: `--config` > `FOGMGR_CONFIG` env var >
`platformdirs.user_config_dir("FogManager")/config.json`
(`%APPDATA%\FogManager` on Windows, XDG config dir on Linux).

## Architecture

```
Interfaces:  cli.py (argparse)         api.py (PUBLIC API)
Core:        config.py   catalog.py   queries.py   model.py
Sources:     base.py   vfxdb/ (official.py, selection.py, space.py, source.py)   local.py
```

- **CLI and `api.py` contain no business logic.**
- **Core contains no source-specific logic** — every read command is
  implemented once in `core/queries.py` against the Source protocol.
- **Source adapters never import the CLI.** VfxDB-specific knowledge (official
  tool invocation, tar selection, space estimation, revision pinning, the
  io-bad catalog) lives only in `sources/vfxdb/`.
- Read commands never open VDB files and never touch the network (only the
  download estimate fetches remote tar sizes).

### The import contract

Imported data must be laid out as:

```
<root>/<Category>/<sequence>/<sample>.vdb
```

`fogmgr import NAME PATH` walks that tree, records real sizes/mtimes, and
writes a `category_index.json` per category **in the VfxDB row schema**
(`{id, base_id, seq, folder, vdb_path, meta_path, size_bytes, mtime}`) so the
same catalog readers serve both sources. Synthesis rules:

- `seq`/`folder`: the sequence directory name (`folder` mirrors VfxDB
  semantics of being `vdb_path`'s first component).
- `id`: the file stem; if stems repeat across sequences in a category, id
  becomes `<seq>/<stem>` (uniformly for every colliding occurrence).
- `base_id`: the file stem (best-effort mirror of VfxDB semantics).
- `vdb_path`: path relative to the category dir.
- `meta_path`: `index/<stem>.json` if a sidecar exists, else null.
- `size_bytes`/`mtime`: real filesystem values.

Indexes are written **next to the data** by default; `--index-dir PATH`
relocates them for read-only trees. `--re-index` re-walks and rewrites.
Imported sources are static — no resume state, no revision pinning.

## Python API

```python
from fogmanager.api import Client

client = Client.load()                        # None = default config resolution
client.sources()                              # -> [SourceInfo]
client.categories(source=None)                # -> [CategorySummary]
client.sequences("RisingFlame")               # -> [SequenceSummary]
client.samples("RisingFlame", limit=20)       # -> [Sample]  (sequence/source/limit optional)
client.status(source=None, category=None)     # -> StatusReport
client.paths("s0000__n0075", "RisingFlame")   # -> [Sample]  (.path is the filesystem path)
client.sample_meta("s0000__n0075", "RisingFlame")  # raw per-sample meta JSON
client.import_source("mysims", "/data/my-vdbs")    # -> ImportResult

# VfxDB orchestration (raises if no vfxdb source is registered):
client.fetch_tool(revision=None, refresh=False)
client.install_metadata()
client.download_estimate(categories=["RisingFlame"], max_samples=5)  # -> DownloadEstimate
client.cache_info()
```

All return values are dataclasses with `.to_dict()` — CLI `--json` output is
exactly those dicts. Every intentional error is a
`fogmanager.api.FogManagerError` subclass; catching that is the contract.
`api.py` is semver'd from day one; breaking changes bump the major. It never
exposes argparse or subprocess internals.

## Space model and the 2× tradeoff

For VfxDB downloads, extracted frames and the cached tar blobs **coexist** on
the target filesystem, so budget **≈ 2× the extracted size** until you choose
to delete cache blobs (`fogmgr cache-info` reports them; the cache is the
authoritative blob store — deleting it forfeits resume/reuse). The download
estimate replicates the official whole-tar selection, sums `size_bytes` from
the installed indexes, fetches exact remote tar sizes per revision (with a
labeled `extracted + 1 KiB/member` fallback), and refuses to run when
`needed + 1 GiB > free` unless you pass `--force`; it warns above 80%.
FogManager's checks use `shutil.disk_usage` (portable); it never calls
`os.statvfs`.

## Trust model

- The official downloader is the only code FogManager ever executes, and only
  through a narrow seam: `run_official` with `[sys.executable, -I, <tool>,
  <abs data_root>, --cache-dir <abs cache_dir>, ...]` — list argv, absolute
  paths, inherited stdio, exit codes propagated verbatim (130 = Ctrl-C, 1 =
  error, 2 = non-TTY TUI). `--tui` is never passed.
- The tool's bootstrap fetches its sibling implementation files from `main`
  regardless of `--revision`; the fetch is therefore **not fully
  pin-reproducible**. sha256 provenance for the bootstrap (and any cached
  siblings) is recorded in the config's `tool` block. `--cache-dir` is passed
  on every invocation so code blobs land in your cache, never on the system
  drive.
- Only the official tool writes into `<data_root>/.vfxdb`; FogManager reads
  `state.json` for the pinned revision and never writes there.

## Dataset license

VfxDB is distributed under **CC BY-NC 4.0** (non-commercial). Respect that
license for anything you download via FogManager; imported data carries no
license from this project.

## Portability (Windows + Linux/WSL)

- All paths go through `pathlib`; config lives via `platformdirs`; machine
  paths never belong in the repo.
- Disk checks use `shutil.disk_usage`. (The `os.statvfs` crash-on-Windows
  concern from early planning turned out to be a non-issue: the official
  tool wraps that call in `try/except (AttributeError, OSError)` — verified
  in its source.)
- subprocess argv is always a list; display quoting is `shlex.join` on POSIX
  and `subprocess.list2cmdline` on Windows — each platform's own rules.
- No test assumes a path separator or a config location.

## Command reference

```
fogmgr [--config PATH] [--json] <command> ...
fogmgr init [--data-root PATH] [--cache-dir PATH] [--force]
fogmgr doctor [--fix]
fogmgr sources
fogmgr fetch-tool [--revision REV] [--refresh]
fogmgr install-metadata [--include-bad] [--revision REV]
fogmgr download (--category NAME --max-samples N | --preset smoke|medium|full | --percentage P)
               [--include-bad] [--revision REV] [--dry-run] [--force]
fogmgr import NAME PATH [--category NAME ...] [--index-dir PATH] [--re-index]
fogmgr status [--source NAME] [--category NAME]
fogmgr list-categories [--source NAME]
fogmgr inspect CATEGORY [--sequence NAME] [--sample ID] [--limit N] [--source NAME]
fogmgr cache-info [--source NAME]
```

`download --dry-run` prints the estimate and the exact child command and
stops — it never downloads. `--category` values are passed to the official
tool verbatim. `list-categories` for a vfxdb source without installed
metadata prints the hardcoded 11-category upstream table (with an install
hint).

## Verified assumptions (checked against the live repo + tool source, 2026-10-19)

- Repo revision `8b2abbf32854c30ef45b7f518c90f064b541f60f`; dataset public and
  ungated at time of check.
- **The tar key is `vdb_path`'s first component** (e.g. `"0/s0000__n0075.vdb"`
  → tar `archives/<Category>/0.tar`), *not* the row's `base_id`, and the row's
  `folder` field only echoes it. Archive order is first appearance of that
  folder in the index rows. (The io-bad manifest's `sequence` field equals
  the folder — consistent.)
- Row schema `{id, base_id, seq, folder, vdb_path, meta_path, size_bytes,
  mtime}` confirmed; `meta_path` stems are opaque hashes **not** derivable
  from `id`; `size_bytes` present per row (~20–29 MB/VDB observed).
- Per-sample meta JSON schema is unpublished → `inspect --sample` raw-dumps it.
- Tar overhead calibration (RisingFlame, ~52 members/tar ≈ 1.1 GB):
  measured ~107 KiB/tar; the 1 KiB/member fallback is conservative.
- The official tool's own space-check margin is
  `min(1 GiB, max(64 MiB, bytes//20))`; statvfs is exception-guarded there.
- `huggingface_hub` 2.x note: `hf_hub_download` on this dataset **must** pass
  `repo_type="dataset"` (the default misbuilds the URL → misleading 401); the
  `[cli]` extra no longer exists, hence explicit `rich`.

**Verified live through FogManager (2026-10-19, WSL2):** `init` → `fetch-tool`
→ `install-metadata` (official run, exit 0; 1,036,656 per-sample JSONs, 11
category indexes, pinned revision) → `status` / `list-categories` / `inspect`
/ `inspect --sample` (raw per-sample meta with `bbox_max`, `base_id`, ...) →
`download --dry-run` in both `--category --max-samples` mode (1 tar / 51 rows
/ 2.2 GiB needed — the estimator matched the official tar for RisingFlame
folder `0`) and `--preset smoke` (22 tars, 2,082 normal + 125 io-bad rows
detected). `fogmgr import` verified against a generated tree with a merged
multi-source API query.

Still unverified: an actual tar download (blocked by choice/budget), vdb_path
variations beyond the sampled categories, `get_paths_info` batch limits at
scale.

Notes from the live run: a metadata-only install is ≈ **4.5 GB on disk** (the
per-sample JSON sidecars dominate — 1M+ small files), plus ≈ 334–666 MB HF
cache (indexes + `meta/vfxdb_meta.tar.zst`); expect many-inode filesystems to
feel it. `doctor`/`status` surface these numbers.

## Development

```bash
python -m unittest discover -s tests     # stdlib unittest; ~100 tests, network-free
```

Tests build fixture roots with `tests/fixtures/make_mini_root.py` (fake vfxdb
root with partial sequence, io-bad row, EmptyCat, state.json; local root with
no control artifacts) and assert everything through `api.Client` or the CLI,
including the exact official-tool argv via the `run_official` seam.