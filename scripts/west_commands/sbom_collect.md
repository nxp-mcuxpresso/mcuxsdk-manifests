# SBOM Collection with `west sbom_collect`

## Overview

`west sbom_collect` is a West extension command that produces a single,
workspace-level Software Bill of Materials (SBOM) for a MCUXpresso SDK
repository-based workspace. Every SDK component repository (drivers, CMSIS,
middleware, examples, tools, ...) already ships its own per-project SBOM file
in [SPDX](https://spdx.dev/) 2.3 JSON format. `west sbom_collect` walks the
repositories referenced by your West manifest, gathers those per-project
SBOM files, and merges them into one consolidated SPDX document describing
exactly what is present in your workspace.

This lets you answer questions such as "which open-source components and
licenses are included in my SDK workspace/package?" without having to open
every repository individually.

## Prerequisites

- A West workspace initialized from the MCUXpresso SDK manifests
  (`west init` / `west update`).
- Run the command from anywhere inside that workspace; West resolves the
  workspace root (`topdir`) automatically.

## Basic usage

```bash
# Merge SBOMs from every project in the manifest
west sbom_collect -o SBOM.spdx.json

# Merge SBOMs from a specific subset of projects only
west sbom_collect core mcu-sdk-examples mcu-sdk-components -o SBOM.spdx.json

# Preview what would be collected without writing a file
west sbom_collect --dry-run

# Enable verbose (debug) logging while collecting
west sbom_collect -o SBOM.spdx.json -v
```

If no project names are given, all projects enabled in the current manifest
(respecting any West `group-filter`) are scanned.

## Command-line options

| Option | Description |
|---|---|
| `projects` (positional) | Optional list of project names to collect from. If omitted, all manifest projects are processed. |
| `-o, --output` | Output file path for the merged SBOM. Default: `MCUXpresso-SDK-SBOM.spdx.json`. Relative paths are resolved against the workspace root. |
| `--sbom-pattern` | Filename pattern used to locate each project's SBOM file. Default: `*SBOM*.json`. Supports shell-style wildcards. |
| `--dry-run` | Report what would be collected and merged, without writing an output file. |
| `-v, --verbose` | Enable debug-level logging (per-project root/package/relationship counts, etc.). |

## What gets merged

For each project, `west sbom_collect`:

1. Searches the project's directory (and common subdirectories such as
   `sbom/`, `SBOM/`, `docs/`) for a file matching `--sbom-pattern`.
2. Parses and validates the file as an SPDX 2.3 document.
3. Determines that project's top-level (`DESCRIBES`) package(s) and follows
   containment/link relationships to find every package the project
   actually includes.
4. Adds those packages to the merged document, de-duplicating packages that
   appear in more than one project (matched by name, version, and download
   location) so shared dependencies are listed only once.

Projects that are disabled via manifest group-filters are skipped silently.
Enabled projects with no matching SBOM file are reported as warnings so gaps
are visible rather than silently ignored.

## Merged SBOM structure

The output follows the same NXP SBOM policy used by the per-component SBOM
generator, so a merged workspace SBOM and a per-repository SBOM look and
validate the same way:

- SPDX version `SPDX-2.3`, `dataLicense: CC0-1.0`.
- A single root package (`MCUXpresso-SDK`) that `DESCRIBES` the document and
  `CONTAINS` every top-level package contributed by each merged project.
- `versionInfo` on the root package is set from the workspace's
  `mcuxsdk/MCUX_VERSION` file (for example `26.09.00`), so the merged SBOM
  reports the SDK release it was generated from.
- `creators` is always `["Organization: NXP"]`, and every package's
  `supplier` is normalized to `NOASSERTION` per policy.
- Per-package fields such as license and `downloadLocation` are preserved
  from the original per-project SBOM whenever present, and fall back to
  `NOASSERTION` only when missing.

## Relationship to `west update_board`

`west sbom_collect` is also used internally by `west update_board`, so you
do not need to run it directly in most workflows:

- **`west update_board --list-sbom -o <file>.json`** runs the same
  collection/merge logic as a standalone step, without building a package.
  Useful for auditing a workspace's SBOM independent of packaging.
- **`west update_board --set board <name> -o package.zip`** (repository ZIP
  packaging) automatically includes a merged `SBOM.spdx.json`, generated
  with the same logic, inside every package it produces. This happens
  unconditionally — no extra flag is required.

## Troubleshooting

- **"No SBOM files found in any project"**: check that `--sbom-pattern`
  matches the actual filenames used by your repositories, and that the
  projects you expect are enabled (not filtered out by a manifest
  group-filter).
- **Warnings about missing `DESCRIBES` relationships**: an older or
  malformed per-project SBOM may lack a `DESCRIBES` relationship. The
  merger falls back to inferring roots from packages with no incoming
  containment edge, but this is logged as a warning so the source SBOM can
  be fixed upstream.
- **`versionInfo` on the root package is `NOASSERTION`**: the workspace is
  missing `mcuxsdk/MCUX_VERSION`, or the file could not be parsed. Verify
  the `mcuxsdk` repository is present and up to date.
