# Copyright 2025 NXP
#
# SPDX-License-Identifier: BSD-3-Clause

#!/usr/bin/env python3
"""
West extension command to merge SBOM files from all manifest projects.
"""

import argparse
import fnmatch
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

from west.commands import WestCommand
from west.manifest import Manifest
from west import log

from sbom_merge import SbomMerger
from spdx23_validator import SPDX23Validator

# Optional internal overlay: NXP's SBOM policy on top of generic SPDX 2.3.
# Lives in bifrost (Bitbucket-only, never mirrored to public GitHub) so
# company policy details never ship in this publicly-mirrored repo. When
# bifrost isn't checked out (e.g. a public GitHub-only workspace), only
# the generic SPDX 2.3 validation above runs.
_POLICY_OVERLAY_RELPATH = Path('bifrost') / 'scripts' / 'nxp_sbom_policy_validator.py'


def _load_policy_overlay(workspace_root: Path):
    """Load the optional internal NXP-policy overlay validator, if present.

    Returns the loaded module (exposing `validate(sbom_dict) -> list[dict]`),
    or None if bifrost is not checked out in this workspace.
    """
    overlay_path = workspace_root / _POLICY_OVERLAY_RELPATH
    if not overlay_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location('nxp_sbom_policy_validator', overlay_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _format_overlay_issue(issue: Dict) -> str:
    loc = f" [{issue['location']}]" if issue.get('location') else ''
    return f"[{issue.get('severity')}] ({issue.get('section')}){loc} {issue.get('message')}"


class SbomCollect(WestCommand):
    def __init__(self):
        super().__init__(
            'sbom_collect',
            'collect SBOM files from manifest projects',
            'Collect SBOM files matching a pattern from specified manifest projects '
            'and create a workspace-level SBOM file. If no projects are specified, '
            'collects from all projects.',
            accepts_unknown_args=False)

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(
            self.name,
            help=self.help,
            description=self.description)
        
        parser.add_argument(
            'projects',
            nargs='*',
            help='List of project names to collect SBOM from. If not specified, collects from all projects.')
        
        parser.add_argument(
            '-o', '--output',
            default='MCUXpresso-SDK-SBOM.spdx.json',
            help='Output file path for merged SBOM (default: MCUXpresso-SDK-SBOM.spdx.json)')
        
        parser.add_argument(
            '-v', '--verbose',
            action='store_true',
            help='Enable verbose (DEBUG) logging')
        
        parser.add_argument(
            '--sbom-pattern',
            default='*SBOM*.json',
            help='Filename pattern to search for SBOM files in each project (default: *SBOM*.json). '
                 'Supports wildcards like *SBOM*.json, SBOM.spdx.json, etc.')

        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be collected without creating output file')

        parser.add_argument(
            '--allow-policy-violations',
            action='store_true',
            help='Write the merged SBOM and exit 0 even if it fails NXP Policy / SPDX 2.3 '
                 'validation (default: fail the command on any validation error)')

        return parser

    def do_run(self, args, unknown_args):
        # Configure logging
        if args.verbose:
            log.VERBOSE = True

        try:
            # Get the west workspace and manifest
            manifest = Manifest.from_file()
            workspace_root = Path(manifest.topdir)
            
            log.inf(f"Starting SBOM merge for workspace: {workspace_root}")
            log.inf(f"Looking for SBOM files matching pattern: {args.sbom_pattern}")

            # Collect disabled groups once from manifest
            disabled_groups = self._get_disabled_groups(manifest)
            if disabled_groups:
                log.inf(f"Manifest has disabled groups: {disabled_groups}")
            else:
                log.inf("No disabled groups found in manifest")

            # Filter projects based on provided project names
            if args.projects:
                log.inf(f"Filtering projects: {args.projects}")
                projects_to_process = [p for p in manifest.projects if p.name in args.projects]
                
                # Check for projects not found
                found_project_names = {p.name for p in projects_to_process}
                not_found = set(args.projects) - found_project_names
                if not_found:
                    log.wrn(f"Projects not found in manifest: {sorted(not_found)}")
                
                if not projects_to_process:
                    log.err(f"None of the specified projects found in manifest: {args.projects}")
                    return 1
                
                log.inf(f"Processing {len(projects_to_process)} specified projects")
            else:
                projects_to_process = manifest.projects
                log.inf(f"No projects specified, processing all {len(projects_to_process)} projects")

            # Collect SBOM files from filtered projects
            sbom_files = []
            projects_without_sbom = []
            disabled_projects = []

            # Process each project
            for project in projects_to_process:
                project_path = workspace_root / project.path
                
                log.dbg(f"Checking project: {project.name} at {project_path}")
                
                # Find SBOM files matching the pattern
                found_sbom_files = self._find_sbom_files(project_path, args.sbom_pattern)
                
                if found_sbom_files:
                    for sbom_path in found_sbom_files:
                        log.inf(f"Found SBOM file in project '{project.name}': {sbom_path}")
                        sbom_files.append({
                            'project_name': project.url.rstrip('/').split('/')[-1] if project.url else project.name,
                            'project_path': project.path,
                            'sbom_path': sbom_path
                        })
                else:
                    if self._is_project_disabled(project, disabled_groups):
                        log.dbg(f"Project '{project.name}' is disabled by group-filter, skipping SBOM requirement")
                        disabled_projects.append({
                            'project_name': project.name,
                            'project_path': project.path,
                            'groups': getattr(project, 'groups', [])
                        })
                    else:
                        log.wrn(f"No SBOM file matching pattern '{args.sbom_pattern}' found in enabled project '{project.name}' at {project_path}")
                        projects_without_sbom.append({
                            'project_name': project.name,
                            'project_path': project.path,
                            'groups': getattr(project, 'groups', [])
                        })
            
            # Log summary of collection
            log.inf(f"Found {len(sbom_files)} SBOM files")
            log.inf(f"Projects without SBOM files: {len(projects_without_sbom)}")
            log.inf(f"Disabled projects (by group-filter): {len(disabled_projects)}")
            
            if projects_without_sbom:
                log.wrn("Enabled projects missing SBOM files:")
                for project in projects_without_sbom:
                    log.wrn(f"  - {project['project_name']} ({project['project_path']})")
            
            if not sbom_files:
                log.err("No SBOM files found in any project. Nothing to merge.")
                return 1

            # Parse and validate each project's SBOM, then merge by reference:
            # each project's own DESCRIBES relationship names its root package(s),
            # which are expanded to their transitive closure and deduplicated
            # across projects rather than concatenated wholesale.
            parsed_sbom_files = self._load_sbom_files(sbom_files)
            if not parsed_sbom_files:
                log.err("No valid SBOM files could be parsed. Nothing to merge.")
                return 1

            sdk_version = self._read_mcux_version(workspace_root)
            log.inf(f"Using SDK version for merged SBOM: {sdk_version}")

            merger = SbomMerger(output_name='MCUXpresso-SDK-SBOM', root_package_version=sdk_version,
                                 warn=log.wrn, debug=log.dbg)
            merged_sbom = merger.merge(parsed_sbom_files)

            policy_errors = self._validate_policy(merged_sbom, workspace_root)

            # Write merged SBOM to output file
            output_path = Path(args.output)
            if not output_path.is_absolute():
                output_path = workspace_root / output_path

            if args.dry_run:
                log.inf("=== DRY RUN - No file will be created ===")
                log.inf(f"Would create SBOM with {len(merged_sbom.get('packages', []))} packages")
                log.inf(f"Would write to: {output_path}")
            else:
                try:
                    log.inf(f"Writing merged SBOM to: {output_path}")

                    # Ensure output directory exists
                    output_path.parent.mkdir(parents=True, exist_ok=True)

                    with open(output_path, 'w', encoding='utf-8') as f:
                        json.dump(merged_sbom, f, indent=2, sort_keys=True)

                    # Verify file was written successfully
                    if output_path.exists() and output_path.stat().st_size > 0:
                        log.inf("SBOM collection completed successfully")
                    else:
                        log.err("Failed to write SBOM file properly")
                        return 1
        
                except PermissionError:
                    log.err(f"Permission denied writing to {output_path}")
                    return 1
                except OSError as e:
                    log.err(f"OS error writing SBOM file: {e}")
                    return 1
            
            log.inf("=== SBOM Collection Summary ===")
            log.inf(f"Projects processed: {len(sbom_files)}")
            log.inf(f"Projects without SBOM: {len(projects_without_sbom)}")
            log.inf(f"Total packages collected: {len(merged_sbom.get('packages', []))}")
            log.inf(f"Total relationships: {len(merged_sbom.get('relationships', []))}")
            log.inf(f"Output file: {output_path}")

            if policy_errors and not args.dry_run and not args.allow_policy_violations:
                log.err(f"Merged SBOM failed validation "
                        f"({len(policy_errors)} error(s)); see validator warnings above. "
                        f"Pass --allow-policy-violations to write it anyway.")
                return 1

            return 0

        except Exception as e:
            log.err(f"Error during SBOM merge: {e}")
            log.dbg("Exception details:", exc_info=True)
            return 1

    def _validate_policy(self, merged_sbom: Dict, workspace_root: Path) -> List:
        """Validate the merged SBOM and log every issue found.

        Always runs the generic, company-policy-free SPDX23Validator. If the
        internal NXP-policy overlay (bifrost/scripts/nxp_sbom_policy_validator.py)
        is present in this workspace, also runs it and merges its findings;
        otherwise only generic SPDX 2.3 conformance is checked.

        Returns the combined list of ERROR-severity issues (empty == passes
        whatever validation actually ran). Warnings/info are logged but never
        fail the command.
        """
        issues = SPDX23Validator().validate(merged_sbom)
        errors = [i for i in issues if i.severity.value == 'ERROR']
        warnings = [i for i in issues if i.severity.value == 'WARNING']

        log.inf(f"SPDX 2.3 validation: {len(errors)} error(s), {len(warnings)} warning(s)")
        for issue in errors:
            log.err(f"SPDX23Validator: {issue}")
        for issue in warnings:
            log.wrn(f"SPDX23Validator: {issue}")

        overlay = _load_policy_overlay(workspace_root)
        if overlay is None:
            log.inf(f"Internal policy overlay not found at {_POLICY_OVERLAY_RELPATH} "
                     "(bifrost not checked out); skipping company-policy checks.")
            return errors

        overlay_issues = overlay.validate(merged_sbom)
        overlay_errors = [i for i in overlay_issues if i.get('severity') == 'ERROR']
        overlay_warnings = [i for i in overlay_issues if i.get('severity') == 'WARNING']

        log.inf(f"NXP policy validation: {len(overlay_errors)} error(s), {len(overlay_warnings)} warning(s)")
        for issue in overlay_errors:
            log.err(f"NXPSbomPolicyValidator: {_format_overlay_issue(issue)}")
        for issue in overlay_warnings:
            log.wrn(f"NXPSbomPolicyValidator: {_format_overlay_issue(issue)}")

        return errors + overlay_errors

    def _read_mcux_version(self, workspace_root: Path) -> str:
        """
        Read the SDK version (YY.MM.NN[-suffix]) from mcuxsdk/MCUX_VERSION, the same
        file used by mcux_version.cmake and sdk_version_update.py, so the merged
        SBOM's root package reports the actual SDK release rather than NOASSERTION.

        Returns:
            Version string, or 'NOASSERTION' if the file is missing or unparsable.
        """
        version_file = workspace_root / 'mcuxsdk' / 'MCUX_VERSION'
        if not version_file.is_file():
            log.wrn(f"MCUX_VERSION file not found at {version_file}; using NOASSERTION for SBOM version")
            return 'NOASSERTION'

        try:
            text = version_file.read_text(encoding='utf-8')
        except OSError as e:
            log.wrn(f"Unable to read {version_file}: {e}; using NOASSERTION for SBOM version")
            return 'NOASSERTION'

        year = re.search(r'CURRENT_YEAR\s*=\s*(\d+)', text)
        major = re.search(r'VERSION_MAJOR\s*=\s*(\d+)', text)
        minor = re.search(r'VERSION_MINOR\s*=\s*([\w.\-]+)', text)
        if not (year and major and minor):
            log.wrn(f"Unable to parse {version_file}; using NOASSERTION for SBOM version")
            return 'NOASSERTION'

        return f"{year.group(1)}.{major.group(1)}.{minor.group(1)}"

    def _find_sbom_files(self, project_path: Path, pattern: str) -> List[Path]:
        """
        Find SBOM files matching the pattern in the project directory.
        
        Args:
            project_path: Path to the project directory
            pattern: Filename pattern to match (supports wildcards)
            
        Returns:
            List of Path objects for matching SBOM files
        """
        sbom_files = []
        
        if not project_path.exists() or not project_path.is_dir():
            log.dbg(f"Project path does not exist or is not a directory: {project_path}")
            return sbom_files
        
        try:
            # Search for files matching the pattern in the project root
            for file_path in project_path.iterdir():
                if file_path.is_file() and fnmatch.fnmatch(file_path.name, pattern):
                    log.dbg(f"Found matching SBOM file: {file_path}")
                    sbom_files.append(file_path)
            
            # If no files found in root, also check common subdirectories
            if not sbom_files:
                common_sbom_dirs = ['sbom', 'SBOM', 'docs', 'documentation']
                for subdir_name in common_sbom_dirs:
                    subdir_path = project_path / subdir_name
                    if subdir_path.exists() and subdir_path.is_dir():
                        for file_path in subdir_path.iterdir():
                            if file_path.is_file() and fnmatch.fnmatch(file_path.name, pattern):
                                log.dbg(f"Found matching SBOM file in {subdir_name}: {file_path}")
                                sbom_files.append(file_path)
        
        except (OSError, PermissionError) as e:
            log.wrn(f"Error searching for SBOM files in {project_path}: {e}")
        
        return sbom_files

    def _validate_sbom_structure(self, sbom_data: Dict, project_name: str) -> bool:
        """Validate basic SBOM structure."""
        required_fields = ['spdxVersion', 'dataLicense', 'SPDXID', 'name']
        
        for field in required_fields:
            if field not in sbom_data:
                log.wrn(f"Missing required field '{field}' in {project_name} SBOM")
                return False
        
        if sbom_data.get('SPDXID') != 'SPDXRef-DOCUMENT':
            log.wrn(f"Invalid document SPDXID in {project_name} SBOM")
            return False
            
        return True

    def _load_sbom_files(self, sbom_files: List[Dict]) -> List[Dict]:
        """Parse and validate each discovered SBOM file.

        Returns [{'project_name': str, 'sbom_data': dict}, ...] for files that
        parse as JSON and pass minimal SPDX structure validation; unparseable
        or invalid files are logged and skipped.
        """
        loaded = []
        for sbom_info in sbom_files:
            sbom_path = sbom_info['sbom_path']
            project_name = sbom_info['project_name']

            try:
                with open(sbom_path, 'r', encoding='utf-8') as f:
                    sbom_data = json.load(f)
            except json.JSONDecodeError as e:
                log.err(f"Invalid JSON in SBOM file {sbom_path}: {e}")
                continue
            except OSError as e:
                log.err(f"Error reading SBOM file {sbom_path}: {e}")
                continue

            if not self._validate_sbom_structure(sbom_data, project_name):
                log.err(f"Invalid SBOM structure in {project_name}, skipping...")
                continue

            spdx_version = sbom_data.get('spdxVersion', '')
            if not spdx_version.startswith('SPDX-2.'):
                log.wrn(f"Unsupported SPDX version in {project_name}: {spdx_version}")

            loaded.append({'project_name': project_name, 'sbom_data': sbom_data})

        return loaded

    def _get_disabled_groups(self, manifest: Manifest) -> Set[str]:
        """
        Extract disabled groups from manifest group-filter.
        Disabled groups are prefixed with '-' in West manifest group-filter.
        
        Args:
            manifest: The west manifest object
            
        Returns:
            Set[str]: Set of disabled group names (without '-' prefix)
        """
        disabled_groups = set()
        
        try:
            group_filter = getattr(manifest, 'group_filter', None)
            
            if group_filter is None:
                return disabled_groups
            
            if isinstance(group_filter, (list, tuple)):
                for group in group_filter:
                    if isinstance(group, str) and group.startswith('-'):
                        disabled_group_name = group[1:]  # Remove '-' prefix
                        disabled_groups.add(disabled_group_name)
            elif isinstance(group_filter, str) and group_filter.startswith('-'):
                disabled_group_name = group_filter[1:]  # Remove '-' prefix
                disabled_groups.add(disabled_group_name)
                
            log.dbg(f"Extracted disabled groups: {disabled_groups}")
            
        except Exception as e:
            log.wrn(f"Error extracting disabled groups from manifest: {e}")
        
        return disabled_groups

    def _is_project_disabled(self, project, disabled_groups: Set[str]) -> bool:
        """
        Check if a project is disabled based on its groups and the global disabled groups.
        A project is considered disabled if ALL of its groups are in the disabled groups set.
        
        Args:
            project: The manifest project to check
            disabled_groups: Set of globally disabled group names
            
        Returns:
            bool: True if project is disabled (all groups are disabled), False otherwise
        """
        try:
            # Get the project's groups
            project_groups = getattr(project, 'groups', [])
            
            if not project_groups:
                log.dbg(f"Project '{project.name}' has no groups, considering as enabled")
                return False
            
            if not disabled_groups:
                log.dbg(f"No disabled groups defined, project '{project.name}' is enabled")
                return False
            
            # Check if ALL project groups are in the disabled groups set
            project_group_set = set(project_groups)
            
            # A project is disabled only if ALL its groups are disabled
            if project_group_set.issubset(disabled_groups):
                log.dbg(f"Project '{project.name}' is disabled - all groups {project_groups} are in disabled set")
                return True
            else:
                enabled_groups = project_group_set - disabled_groups
                log.dbg(f"Project '{project.name}' is enabled - has enabled groups: {enabled_groups}")
                return False
                
        except Exception as e:
            log.wrn(f"Error checking if project '{project.name}' is disabled: {e}")
            return False
