# VfxDB Download Manager (`vfxdb`) — Implementation Plan

## Context

The user wants to download parts of the VfxDB volumetric dataset (https://huggingface.co/datasets/ryogishiki/VfxDB) — 4.61 TB, ~9k sequences, ~1M OpenVDB frames, 11 categories — and wants a Python CLI app in `D:\repos\VolumeVFDB` (empty, not a git repo) to **manage** that process. Confirmed decisions:

- **Scope:** selected categories with sample limits (D: has ~67 GB free; C: ~16 GB).
- **Architecture:** wrap the dataset's official downloader (`tools/download_extract_data.py` from the HF repo). Card says do NOT clone the repo — fetch the script and run it. Our app orchestrates; it never reimplements the download spec.
- **Interface:** scriptable CLI. **Features:** download orchestration + status/progress + dataset inspection.

## Verified facts (live HF repo + spec, 2026-10-19)

- Dataset public, ungated. Default revision `8b2abbf32854c30ef45b7f518c90f064b541f60f`.
- `category_index.json` per category: `{"category", "num_samples", "samples": [row]}`, row = `{id, base_id, seq, folder, vdb_path, meta_path, size_bytes, mtime}`. **`size_bytes` present per row** (~20–29 MB/VDB observed) → exact local space math is possible. `deleted_bad_io_sample` is added locally after install.
- `tools/download_extract_data.py` is a **bootstrap**: it fetches siblings `vfxdb_downloader.py` / `vfxdb_tui.py` from `main` (regardless of `--revision`) next to itself, honors `--cache-dir` even for that fetch → **we must pass `--cache-dir` on every invocation** or code blobs land on C:. Requires Python 3.10+.
- Official CLI: `[data_root]` (default `data/vdbset` — always pass absolute), mutually exclusive `--preset smoke|medium|full` | `--percentage P` | `--category NAME` (repeatable, comma-splits) **with** `--max-samples N`; `--include-bad`, `--revision`, `--cache-dir` in any mode; bare invocation = metadata-only install. Exit codes: 130 Ctrl-C, 1 error (prints stage/path/cause, no traceback), 2 non-TTY TUI. It runs its **own space check with margins** and prints a plan summary before downloading.
- Selection is deterministic: tars walked in first-appearance order of each sequence in `category_index.json`; whole tars until cumulative *non-IO-bad* rows ≥ `--max-samples`. State: `<data_root>/.vfxdb/state.json` (pinned revision — a root pinned to a different revision is hard-rejected). IO-bad catalog: `meta/io_bad_vdbs.meta.json` (17,416 records; RisingFlame has exactly 1 bad sample — good smoke-test target).
- Indexes are never cropped — presence on disk is the source of truth for "what's downloaded".
- Deps: `huggingface_hub[cli]>=0.36.0`, `rich`, `zstandard`. Machine: Python 3.14.7 (miniconda), `python -m pip` works, `zstandard` present, `huggingface_hub`/`rich` missing.
- **Risk:** the downloader uses `os.statvfs`, which **does not exist on Windows** — may be guarded or may crash. Probe FIRST (step 1 below). Our pre-flight space guard is the mitigation.
- **Space model:** extracted data + cached tar blobs coexist → `need ≈ 2× extracted size`. With 67 GB free, only ~30 GB of final data fits. Estimates must show both numbers.

## On-disk organization (confirmed by user)

Single shared root, official layout — all download plans accumulate in one data root; categories each get their own named directory inside it:

```
D:\data\vfxdb\
├── .vfxdb\state.json        (tool-managed revision pin; wrapper reads only)
├── meta\io_bad_vdbs.{jsonl,meta.json}
├── <Category>\category_index.json
├── <Category>\index\<sample>.json
└── <Category>\<sequence>\<sample>.vdb
```

Metadata installed once, resume state shared, `status` sees everything in one command. No per-plan sub-roots.

## Design

- Package `vfxdb`, console script `vfxdb` (also `python -m vfxdb`). `pyproject.toml` (setuptools), `dependencies = ["huggingface_hub[cli]>=0.36.0", "rich", "zstandard"]` → one `python -m pip install -e .` bootstraps the whole stack. Stdlib `argparse`; our own output is plain text + `--json` on read commands (we do NOT import rich in our code).
- Config: `%APPDATA%\VolumeVFDB\config.json` (machine paths don't belong in a repo), `--config PATH` + `VFXDB_CONFIG` overrides. Schema: `{schema_version, data_root: D:\data\vfxdb, cache_dir: D:\hf-cache\vfxdb, revision: null|40-hex, tool: {path, sha256s, fetched_revision, fetched_at}}`. Both paths on D:, never under the repo. The official script owns `<data_root>/.vfxdb/state.json`; we only read it.
- **Tool acquisition** (`fetch-tool`): resolve revision via `HfApi().dataset_info`, `hf_hub_download(repo_id="ryogishiki/VfxDB", filename="tools/download_extract_data.py", revision=<sha>, cache_dir=<config.cache_dir>)`, copy to `D:\repos\VolumeVFDB\tools\download_extract_data.py` (gitignored), record sha256 provenance in config. Re-fetch compares sha256, copies only on change. Caveat documented: siblings are pulled from `main` by the bootstrap itself — not pin-reproducible.
- **Invocation seam** (`official.py`): always `subprocess.run([sys.executable, "-I", <tool>, <abs data_root>, "--cache-dir", <abs cache_dir>, ...])` — absolute paths (defeats `data/vdbset` default), `-I` per untrusted-input posture, list argv, **inherited stdio** so rich progress streams live (never captured). Echo the exact command (`subprocess.list2cmdline`) before running. Propagate child exit codes verbatim with a one-line interpretation. Never pass `--tui`; never write into `<data_root>/.vfxdb`.

## File list

```
D:\repos\VolumeVFDB\
├── pyproject.toml, README.md, .gitignore (tools/*, __pycache__, build)
├── vfxdb\
│   ├── __init__.py, __main__.py
│   ├── cli.py          # argparse tree, global --config/--json, dispatch, exit-code policy
│   ├── config.py       # load/save/validate, APPDATA resolution, defaults
│   ├── deps.py         # import probes, exact pip command, --fix installer, shutil.disk_usage helpers
│   ├── official.py     # fetch-tool, build_argv(), run_official() seam (monkeypatch point for tests)
│   ├── catalog.py      # defensive readers: category_index.json, .vfxdb/state.json, io_bad files, index/*.json
│   ├── selection.py    # documented tar-selection ESTIMATOR (first-appearance, IO-bad exclusion, whole-tar rounding)
│   ├── space.py        # byte aggregation, HfApi.get_paths_info tar sizes (cached JSON), disk guard, human sizes
│   └── commands\{init,doctor,fetch_tool,install_metadata,download,status,list_categories,inspect,cache_info}.py
├── tools\.gitkeep      # official bootstrap + runtime siblings land here
└── tests\ (unittest; fixtures\make_mini_root.py generates a fake DATA_ROOT)
```

## CLI reference

```
vfxdb [--config PATH] [--json] <command> ...
vfxdb init [--data-root PATH] [--cache-dir PATH] [--force]
vfxdb doctor [--fix]                 # deps/tool/metadata report; --fix runs the pip command
vfxdb fetch-tool [--revision REV] [--refresh]
vfxdb install-metadata [--include-bad] [--revision REV]
vfxdb download (--category NAME --max-samples N | --preset ... | --percentage P)
               [--include-bad] [--revision REV] [--dry-run] [--force]
vfxdb status [--category NAME]
vfxdb list-categories
vfxdb inspect CATEGORY [--sequence NAME] [--sample ID] [--limit N]
vfxdb cache-info
```

- `download` pre-flight: doctor-lite; early revision-conflict warning vs `.vfxdb/state.json` pinned SHA; estimate + space guard if indexes exist (else warn, don't block — official script installs metadata first and re-checks); `--dry-run` prints estimate + exact command and stops. `--category` values passed through verbatim (upstream comma-splits).
- `status` (one `os.scandir` walk per category, skip `index/`; sequence := first component of `vdb_path` after stripping an optional category prefix — confirm exact form at first install): per-category table of complete/partial/absent sequences, present/total VDB files, on-disk bytes, pinned revision, drive free space, cache usage. Sequence complete iff every index row has its file on disk.
- `inspect` reads only JSONs — category summary, per-sequence rollups, row lists, `--sample` renders the per-sample meta JSON (schema unpublished — raw-dump fallback). Never opens VDBs, never touches the network. `list-categories` without installed metadata prints the hardcoded 11-category upstream table.
- `cache-info`: bytes under HF cache (blobs/incomplete), and the safety note — cache is the authoritative blob store; deleting it forfeits resume/reuse for those tars.

## Space estimation & guard

1. Requires installed indexes (else skipped with warning). Replicate documented selection per mode (max-samples rounds up over whole tars; smoke = 2 tars/cat; medium/percentage ≈ `ceil(A·P/100)` tars — labeled estimate).
2. `extracted_bytes` = Σ `size_bytes` of rows in selected tars; `remote_bytes` = exact tar sizes via `HfApi().get_paths_info("ryogishiki/VfxDB", ["archives/<Cat>/<seq>.tar", ...])` batched + cached per revision (fallback: `extracted_bytes + 1 KiB/member`). Display both: `≈ X extracted + Y cache = Z needed`.
3. Guard: refuse (exit 1) if `Z + 1 GiB > free(data_root_drive)` unless `--force`; warn at 80%. The official tool re-checks too — but its check may be broken on Windows (statvfs).

## Implementation steps

1. **Windows compatibility probe (first, before app code):** manually fetch the bootstrap, run bare invocation against scratch `D:\data\vfxdb-probe` with `--cache-dir D:\hf-cache\vfxdb-probe`. Confirm it runs on Windows, note `vdb_path` form, meta tar size, symlink behavior. If statvfs crashes → report upstream, rely on our guard (patched local copy only as flagged last resort).
2. Scaffold: pyproject, package skeleton, editable install, `init`/`config.py`/`deps.py`.
3. `official.py` + `fetch-tool` + `doctor`.
4. `catalog.py` + `install-metadata` (real run against `D:\data\vfxdb`).
5. `selection.py` + `space.py` + `download --dry-run`; validate estimator against the official script's printed plan for several combos.
6. `status`, `list-categories`, `inspect`, `cache-info`.
7. Tests, README (install, CC BY-NC 4.0 license note, trust model, 2× space tradeoff, command cookbook), .gitignore.

Confirm at implementation time (README "verified assumptions" list): `vdb_path` prefix form; `folder` vs `base_id` as true tar key (cross-check `archives/` listing + io_bad `sequence` values); per-sample JSON schema; tar overhead calibration; `get_paths_info` batching limits.

## Verification

- **Unit tests** (stdlib unittest): fixture mini-dataset (2 categories, an IO-bad row, a partial sequence with placeholder .vdb files whose sizes match `size_bytes`, `EmptyCat` index-only) → status counts, both `vdb_path` forms, estimator vs hand-computed plans, guard thresholds, config round-trip, CLI validation, exact child argv (monkeypatch `run_official`).
- **E2E smoke (budget-aware):** `init` → `doctor --fix` → `fetch-tool` → `install-metadata` → `download --category RisingFlame --max-samples 5 --dry-run` (smallest category; adjust N to stay in ~10 GB self-imposed budget) → real run → `status` matches disk → rerun same command, verify cache reuse (fast, no re-download) → `inspect RisingFlame --limit 20` → optionally Ctrl-C mid-download to verify exit 130 + resume.

## Risks

- **statvfs on Windows** — probed before any code; our guard is mitigation.
- **2× space** (extracted + cached blobs) — explicit in every estimate; ~30 GB of final data fits now.
- **Siblings always fetched from `main`** — not pin-reproducible; sha256s recorded, caveat documented.
- Large `--max-samples` values are one unbounded tar away from blowing the budget — dry-run estimate is the gate.