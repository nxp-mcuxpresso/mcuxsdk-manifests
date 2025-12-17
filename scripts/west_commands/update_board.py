# -*- coding: utf-8 -*-
# Copyright 2025 NXP
# SPDX-License-Identifier: BSD-3-Clause

import os
import subprocess
import time
import yaml
from datetime import datetime
from typing import List, Tuple
import argparse

from west.commands import WestCommand
from west.manifest import Manifest
from west import log

# Import utilities from the utilities package
from utilities import DeviceBoardMapper, ConfigLoader, BoardConfig, PackageCreator, PackageOptions


class UpdateBoardCommand(WestCommand):
    """Updates repositories for a specific set (board/device/custom) and optionally creates zip packages."""

    def __init__(self):
        super().__init__(
            'update_board',
            'update repositories for a board, device, or custom configuration',
            '''
Update repositories based on board, device, or custom configuration.

Without -o/--output: Simply updates the configured repositories for the specified set.

With -o/--output: Updates repositories and creates a filtered zip package with optional
documentation generation and git history inclusion. By default, repositories are updated
first, but this can be skipped with --no-update for CI/CD scenarios or when repos are
already current.

Examples:
  # Update repositories only
  west update_board --set board mcxw23evk

  # Update and create package with all optional items
  west update_board --set board mcxw23evk -o package.zip --include-optional

  # Skip update, create package with docs (for CI/CD parallel execution)
  west update_board --set board mcxw23evk -o package.zip --no-update --gen-doc

  # List all repositories with descriptions in YAML format
  west update_board --set board mcxw23evk --list-repo

  # Save repository information to YAML file
  west update_board --set board mcxw23evk --list-repo -o repos.yml

  # Collect SBOM for board repositories and save to JSON file
  west update_board --set board mcxw23evk --list-sbom -o sbom.json

  # Collect SBOM for device repositories
  west update_board --set device MCXW235 --list-sbom -o device-sbom.json

Note: For custom SBOM pattern matching, use 'west sbom_collect' directly with
      --sbom-pattern option.
''')

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name, help=self.help, description=self.description, formatter_class=argparse.RawDescriptionHelpFormatter)
        
        # Core set argument with value
        parser.add_argument('--set', nargs=2, metavar=('TYPE', 'VALUE'), required=True,
                          help='Configuration set: "board BOARD_NAME", "device DEVICE_NAME", or "custom CONFIG_FILE"')
        
        # Output (makes this a packaging operation)
        parser.add_argument('-o', '--output', 
                          help='Output file path. For packaging: creates a filtered zip package after updating repositories. '
                               'For --list-repo: if the file has .yml/.yaml extension, writes repository information to that file; '
                               'For --list-sbom: writes merged SBOM to the specified JSON file.')
        
        # Repository listing
        parser.add_argument('--list-repo', action='store_true',
                          help='List all repositories with display names and descriptions in YAML format for the specified set.')
        
        # SBOM collection
        parser.add_argument('--list-sbom', action='store_true',
                          help='Collect SBOM files from repositories for the specified set and output merged SBOM. '
                               'Requires -o/--output to specify the output JSON file path. '
                               'Uses default pattern (*SBOM*.json). For custom patterns, use "west sbom_collect" directly.')
        
        # Package-only options (only valid with -o/--output)
        package_group = parser.add_argument_group('Package Options', 
                                                'These options are only available when creating a package with -o/--output. '
                                                'Note: --include-optional can also be used with --list-sbom.')
        package_group.add_argument('--no-update', '-n', action='store_true',
                                 help='Skip repository update step. Assumes repositories are already current. '
                                      'Useful for parallel CI/CD execution or when repositories are already up-to-date. Only valid with -o/--output.')
        package_group.add_argument('--include-optional', nargs='*', metavar='REPO', 
                                 help='Include specific optional repos/examples, or use without arguments to include all optional items. '
                                      'Example: --include-optional repo1 repo2, or just --include-optional for all. '
                                      'Can be used with --list-sbom to include optional repositories in SBOM collection.')
        package_group.add_argument('--include-git', action='store_true', 
                                 help='Include .git history in package. Only valid with -o/--output.')
        package_group.add_argument('--gen-doc', action='store_true', 
                                 help='Generate and include PDF documentation in package. Requires mcu-sdk-doc repository. Only valid with -o/--output.')
        
        return parser

    def do_run(self, args, unknown_args=None):
        """Main execution method."""
        start_time = time.time()
        self._log_with_timestamp("Starting update_board command", 'inf')
    
        try:
            # Validate arguments
            self._validate_arguments(args)
            
            # Parse and validate set arguments
            set_type, set_value = self._parse_set_arguments(args)
        
            # Initialize workspace
            workspace_root, manifest_dir, manifest = self._init_workspace()
        
            # Initialize utilities with logger
            config_loader = ConfigLoader(workspace_root, manifest_dir, self._log_with_timestamp)
        
            # Load configuration
            config = self._load_configuration(config_loader, set_type, set_value)
        
            # Handle list-repo operation
            if args.list_repo:
                self._handle_list_repositories(config, manifest, args.output)
                return
            
            # Process repositories and examples based on optional inclusion
            # This needs to happen before list-sbom to respect --include-optional
            final_repos, final_examples = self._process_optional_inclusion(config, args)
            
            # Handle list-sbom operation
            if args.list_sbom:
                self._handle_list_sbom(final_repos, args.output, workspace_root)
                return
        
            # Get projects to update
            projects = self._get_projects_to_update(manifest, final_repos)
        
            # Update repositories (conditional)
            if not args.no_update:
                self._update_projects(workspace_root, projects)
            else:
                self._log_with_timestamp("Skipping repository update as requested", 'inf')
                self._validate_projects_exist(workspace_root, projects)
        
            # Create package if requested
            if args.output:
                self._create_package(config_loader, config, args, projects, final_repos, final_examples)
            else:
                self._log_with_timestamp("Repository update completed. No package creation requested.", 'inf')
            
            elapsed_time = time.time() - start_time
            self._log_with_timestamp(f"Command completed successfully in {elapsed_time:.2f} seconds", 'inf')
        
        except SystemExit:
            # Don't log SystemExit - it was already handled
            raise
        except Exception as e:
            elapsed_time = time.time() - start_time
            self._log_with_timestamp(f"Command failed after {elapsed_time:.2f} seconds: {e}", 'err')
            self.die(f"Command failed: {e}")

    def _validate_arguments(self, args):
        """Validate argument combinations."""
        # Check for mutually exclusive operations
        if args.list_repo and args.list_sbom:
            self._log_with_timestamp("Cannot use --list-repo and --list-sbom together", 'err')
            raise Exception("Options --list-repo and --list-sbom are mutually exclusive")
        
        # Check if --list-sbom requires -o/--output
        if args.list_sbom and not args.output:
            self._log_with_timestamp("--list-sbom requires -o/--output to specify the output JSON file", 'err')
            raise Exception("--list-sbom requires -o/--output to specify the output JSON file path")
        
        # Check if --list-sbom output has .json extension
        if args.list_sbom and args.output:
            if not args.output.lower().endswith('.json'):
                self._log_with_timestamp("--list-sbom output file must have .json extension", 'wrn')
        
        package_only_options = []
        if args.no_update:
            package_only_options.append('--no-update')
        if args.include_git:
            package_only_options.append('--include-git')
        if args.gen_doc:
            package_only_options.append('--gen-doc')
        
        # --include-optional can be used with --list-sbom, so don't add it to package_only_options
        # Package-only options should not be used with --list-repo or --list-sbom
        if package_only_options and (args.list_repo or args.list_sbom):
            options_str = ', '.join(package_only_options)
            operation = '--list-repo' if args.list_repo else '--list-sbom'
            self._log_with_timestamp(f"Invalid argument combination: {options_str} cannot be used with {operation}", 'err')
            raise Exception(f"Options {options_str} are not applicable with {operation}")
        
        if package_only_options and not args.output:
            options_str = ', '.join(package_only_options)
            self._log_with_timestamp(f"Invalid argument combination: {options_str} require -o/--output", 'err')
            raise Exception(f"Options {options_str} are only available when creating a package with -o/--output")

    def _log_with_timestamp(self, message: str, level: str = 'inf'):
        """Log message with timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        
        if level == 'inf':
            log.inf(formatted_message)
        elif level == 'wrn':
            log.wrn(formatted_message)
        elif level == 'dbg':
            log.dbg(formatted_message)
        elif level == 'err':
            log.err(formatted_message)

    def _parse_set_arguments(self, args) -> Tuple[str, str]:
        """Parse and validate set arguments."""
        if len(args.set) != 2:
            self._log_with_timestamp("--set requires exactly 2 arguments: TYPE and VALUE", 'err')
            raise Exception("--set requires exactly 2 arguments: TYPE and VALUE")
        
        set_type, set_value = args.set
        
        if set_type not in ['board', 'device', 'custom']:
            self._log_with_timestamp(f"Invalid set type '{set_type}'. Must be 'board', 'device', or 'custom'", 'err')
            raise Exception(f"Invalid set type '{set_type}'. Must be 'board', 'device', or 'custom'")
        
        if not set_value.strip():
            self._log_with_timestamp(f"Value cannot be empty for set type '{set_type}'", 'err')
            raise Exception(f"Value cannot be empty for set type '{set_type}'")
        
        self._log_with_timestamp(f"Parsed arguments: type={set_type}, value={set_value}", 'dbg')
        return set_type, set_value.strip()

    def _init_workspace(self) -> Tuple[str, str, Manifest]:
        """Initialize workspace."""
        try:
            manifest = Manifest.from_topdir()
            workspace_root = self.topdir
            manifest_dir = os.path.dirname(self.manifest.path)
            
            self._log_with_timestamp(f"Workspace root: {workspace_root}", 'dbg')
            self._log_with_timestamp(f"Manifest directory: {manifest_dir}", 'dbg')
            
            return workspace_root, manifest_dir, manifest
        except Exception as e:
            self._log_with_timestamp(f"Error initializing workspace: {e}", 'err')
            raise Exception(f"Error initializing workspace: {e}")

    def _load_configuration(self, config_loader: ConfigLoader, set_type: str, set_value: str) -> BoardConfig:
        """Load configuration based on set type."""
        self._log_with_timestamp(f"Loading {set_type} configuration: {set_value}", 'dbg')
        
        try:
            if set_type == 'board':
                config = config_loader.load_board_config(set_value)
                # Log board dependencies if any
                if config.board_dependencies:
                    self._log_with_timestamp(f"Board '{set_value}' has dependencies: {', '.join(config.board_dependencies)}", 'inf')
            elif set_type == 'device':
                config = config_loader.load_device_config(set_value)
                if hasattr(config, 'source_boards') and config.source_boards:
                    self._log_with_timestamp(f"Matched boards for device '{set_value}': {', '.join(config.source_boards)}", 'inf')
                # Log merged dependencies if any
                if config.board_dependencies:
                    self._log_with_timestamp(f"Device '{set_value}' has merged dependencies: {', '.join(config.board_dependencies)}", 'inf')
            elif set_type == 'custom':
                config = config_loader.load_custom_config(set_value)
                # Log custom config dependencies if any
                if config.board_dependencies:
                    self._log_with_timestamp(f"Custom config '{set_value}' has dependencies: {', '.join(config.board_dependencies)}", 'inf')
            
            self._log_with_timestamp(f"Loaded {set_type} configuration: {config.name}", 'inf')

            return config
            
        except Exception as e:
            # Configuration not found errors should be 'err' level
            if any(keyword in str(e).lower() for keyword in ['not found', 'missing', 'does not exist']):
                self._log_with_timestamp(f"{e}", 'err')
            else:
                # Other errors (parsing, etc.) can be 'wrn' level
                self._log_with_timestamp(f"{e}", 'wrn')
            raise SystemExit(1)

    def _resolve_output_path(self, output_file: str, workspace_root: str) -> str:
        """
        Resolve output file path to absolute path.
        
        If the path is relative, it's resolved relative to the current working directory,
        not the workspace root. This ensures consistent behavior with user expectations.
        
        Args:
            output_file: Output file path (can be relative or absolute)
            workspace_root: Workspace root directory
            
        Returns:
            Absolute path to the output file
        """
        if not output_file:
            return output_file
        
        # Convert to absolute path if relative
        if not os.path.isabs(output_file):
            # Resolve relative to current working directory
            abs_path = os.path.abspath(output_file)
            self._log_with_timestamp(f"Resolved relative path '{output_file}' to '{abs_path}'", 'dbg')
            return abs_path
        
        self._log_with_timestamp(f"Using absolute path: {output_file}", 'dbg')
        return output_file

    def _handle_list_repositories(self, config: BoardConfig, manifest: Manifest, output_file: str = None):
        """Handle repository listing operation."""
        self._log_with_timestamp("Generating repository information in YAML format", 'inf')
        
        # Collect all repositories (core + optional)
        all_repos = set(config.repo_list + config.optional_repos)
        self._log_with_timestamp(f"Processing {len(all_repos)} repositories", 'dbg')
        
        # Get project information from manifest
        project_map = {project.name: project for project in manifest.projects}
        
        # Create repository information structure
        repo_info = {}
        
        for repo_name in sorted(all_repos):
            if repo_name in project_map:
                project = project_map[repo_name]
                display_name = repo_name
                description = 'No description available'
                
                # Extract display_name and description from userdata
                if hasattr(project, 'userdata') and project.userdata:
                    display_name = project.userdata.get('display_name', repo_name)
                    description = project.userdata.get('description', 'No description available')
                
                # Determine if repository is optional
                is_optional = repo_name in config.optional_repos
                
                repo_entry = {
                    'display_name': display_name,
                    'description': description,
                }
                if is_optional:
                    repo_entry['optional'] = True
                
                repo_info[repo_name] = repo_entry
            else:
                self._log_with_timestamp(f"Repository '{repo_name}' not found in manifest", 'wrn')
                is_optional = repo_name in config.optional_repos
                repo_entry = {
                    'display_name': repo_name,
                    'description': f"Repository '{repo_name}' not found in manifest",
                }
                if is_optional:
                    repo_entry['optional'] = True
                repo_info[repo_name] = repo_entry
       
        # Generate YAML output
        yaml_output = yaml.dump(repo_info, default_flow_style=False, sort_keys=False, indent=2)

        # Output to console
        print(yaml_output)
        
        # Output to file if requested
        if output_file and output_file.lower().endswith(('.yml', '.yaml')):
            try:
                # Resolve output path to absolute
                abs_output_file = self._resolve_output_path(output_file, self.topdir)
                
                # Only create directory if there is a directory component
                output_dir = os.path.dirname(abs_output_file)
                if output_dir:  # Only create if directory path is not empty
                    os.makedirs(output_dir, exist_ok=True)
                
                with open(abs_output_file, 'w', encoding='utf-8') as f:
                    f.write(yaml_output)
                self._log_with_timestamp(f"Repository information written to: {abs_output_file}", 'inf')
            except (IOError, OSError) as e:
                self._log_with_timestamp(f"Failed to write repository information to file: {e}", 'err')
                raise Exception(f"Failed to write repository information to file: {e}")
        
        self._log_with_timestamp(f"Listed {len(all_repos)} repositories ({len(config.repo_list)} core, {len(config.optional_repos)} optional)", 'inf')

    def _handle_list_sbom(self, repo_list: List[str], output_file: str, workspace_root: str):
        """Handle SBOM collection operation by calling west sbom_collect."""
        self._log_with_timestamp("Starting SBOM collection for configured repositories", 'inf')
        
        # Use the provided repo_list which already includes optional repos if --include-optional was specified
        self._log_with_timestamp(f"Collecting SBOM from {len(repo_list)} repositories", 'inf')
        
        # Resolve output path to absolute path
        abs_output_file = self._resolve_output_path(output_file, workspace_root)
        self._log_with_timestamp(f"SBOM will be written to: {abs_output_file}", 'dbg')
        
        # Build west sbom_collect command
        cmd = ['west', 'sbom_collect'] + repo_list
        
        # Add output file (use absolute path)
        cmd.extend(['-o', abs_output_file])
        
        # Add verbose flag if in debug mode
        if log.VERBOSE:
            cmd.append('-v')
        
        try:
            self._log_with_timestamp(f"Running command: {' '.join(cmd)}", 'dbg')
            start_time = time.time()
            
            # Run the sbom_collect command from workspace root
            result = subprocess.run(
                cmd,
                cwd=workspace_root,
                check=True,
                capture_output=True,
                text=True
            )
            
            elapsed_time = time.time() - start_time
            self._log_with_timestamp(f"SBOM collection completed in {elapsed_time:.2f} seconds", 'inf')
            
            # Log output
            if result.stdout:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        self._log_with_timestamp(f"SBOM: {line}", 'dbg')
            
            # Verify output file was created
            if os.path.exists(abs_output_file):
                file_size = os.path.getsize(abs_output_file) / 1024  # Size in KB
                self._log_with_timestamp(f"SBOM file created: {abs_output_file} ({file_size:.1f} KB)", 'inf')
            else:
                self._log_with_timestamp(f"SBOM file was not created: {abs_output_file}", 'wrn')
                
        except subprocess.CalledProcessError as e:
            self._log_with_timestamp(f"SBOM collection failed with exit code {e.returncode}", 'err')
            if e.stderr:
                for line in e.stderr.strip().split('\n'):
                    if line.strip():
                        self._log_with_timestamp(f"SBOM Error: {line}", 'err')
            raise Exception(f"SBOM collection failed: {e.stderr}")
        except Exception as e:
            self._log_with_timestamp(f"Error during SBOM collection: {e}", 'err')
            raise

    def _process_optional_inclusion(self, config: BoardConfig, args) -> Tuple[List[str], List[str]]:
        """Process optional repository and example inclusion."""
        final_repos = list(config.repo_list)
        final_examples = list(config.example_list)
        
        included_optional_repos = set()
        included_optional_examples = set()
        
        # Process optional inclusion
        if args.include_optional is not None:
            if len(args.include_optional) == 0 or 'all' in args.include_optional:
                # Include all optional items
                included_optional_repos.update(config.optional_repos)
                included_optional_examples.update(config.optional_examples)
                self._log_with_timestamp("Including all optional repositories and examples", 'inf')
            else:
                # Include specific optional items
                for name in args.include_optional:
                    if name in config.optional_repos:
                        included_optional_repos.add(name)
                        self._log_with_timestamp(f"Including optional repository: {name}", 'dbg')
                    elif name in config.optional_examples:
                        included_optional_examples.add(name)
                        self._log_with_timestamp(f"Including optional example: {name}", 'dbg')
                    else:
                        self._log_with_timestamp(f"Unknown optional item: {name}", 'wrn')
        
        # Add included optional items to final lists
        final_repos.extend(sorted(included_optional_repos))
        final_examples.extend(sorted(included_optional_examples))
        
        # Log summary
        self._log_with_timestamp(f"Final repository count: {len(final_repos)} (core: {len(config.repo_list)}, optional: {len(included_optional_repos)})", 'inf')
        self._log_with_timestamp(f"Final example count: {len(final_examples)} (core: {len(config.example_list)}, optional: {len(included_optional_examples)})", 'inf')
        
        if included_optional_repos:
            self._log_with_timestamp(f"Including optional repos: {sorted(included_optional_repos)}", 'dbg')
        if included_optional_examples:
            self._log_with_timestamp(f"Including optional examples: {sorted(included_optional_examples)}", 'dbg')
        
        return final_repos, final_examples

    def _get_projects_to_update(self, manifest: Manifest, repo_list: List[str]) -> List:
        """Get projects to update from manifest."""
        projects = [p for p in manifest.projects if p.name in repo_list]
        
        if projects:
            project_names = [p.name for p in projects]
            self._log_with_timestamp(f"Found {len(projects)} projects to update: {project_names}", 'inf')
            self._log_with_timestamp(f"Project details: {[(p.name, p.path) for p in projects]}", 'dbg')
        else:
            self._log_with_timestamp("No projects found to update", 'wrn')
            
        return projects

    def _update_projects(self, workspace_root: str, projects: List):
        """Update specified projects."""
        if not projects:
            self._log_with_timestamp("No projects to update", 'wrn')
            return
        
        project_names = [p.name for p in projects]
        self._log_with_timestamp(f"Updating {len(projects)} repositories: {project_names}", 'inf')
        
        try:
            cmd = ['west', 'update', '-n', '-o=--depth=1'] + project_names
            start_time = time.time()
            
            self._log_with_timestamp(f"Running command: {' '.join(cmd)}", 'dbg')
            result = subprocess.run(cmd, cwd=workspace_root, check=True, capture_output=True, text=True)
            
            elapsed_time = time.time() - start_time
            self._log_with_timestamp(f"Repository update completed in {elapsed_time:.2f} seconds", 'inf')
            
            if result.stdout:
                self._log_with_timestamp(f"Update output: {result.stdout.strip()}", 'dbg')
            
        except subprocess.CalledProcessError as e:
            self._log_with_timestamp(f"Update command failed with exit code {e.returncode}", 'err')
            if e.stderr:
                self._log_with_timestamp(f"Error output: {e.stderr.strip()}", 'err')
            raise Exception(f"Update failed: {e.stderr}")

    def _validate_projects_exist(self, workspace_root: str, projects: List):
        """Validate that required projects exist in the workspace when skipping update."""
        if not projects:
            self._log_with_timestamp("No projects to validate", 'dbg')
            return
        
        missing_projects = []
        existing_projects = []
        
        for project in projects:
            if project.name == "core":
                # Special handling for core project - check key subdirectories
                core_subdirs = ["drivers", "arch", "cmake", "share", "scripts", "devices"]
                missing_core_subdirs = []
                
                for subdir in core_subdirs:
                    subdir_path = os.path.join(workspace_root, "mcuxsdk", subdir)
                    if not os.path.exists(subdir_path):
                        missing_core_subdirs.append(f"mcuxsdk/{subdir}")
                
                if missing_core_subdirs:
                    missing_projects.extend(missing_core_subdirs)
                else:
                    existing_projects.append(f"{project.name} (mcuxsdk)")
            else:
                project_path = os.path.join(workspace_root, project.path)
                if not os.path.exists(project_path):
                    missing_projects.append(f"{project.name} ({project.path})")
                else:
                    existing_projects.append(f"{project.name} ({project.path})")
        
        # Log validation results
        if existing_projects:
            self._log_with_timestamp(f"Validated {len(existing_projects)} existing projects", 'inf')
            for project in existing_projects:
                self._log_with_timestamp(f"  ✓ {project}", 'dbg')
        
        if missing_projects:
            self._log_with_timestamp(f"Missing {len(missing_projects)} required projects:", 'err')
            for project in missing_projects:
                self._log_with_timestamp(f"  ✗ {project}", 'err')
            
            raise Exception(f"Missing required projects: {', '.join(missing_projects)}. "
                        f"Run without --no-update to checkout missing repositories.")
        
        self._log_with_timestamp(f"All {len(projects)} required projects are present in workspace", 'inf')

    def _create_package(self, config_loader: ConfigLoader, config: BoardConfig, args, 
                       projects: List, final_repos: List[str], final_examples: List[str]):
        """Create package using PackageCreator."""
        # Resolve output path to absolute
        abs_output = self._resolve_output_path(args.output, self.topdir)
        self._log_with_timestamp(f"Creating package: {abs_output}", 'inf')
        
        # Get board list for filtering
        board_list = config_loader.get_filtering_boards(config)
        self._log_with_timestamp(f"Using board filter (including dependencies): {board_list}", 'dbg')
        
        # Create package options
        options = PackageOptions(
            include_git=args.include_git,
            generate_docs=args.gen_doc,
            board_filter=board_list
        )
        
        self._log_with_timestamp(f"Package options: git={options.include_git}, docs={options.generate_docs}", 'dbg')
        
        # Create package with logger
        package_creator = PackageCreator(self.topdir, self._log_with_timestamp)
        
        start_time = time.time()
        package_creator.create_package(abs_output, projects, final_repos, final_examples, options)
        elapsed_time = time.time() - start_time
        
        # Get final package size
        if os.path.exists(abs_output):
            size_mb = os.path.getsize(abs_output) / (1024 * 1024)
            self._log_with_timestamp(f"Package created: {abs_output} ({size_mb:.1f} MB) in {elapsed_time:.2f} seconds", 'inf')
        else:
            self._log_with_timestamp(f"Package creation completed in {elapsed_time:.2f} seconds, but file not found", 'wrn')
