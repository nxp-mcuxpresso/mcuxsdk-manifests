# -*- coding: utf-8 -*-

import os
import yaml
import zipfile
import shutil
import tempfile
import subprocess
import time
from pathlib import Path
from datetime import datetime

from west.commands import WestCommand
from west.manifest import Manifest
from west import log


class UpdateBoardCommand(WestCommand):
    """Updates repositories for a specific set (board/device/custom) and optionally creates zip packages."""

    def __init__(self):
        super().__init__(
            'update_board',
            'Updates repositories for a board, device, or custom configuration.',
            '''Updates repositories based on board, device, or custom configuration.
            
            Without -o/--output: Simply updates the configured repositories for the specified set.
            With -o/--output: Updates repositories and creates a filtered zip package with optional 
            documentation generation and git history inclusion. By default, repositories are updated first,
            but this can be skipped with --no-update for CI/CD scenarios or when repos are already current.
            
            Examples:
              # Update repositories only
              west update_board --set board mcxw23evk
              
              # Update and create package with all optional items
              west update_board --set board mcxw23evk -o package.zip --include-optional
              
              # Skip update, create package without updating and docs (for CI/CD parallel execution)
              west update_board --set board mcxw23evk -o package.zip --no-update --gen-doc
              
              # List available optional repositories and examples
              west update_board --set board mcxw23evk --list-optional
            ''')

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name, help=self.help, description=self.description)
        
        # Core set argument with value
        parser.add_argument('--set', nargs=2, metavar=('TYPE', 'VALUE'), required=True,
                          help='Configuration set: "board BOARD_NAME", "device DEVICE_NAME", or "custom CONFIG_FILE"')
        
        # Output (makes this a packaging operation)
        parser.add_argument('-o', '--output', 
                          help='Output zip file path. When specified, creates a filtered package after updating repositories.')
        
        # Optional repo control
        parser.add_argument('--list-optional', action='store_true',
                          help='List available optional repositories and examples for the specified set.')
        
        # Package-only options (only valid with -o/--output)
        package_group = parser.add_argument_group('Package Options', 
                                                'These options are only available when creating a package with -o/--output')
        package_group.add_argument('--no-update', '-n', action='store_true',
                                 help='Skip repository update step. Assumes repositories are already current. '
                                      'Useful for parallel CI/CD execution or when repositories are already up-to-date. Only valid with -o/--output.')
        package_group.add_argument('--include-optional', nargs='*', metavar='REPO', 
                                 help='Include specific optional repos/examples, or use without arguments to include all optional items. '
                                      'Example: --include-optional repo1 repo2, or just --include-optional for all.')
        package_group.add_argument('--include-git', action='store_true', 
                                 help='Include .git history in package. Only valid with -o/--output.')
        package_group.add_argument('--gen-doc', action='store_true', 
                                 help='Generate and include PDF documentation in package. Requires mcu-sdk-doc repository. Only valid with -o/--output.')
        
        return parser

    def do_run(self, args, unknown_args=None):
        """Main execution method."""
        start_time = time.time()
        self._log_with_timestamp("Starting update_board command")
    
        try:
            # Validate arguments
            self._validate_arguments(args)
            
            # Parse and validate set arguments
            set_type, set_value = self._parse_set_arguments(args)
        
            # Initialize workspace
            workspace_root, manifest_dir, manifest = self._init_workspace()
        
            # Load configuration
            config = self._load_configuration(set_type, set_value, manifest_dir)
        
            # Handle list-only operation
            if args.list_optional:
                self._list_optional_repos(config)
                return
        
            # Process repositories and examples
            final_repos, final_examples = self._process_optional_repos(config, args)
        
            # Get projects to update
            projects = self._get_projects_to_update(manifest, final_repos)
        
            # Update repositories (conditional)
            if not args.no_update:
                self._update_projects(workspace_root, projects)
            else:
                self._log_with_timestamp("Skipping repository update as requested")
                self._validate_projects_exist(workspace_root, projects)
        
            # Create package if requested
            if args.output:
                board_list = self._get_board_list_for_filtering(config, set_type, set_value)
                self._create_package(args, workspace_root, manifest, projects, 
                                   final_repos, final_examples, board_list)
            else:
                self._log_with_timestamp("Repository update completed. No package creation requested.")
            
            elapsed_time = time.time() - start_time
            self._log_with_timestamp(f"Command completed successfully in {elapsed_time:.2f} seconds")
        
        except Exception as e:
            elapsed_time = time.time() - start_time
            self._log_with_timestamp(f"Command failed after {elapsed_time:.2f} seconds: {e}")
            self.die(f"Command failed: {e}")

    def _validate_arguments(self, args):
        """Validate argument combinations."""
        # Package-only options validation
        package_only_options = []
        if args.no_update:
            package_only_options.append('--no-update')
        if args.include_optional:
            package_only_options.append('--include-optional')
        if args.include_git:
            package_only_options.append('--include-git')
        if args.gen_doc:
            package_only_options.append('--gen-doc')
        
        if package_only_options and not args.output:
            options_str = ', '.join(package_only_options)
            raise Exception(f"Options {options_str} are only available when creating a package with -o/--output")

    def _log_with_timestamp(self, message, level='inf'):
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

    def _parse_set_arguments(self, args):
        """Parse and validate set arguments."""
        if len(args.set) != 2:
            raise Exception("--set requires exactly 2 arguments: TYPE and VALUE")
        
        set_type, set_value = args.set
        
        if set_type not in ['board', 'device', 'custom']:
            raise Exception(f"Invalid set type '{set_type}'. Must be 'board', 'device', or 'custom'")
        
        if not set_value.strip():
            raise Exception(f"Value cannot be empty for set type '{set_type}'")
        
        return set_type, set_value.strip()

    def _init_workspace(self):
        """Initialize workspace."""
        try:
            manifest = Manifest.from_topdir()
            workspace_root = self.topdir
            manifest_dir = os.path.dirname(self.manifest.path)
            
            self._log_with_timestamp(f"Workspace root: {workspace_root}")
            self._log_with_timestamp(f"Manifest directory: {manifest_dir}")
            
            return workspace_root, manifest_dir, manifest
        except Exception as e:
            raise Exception(f"Error initializing workspace: {e}")

    def _load_configuration(self, set_type, set_value, manifest_dir):
        """Load configuration based on set type."""
        self._log_with_timestamp(f"Loading {set_type} configuration: {set_value}")
        
        if set_type == 'board':
            return self._load_board_config(set_value, manifest_dir)
        elif set_type == 'device':
            return self._load_device_config(set_value, manifest_dir)
        elif set_type == 'custom':
            return self._load_custom_config(set_value)

    def _load_board_config(self, board_name, manifest_dir):
        """Load configuration from board YAML file with automatic controlled-access inclusion."""
        config_path = os.path.join(manifest_dir, 'boards', f'{board_name}.yml')
    
        if not os.path.isfile(config_path):
            raise Exception(f"Board configuration file not found: {config_path}")
    
        try:
            # Load the main board configuration
            with open(config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
        
            # Automatically look for controlled-access configuration
            # Convention: bifrost/boards/{board_name}.yml
            controlled_config_path = os.path.join(self.topdir, 'bifrost', 'boards', f'{board_name}.yml')
        
            if os.path.isfile(controlled_config_path):
                try:
                    with open(controlled_config_path, 'r', encoding='utf-8') as f:
                        controlled_data = yaml.safe_load(f) or {}
                
                    # Merge controlled-access optional repositories
                    base_optional_repos = data.get('optional_repos', [])
                    controlled_optional_repos = controlled_data.get('optional_repos', [])
                    if controlled_optional_repos:
                        base_optional_repos.extend(controlled_optional_repos)
                        data['optional_repos'] = list(dict.fromkeys(base_optional_repos))  # Remove duplicates
                        self._log_with_timestamp(f"Added {len(controlled_optional_repos)} controlled-access repositories")
                
                    # Merge controlled-access optional examples
                    base_optional_examples = data.get('optional_examples', [])
                    controlled_optional_examples = controlled_data.get('optional_examples', [])
                    if controlled_optional_examples:
                        base_optional_examples.extend(controlled_optional_examples)
                        data['optional_examples'] = list(dict.fromkeys(base_optional_examples))  # Remove duplicates
                        self._log_with_timestamp(f"Added {len(controlled_optional_examples)} controlled-access examples")
                
                    self._log_with_timestamp(f"Loaded controlled-access config: {controlled_config_path}", 'dbg')
                
                except yaml.YAMLError as e:
                    self._log_with_timestamp(f"Error parsing controlled-access config: {e}", 'wrn')
                except Exception as e:
                    self._log_with_timestamp(f"Error loading controlled-access config: {e}", 'wrn')
            else:
                self._log_with_timestamp(f"No controlled-access config found for {board_name}", 'dbg')
        
            config = self._normalize_config(data, board_name)
            self._log_with_timestamp(f"Loaded board configuration: {board_name}")
            return config
        
        except yaml.YAMLError as e:
            raise Exception(f"Error parsing board config file: {e}")

    def _load_device_config(self, device_name, manifest_dir):
        """Load configuration for device by merging related board configurations."""
        # Get board list for the device
        board_list = self._get_boards_for_device(device_name, manifest_dir)
        
        if not board_list:
            raise Exception(f"No boards found for device: {device_name}")
        
        self._log_with_timestamp(f"Found boards for device '{device_name}': {board_list}")
        
        # Merge configurations from all boards
        merged_config = self._merge_board_configs(board_list, manifest_dir)
        merged_config['board_name'] = f"{device_name}_device"
        merged_config['source_boards'] = board_list
        
        self._log_with_timestamp(f"Merged configuration from {len(board_list)} board(s)")
        return merged_config

    def _load_custom_config(self, custom_file_path):
        """Load configuration from custom YAML file."""
        if not os.path.isfile(custom_file_path):
            raise Exception(f"Custom configuration file not found: {custom_file_path}")
        
        try:
            with open(custom_file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            
            board_name = data.get('board_name', 'custom')
            config = self._normalize_config(data, board_name)
            self._log_with_timestamp(f"Loaded custom configuration from: {custom_file_path}")
            return config
            
        except yaml.YAMLError as e:
            raise Exception(f"Error parsing custom config file: {e}")

    def _get_boards_for_device(self, device_name, manifest_dir):
        """Get list of boards that support the specified device."""
        # Device to boards mapping - centralized for easy maintenance
        device_to_boards_mapping = {
            'mcxw23': ['frdmmcxw23', 'mcxw23evk'],
            'mcxa15': ['frdmmcxa15'],
            'mcxn94': ['frdmmcxn94'],
            # Add more device mappings as needed
        }
        
        boards_dir = os.path.join(manifest_dir, 'boards')
        found_boards = []
        
        device_lower = device_name.lower()
        
        if device_lower in device_to_boards_mapping:
            # Use predefined mapping
            potential_boards = device_to_boards_mapping[device_lower]
            for board in potential_boards:
                board_file = os.path.join(boards_dir, f'{board}.yml')
                if os.path.isfile(board_file):
                    found_boards.append(board)
        else:
            # Fallback: search through all board files
            if os.path.isdir(boards_dir):
                try:
                    for filename in os.listdir(boards_dir):
                        if filename.endswith('.yml'):
                            board_name = filename[:-4]  # Remove .yml extension
                            if device_lower in board_name.lower():
                                found_boards.append(board_name)
                except (OSError, PermissionError) as e:
                    self._log_with_timestamp(f"Cannot access boards directory: {e}", 'wrn')
        
        return found_boards

    def _merge_board_configs(self, board_list, manifest_dir):
        """Merge configurations from multiple board YAML files."""
        merged_repos = set()
        merged_examples = set()
        merged_optional_repos = set()
        merged_optional_examples = set()
    
        for board_name in board_list:
            try:
                config = self._load_board_config(board_name, manifest_dir)
            
                # Merge core lists
                merged_repos.update(config['repo_list'])
                merged_examples.update(config['example_list'])
            
                # Merge optional lists (now includes controlled-access items)
                merged_optional_repos.update(config['optional_repos'])
                merged_optional_examples.update(config['optional_examples'])
            
            except Exception as e:
                self._log_with_timestamp(f"Failed to load board config '{board_name}': {e}", 'wrn')
                continue
    
        return {
            'repo_list': sorted(list(merged_repos)),  # Sort for consistent output
            'example_list': sorted(list(merged_examples)),
            'optional_repos': sorted(list(merged_optional_repos)),
            'optional_examples': sorted(list(merged_optional_examples)),
            'board_name': 'merged_device_config'
        }

    def _normalize_config(self, data, board_name):
        """Normalize configuration data to consistent format."""
        return {
            'repo_list': data.get('repo_list', []),
            'example_list': data.get('example_list', []),
            'optional_repos': data.get('optional_repos', []),
            'optional_examples': data.get('optional_examples', []),
            'board_name': board_name
        }

    def _get_board_list_for_filtering(self, config, set_type, set_value):
        """Get board list for filtering purposes."""
        if set_type == 'device':
            return config.get('source_boards', [set_value])
        elif set_type == 'board':
            return [set_value]
        else:  # custom
            return [config.get('board_name', 'custom')]

    def _list_optional_repos(self, config):
        """List available optional repositories and examples."""
        self._log_with_timestamp("Listing optional repositories and examples")
        
        log.inf("Optional repositories (excluded by default):")
        if not config['optional_repos']:
            log.inf("  None")
        else:
            for repo in config['optional_repos']:
                log.inf(f"  {repo}")
        
        log.inf("\nOptional examples (excluded by default):")
        if not config['optional_examples']:
            log.inf("  None")
        else:
            for example in config['optional_examples']:
                log.inf(f"  {example}")

    def _process_optional_repos(self, config, args):
        """Process optional repository inclusion."""
        # Start with core lists (required repos/examples)
        final_repos = list(config['repo_list'])
        final_examples = list(config['example_list'])
        
        # Track what we're including
        included_optional_repos = set()
        included_optional_examples = set()
        
        # Only include optional items if explicitly requested
        if args.include_optional:
            if 'all' in args.include_optional:
                # Include all optional items
                included_optional_repos.update(config['optional_repos'])
                included_optional_examples.update(config['optional_examples'])
            else:
                # Include specific optional items
                for name in args.include_optional:
                    if name in config['optional_repos']:
                        included_optional_repos.add(name)
                    elif name in config['optional_examples']:
                        included_optional_examples.add(name)
                    else:
                        self._log_with_timestamp(f"Unknown optional item: {name}", 'wrn')
        
        # Add included optional items to final lists
        final_repos.extend(sorted(included_optional_repos))
        final_examples.extend(sorted(included_optional_examples))
        
        # Log summary
        self._log_with_timestamp(f"Final repository count: {len(final_repos)} (core: {len(config['repo_list'])}, optional: {len(included_optional_repos)})")
        self._log_with_timestamp(f"Final example count: {len(final_examples)} (core: {len(config['example_list'])}, optional: {len(included_optional_examples)})")
        
        if included_optional_repos:
            self._log_with_timestamp(f"Including optional repos: {sorted(included_optional_repos)}")
        if included_optional_examples:
            self._log_with_timestamp(f"Including optional examples: {sorted(included_optional_examples)}")
        
        return final_repos, final_examples

    def _get_projects_to_update(self, manifest, repo_list):
        """Get projects to update from manifest."""
        projects = [p for p in manifest.projects if p.name in repo_list]
        
        if projects:
            project_names = [p.name for p in projects]
            self._log_with_timestamp(f"Found {len(projects)} projects to update: {project_names}")
        else:
            self._log_with_timestamp("No projects found to update", 'wrn')
            
        return projects

    def _update_projects(self, workspace_root, projects):
        """Update specified projects."""
        if not projects:
            self._log_with_timestamp("No projects to update")
            return
        
        project_names = [p.name for p in projects]
        self._log_with_timestamp(f"Updating {len(projects)} repositories: {project_names}")
        
        try:
            cmd = ['west', 'update', '-n', '-o=--depth=1'] + project_names
            start_time = time.time()
            
            result = subprocess.run(cmd, cwd=workspace_root, check=True, capture_output=True, text=True)
            
            elapsed_time = time.time() - start_time
            self._log_with_timestamp(f"Repository update completed in {elapsed_time:.2f} seconds")
            
        except subprocess.CalledProcessError as e:
            raise Exception(f"Update failed: {e.stderr}")

    def _validate_projects_exist(self, workspace_root, projects):
        """Validate that required projects exist in the workspace when skipping update."""
        if not projects:
            self._log_with_timestamp("No projects to validate")
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
            self._log_with_timestamp(f"Validated {len(existing_projects)} existing projects")
            for project in existing_projects:
                log.dbg(f"  ✓ {project}")
        
        if missing_projects:
            self._log_with_timestamp(f"Missing {len(missing_projects)} required projects:", 'wrn')
            for project in missing_projects:
                self._log_with_timestamp(f"  ✗ {project}", 'wrn')
            
            raise Exception(f"Missing required projects: {', '.join(missing_projects)}. "
                        f"Run without --no-update to checkout missing repositories.")
        
        self._log_with_timestamp(f"All {len(projects)} required projects are present in workspace")

    def _create_package(self, args, workspace_root, manifest, projects, repo_list, example_list, board_list):
        """Create zip package."""
        self._log_with_timestamp(f"Creating package: {args.output}")
        
        temp_dir = tempfile.mkdtemp(prefix='west_board_')
        try:
            self._log_with_timestamp(f"Using temporary directory: {temp_dir}")
            
            # Copy repositories with filtering and symlink preservation
            copy_start = time.time()
            self._copy_repos_with_symlinks(workspace_root, temp_dir, projects, board_list, args.include_git)
            copy_time = time.time() - copy_start
            self._log_with_timestamp(f"Repository copying completed in {copy_time:.2f} seconds")
            
            # Handle documentation
            if args.gen_doc:
                doc_start = time.time()
                self._generate_docs_in_temp_dir(temp_dir, board_list)
                doc_time = time.time() - doc_start
                self._log_with_timestamp(f"Documentation generation completed in {doc_time:.2f} seconds")
            else:
                self._remove_docs_directory(temp_dir)
            
            # Filter examples
            filter_start = time.time()
            self._filter_examples(temp_dir, manifest, repo_list, example_list)
            self._filter_example_yml_files(temp_dir, board_list)
            filter_time = time.time() - filter_start
            self._log_with_timestamp(f"Example filtering completed in {filter_time:.2f} seconds")
            
            # Create archive with symlink support
            archive_start = time.time()
            self._create_archive_with_symlinks(temp_dir, args.output)
            archive_time = time.time() - archive_start
            self._log_with_timestamp(f"Archive creation completed in {archive_time:.2f} seconds")
            
            # Get final package size
            if os.path.exists(args.output):
                size_mb = os.path.getsize(args.output) / (1024 * 1024)
                self._log_with_timestamp(f"Package created: {args.output} ({size_mb:.1f} MB)")
            
        finally:
            if os.path.exists(temp_dir):
                self._log_with_timestamp(f"Cleaning up temporary directory: {temp_dir}", 'dbg')
                shutil.rmtree(temp_dir)

    def _copy_repos_with_symlinks(self, workspace_root, temp_dir, projects, board_list, include_git):
        """Copy repositories with symlink preservation and filtering."""
        # Get other board names for filtering (optimized for board list)
        other_boards = self._get_other_board_names(workspace_root, board_list)
        
        # Copy workspace metadata
        for meta_dir in ['.west', 'manifests']:
            src = os.path.join(workspace_root, meta_dir)
            if os.path.exists(src):
                dst = os.path.join(temp_dir, meta_dir)
                self._copy_directory_with_symlinks(src, dst, meta_dir, other_boards, include_git)
        
        # Copy project repositories
        for project in projects:
            # # Skip mcu-sdk-doc repository by default (docs folder)
            # if project.name == "mcu-sdk-doc":
            #     self._log_with_timestamp(f"Skipping mcu-sdk-doc repository (docs folder)", 'dbg')
            #     continue

            if project.name == "core":
                # Handle core project subdirectories
                for subdir in ["drivers", "arch", "cmake", "share", "scripts", "devices"]:
                    src = os.path.join(workspace_root, "mcuxsdk", subdir)
                    if os.path.exists(src):
                        dst = os.path.join(temp_dir, "mcuxsdk", subdir)
                        project_path = f"mcuxsdk/{subdir}"
                        self._copy_directory_with_symlinks(src, dst, project_path, other_boards, include_git)
            else:
                src = os.path.join(workspace_root, project.path)
                if os.path.exists(src):
                    dst = os.path.join(temp_dir, project.path)
                    self._copy_directory_with_symlinks(src, dst, project.path, other_boards, include_git)
        
        # Copy mcuxsdk root files
        self._copy_mcuxsdk_root_files(workspace_root, temp_dir)

    def _copy_directory_with_symlinks(self, src_dir, dest_dir, project_path, other_boards, include_git, rel_path=""):
        """Recursively copy directory contents, preserving symlinks."""
        try:
            if not os.path.exists(src_dir):
                return
                
            # Create destination directory if it doesn't exist
            os.makedirs(dest_dir, exist_ok=True)
            
            # List all items in the source directory
            try:
                items = os.listdir(src_dir)
            except (OSError, PermissionError) as e:
                self._log_with_timestamp(f"Cannot access directory {src_dir}: {e}", 'wrn')
                return
                
            for item in items:
                src_item_path = os.path.join(src_dir, item)
                dest_item_path = os.path.join(dest_dir, item)
                item_rel_path = os.path.join(rel_path, item) if rel_path else item
                
                # Check if item should be filtered out
                if self._should_filter_path(item_rel_path, other_boards, include_git, project_path):
                    continue
                    
                if os.path.islink(src_item_path):
                    # Handle symbolic links (both files and directories)
                    self._copy_symlink(src_item_path, dest_item_path, item_rel_path, project_path)
                    
                elif os.path.isdir(src_item_path):
                    # Handle regular directories - recurse into them
                    self._copy_directory_with_symlinks(
                        src_item_path, dest_item_path, project_path, 
                        other_boards, include_git, item_rel_path
                    )
                    
                elif os.path.isfile(src_item_path):
                    # Handle regular files
                    try:
                        # Ensure destination directory exists
                        os.makedirs(os.path.dirname(dest_item_path), exist_ok=True)
                        shutil.copy2(src_item_path, dest_item_path)
                    except (OSError, IOError) as e:
                        self._log_with_timestamp(f"Failed to copy file {src_item_path}: {e}", 'wrn')
                        
        except Exception as e:
            self._log_with_timestamp(f"Error processing directory {src_dir}: {e}", 'wrn')

    def _should_filter_path(self, rel_path, other_boards, include_git, project_path):
        """Check if a path should be filtered out based on filtering rules."""
        path_components = rel_path.split(os.sep)
        
        # Check for other board names
        for comp in path_components:
            if comp in other_boards:
                log.dbg(f"Skipping '{os.path.join(project_path, rel_path)}' (contains excluded board name: {comp})")
                return True
            
        # Check for .git directories/files
        if not include_git and any(".git" in comp for comp in path_components):
            log.dbg(f"Skipping '{os.path.join(project_path, rel_path)}' (contains .git)")
            return True
            
        return False

    def _copy_symlink(self, src_symlink_path, dest_symlink_path, rel_path, project_path):
        """Copy a symbolic link (file or directory), preserving its symlink nature."""
        try:
            # Read the symlink target
            link_target = os.readlink(src_symlink_path)
            
            # Remove destination if it exists
            if os.path.exists(dest_symlink_path) or os.path.islink(dest_symlink_path):
                if os.path.isdir(dest_symlink_path) and not os.path.islink(dest_symlink_path):
                    shutil.rmtree(dest_symlink_path)
                else:
                    os.remove(dest_symlink_path)
            
            # Ensure destination directory exists
            dest_dir = os.path.dirname(dest_symlink_path)
            if dest_dir:
                os.makedirs(dest_dir, exist_ok=True)
            
            # Create the symbolic link
            os.symlink(link_target, dest_symlink_path)
            
            # Determine if it's a directory or file symlink
            symlink_type = "directory" if os.path.isdir(src_symlink_path) else "file"
            log.dbg(f"Created {symlink_type} symlink: {os.path.join(project_path, rel_path)} -> {link_target}")
            
        except (OSError, IOError) as e:
            self._log_with_timestamp(f"Failed to copy symlink {src_symlink_path}: {e}", 'wrn')
            
            # Fallback: try to copy the target content
            try:
                if os.path.isdir(src_symlink_path):
                    log.dbg(f"Fallback: Copying directory content instead of symlink")
                    shutil.copytree(src_symlink_path, dest_symlink_path, symlinks=False)
                else:
                    log.dbg(f"Fallback: Copying file content instead of symlink")
                    shutil.copy2(src_symlink_path, dest_symlink_path)
            except Exception as fallback_e:
                self._log_with_timestamp(f"Fallback copy also failed for {src_symlink_path}: {fallback_e}", 'wrn')

    def _copy_mcuxsdk_root_files(self, workspace_root, temp_dir):
        """Copy files directly under mcuxsdk/ folder."""
        mcuxsdk_dir = os.path.join(workspace_root, "mcuxsdk")
        if not os.path.exists(mcuxsdk_dir):
            return
            
        dest_mcuxsdk_dir = os.path.join(temp_dir, "mcuxsdk")
        os.makedirs(dest_mcuxsdk_dir, exist_ok=True)
        
        for item in os.listdir(mcuxsdk_dir):
            src_path = os.path.join(mcuxsdk_dir, item)
            if os.path.isfile(src_path) or os.path.islink(src_path):
                dest_path = os.path.join(dest_mcuxsdk_dir, item)
                self._copy_file_or_symlink(src_path, dest_path)

    def _copy_file_or_symlink(self, src_path, dest_path):
        """Copy file or symlink, preserving symlink properties."""
        try:
            if os.path.islink(src_path):
                # Handle symbolic links
                link_target = os.readlink(src_path)
                
                # Remove destination if it exists
                if os.path.exists(dest_path) or os.path.islink(dest_path):
                    os.remove(dest_path)
                
                # Create the symbolic link
                os.symlink(link_target, dest_path)
                log.dbg(f"Created symlink: {dest_path} -> {link_target}")
            else:
                # Handle regular files
                shutil.copy2(src_path, dest_path)
        except (OSError, IOError) as e:
            self._log_with_timestamp(f"Failed to copy {src_path} to {dest_path}: {e}", 'wrn')

    def _get_other_board_names(self, workspace_root, target_board_list):
        """Get other board names for filtering (optimized to accept board list)."""
        # Convert single board to list for consistency
        if isinstance(target_board_list, str):
            target_board_list = [target_board_list]
        
        # Convert to set for faster lookup
        target_boards_set = set(target_board_list)
        
        other_boards = set()
        boards_dir = os.path.join(workspace_root, "mcuxsdk", "examples", "_boards")
        
        if os.path.isdir(boards_dir):
            try:
                for name in os.listdir(boards_dir):
                    board_path = os.path.join(boards_dir, name)
                    # Only consider directories that are not in our target board list
                    if (os.path.isdir(board_path) and 
                        name not in target_boards_set):
                        other_boards.add(name)
            except (OSError, PermissionError) as e:
                self._log_with_timestamp(f"Cannot access boards directory {boards_dir}: {e}", 'wrn')
        
        if other_boards:
            self._log_with_timestamp(f"Target boards: {sorted(target_boards_set)}")
            self._log_with_timestamp(f"Filtering out paths containing other board names: {sorted(other_boards)}", 'dbg')
        else:
            self._log_with_timestamp(f"Target boards: {sorted(target_boards_set)}")
            self._log_with_timestamp("No other boards found for path filtering", 'dbg')
            
        return other_boards

    def _generate_docs_in_temp_dir(self, temp_dir, board_list):
        """Generate documentation for one or more boards in the temporary directory."""
        # Convert single board to list for consistency
        if isinstance(board_list, str):
            board_list = [board_list]
        
        if not board_list:
            return
        
        self._log_with_timestamp(f"Generating documentation for {len(board_list)} board(s): {board_list}")
        
        # Ensure docs directory exists in temp_dir
        docs_dir = os.path.join(temp_dir, "mcuxsdk", "docs")
        os.makedirs(docs_dir, exist_ok=True)
        
        success_count = 0
        
        for board_name in board_list:
            try:
                self._log_with_timestamp(f"Generating docs for: {board_name}")
                
                # Run west doc command in the temporary directory
                cmd = ['west', 'doc', 'pdf', '--board', board_name]
                doc_start = time.time()
                
                # Set up environment for west command in temp directory
                env = os.environ.copy()
                
                # Run the command in the temporary directory
                result = subprocess.run(cmd, cwd=temp_dir, check=True, capture_output=True, text=True, env=env)
                doc_time = time.time() - doc_start
                
                # Look for generated PDF in the temporary directory
                temp_pdf_src = os.path.join(temp_dir, "mcuxsdk", "docs", "_build", "latex", f"mcuxsdk-{board_name}.pdf")
                
                if os.path.exists(temp_pdf_src):
                    # Move PDF to the final docs location
                    pdf_dest = os.path.join(docs_dir, f"mcuxsdk-{board_name}.pdf")
                    shutil.move(temp_pdf_src, pdf_dest)
                    success_count += 1
                    self._log_with_timestamp(f"Documentation generated for {board_name} in {doc_time:.2f} seconds")
                else:
                    self._log_with_timestamp(f"PDF not found for board: {board_name} at {temp_pdf_src}", 'wrn')
                    
            except subprocess.CalledProcessError as e:
                self._log_with_timestamp(f"Documentation generation failed for {board_name}: {e.stderr}", 'wrn')
                continue
            except Exception as e:
                self._log_with_timestamp(f"Error generating docs for {board_name}: {e}", 'wrn')
                continue
        
        # Clean up the docs build directory but keep the PDFs
        self._cleanup_docs_build_directory(temp_dir)
        
        if success_count > 0:
            self._log_with_timestamp(f"Successfully generated documentation for {success_count}/{len(board_list)} boards")
        else:
            self._log_with_timestamp("No documentation was generated", 'wrn')
            # Remove empty docs directory
            if os.path.exists(docs_dir) and not os.listdir(docs_dir):
                os.rmdir(docs_dir)

    def _cleanup_docs_build_directory(self, temp_dir):
        """Back up PDF files, clean the entire docs folder, then restore only the PDFs."""
        docs_dir = os.path.join(temp_dir, "mcuxsdk", "docs")
        
        if not os.path.exists(docs_dir):
            return
        
        try:
            # Find and back up all PDF files
            pdf_files = []
            for root, dirs, files in os.walk(docs_dir):
                for file in files:
                    if file.endswith('.pdf') and file.startswith('mcuxsdk-'):
                        pdf_path = os.path.join(root, file)
                        pdf_files.append((file, pdf_path))
            
            if not pdf_files:
                self._log_with_timestamp("No PDF files found to preserve", 'dbg')
                shutil.rmtree(docs_dir)
                return
            
            self._log_with_timestamp(f"Backing up {len(pdf_files)} PDF file(s): {[name for name, _ in pdf_files]}", 'dbg')
            
            # Create temporary backup directory
            temp_backup_dir = tempfile.mkdtemp(prefix='pdf_backup_')
            
            try:
                # Copy PDF files to backup location
                for pdf_name, pdf_path in pdf_files:
                    backup_path = os.path.join(temp_backup_dir, pdf_name)
                    shutil.copy2(pdf_path, backup_path)
                
                # Remove entire docs directory
                shutil.rmtree(docs_dir)
                self._log_with_timestamp("Cleared docs directory", 'dbg')
                
                # Recreate docs directory
                os.makedirs(docs_dir, exist_ok=True)
                
                # Restore PDF files to docs directory
                for pdf_name, _ in pdf_files:
                    backup_path = os.path.join(temp_backup_dir, pdf_name)
                    restore_path = os.path.join(docs_dir, pdf_name)
                    shutil.copy2(backup_path, restore_path)
                    self._log_with_timestamp(f"Restored PDF: {pdf_name}", 'dbg')
                    
            finally:
                # Clean up temporary backup directory
                if os.path.exists(temp_backup_dir):
                    shutil.rmtree(temp_backup_dir)
                    
        except Exception as e:
            self._log_with_timestamp(f"Failed to clean up docs directory: {e}", 'wrn')

    def _remove_docs_directory(self, temp_dir):
        """Remove docs directory when documentation generation is not requested."""
        dest_docs_dir = os.path.join(temp_dir, "mcuxsdk", "docs")
        if os.path.exists(dest_docs_dir):
            shutil.rmtree(dest_docs_dir)

    def _filter_examples(self, temp_dir, manifest, repo_list, example_list):
        """Filter examples and remove unwanted directories."""
        # Remove unwanted project directories
        removed_projects = 0
        for project in manifest.projects:
            if project.name not in repo_list and project.path != "manifests":
                proj_dir = os.path.join(temp_dir, project.path)
                if os.path.exists(proj_dir):
                    shutil.rmtree(proj_dir)
                    removed_projects += 1
        
        if removed_projects > 0:
            self._log_with_timestamp(f"Removed {removed_projects} unwanted project directories", 'dbg')
        
        # Filter examples directory
        examples_root = os.path.join(temp_dir, "mcuxsdk", "examples")
        removed_examples = 0
        
        if os.path.exists(examples_root):
            for example_dir in os.listdir(examples_root):
                example_path = os.path.join(examples_root, example_dir)
                if not os.path.isdir(example_path):
                    continue
                    
                # Keep _boards and _common directories
                if example_dir in ["_boards", "_common"]:
                    continue
                    
                # Remove examples not in example_list
                if example_dir not in example_list:
                    shutil.rmtree(example_path)
                    removed_examples += 1
        
        if removed_examples > 0:
            self._log_with_timestamp(f"Removed {removed_examples} unwanted example directories", 'dbg')

    def _filter_example_yml_files(self, temp_dir, target_board_list):
        """Filter example.yml files to only include boards that match any of the specified board names."""
        # Convert single board to list for consistency
        if isinstance(target_board_list, str):
            target_board_list = [target_board_list]
        
        self._log_with_timestamp(f"Filtering example.yml files for boards: {target_board_list}")
        
        # Find all example.yml files
        example_yml_files = []
        examples_dir = os.path.join(temp_dir, "mcuxsdk", "examples")
        if os.path.exists(examples_dir):
            for root, dirs, files in os.walk(examples_dir):
                for file in files:
                    if file == 'example.yml':
                        example_yml_files.append(os.path.join(root, file))
        
        if not example_yml_files:
            self._log_with_timestamp("No example.yml files found to filter")
            return
        
        self._log_with_timestamp(f"Processing {len(example_yml_files)} example.yml files")
        
        total_filtered_count = 0
        modified_count = 0
        total_kept_count = 0
        
        for yml_file in example_yml_files:
            try:
                with open(yml_file, 'r', encoding='utf-8') as f:
                    yaml_data = yaml.safe_load(f)
                
                if not yaml_data or not isinstance(yaml_data, dict):
                    continue
                
                file_modified = False
                file_filtered_count = 0
                file_kept_count = 0
                
                # Process each example configuration
                for example_name, example_config in yaml_data.items():
                    if not isinstance(example_config, dict) or 'boards' not in example_config:
                        continue
                    
                    boards_config = example_config['boards']
                    if not isinstance(boards_config, dict):
                        continue
                    
                    # Filter boards to keep only those matching any of the target board names
                    original_board_count = len(boards_config)
                    filtered_boards = {}
                    
                    for board_key, board_value in boards_config.items():
                        # Check if this board_key matches any of our target boards
                        keep_board = any(target_board in board_key for target_board in target_board_list)
                        
                        if keep_board:
                            filtered_boards[board_key] = board_value
                    
                    # Update the example configuration with filtered boards
                    example_config['boards'] = filtered_boards
                    
                    boards_removed = original_board_count - len(filtered_boards)
                    boards_kept = len(filtered_boards)
                    
                    if boards_removed > 0:
                        file_modified = True
                        file_filtered_count += boards_removed
                    
                    file_kept_count += boards_kept
                
                # Write back the modified YAML if changes were made
                if file_modified:
                    with open(yml_file, 'w', encoding='utf-8') as f:
                        yaml.safe_dump(yaml_data, f, default_flow_style=False, sort_keys=False)
                    
                    modified_count += 1
                    total_filtered_count += file_filtered_count
                    total_kept_count += file_kept_count
                    
                    log.dbg(f"Processed {yml_file}: kept {file_kept_count} boards, removed {file_filtered_count} boards")
                elif file_kept_count > 0:
                    total_kept_count += file_kept_count
                
            except yaml.YAMLError as e:
                self._log_with_timestamp(f"Error parsing YAML file {yml_file}: {e}", 'wrn')
                continue
            except (IOError, OSError) as e:
                self._log_with_timestamp(f"Error reading/writing file {yml_file}: {e}", 'wrn')
                continue
            except Exception as e:
                self._log_with_timestamp(f"Unexpected error processing {yml_file}: {e}", 'wrn')
                continue
        
        self._log_with_timestamp(f"Example.yml filtering completed: {modified_count} files modified, "
                               f"{total_kept_count} board entries kept, {total_filtered_count} board entries removed")

    def _create_archive_with_symlinks(self, temp_dir, output_zip_path):
        """Create zip archive from temporary directory with proper symlink support."""
        try:
            # Ensure the output directory exists
            output_zip_dir = os.path.dirname(output_zip_path)
            if output_zip_dir and not os.path.exists(output_zip_dir):
                os.makedirs(output_zip_dir)

            self._log_with_timestamp(f"Creating archive with symlink support: {output_zip_path}")
            
            # Count files for progress tracking
            total_files = sum([len(files) for r, d, files in os.walk(temp_dir)])
            self._log_with_timestamp(f"Archiving {total_files} files and directories")
            
            # Use custom traversal to handle symlinks properly
            with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
                self._add_directory_to_zip(zipf, temp_dir, "")
            
            # Verify symlinks in the created archive
            self._verify_archive_symlinks(output_zip_path)
            
        except Exception as e:
            raise Exception(f"Error creating archive: {e}")

    def _add_directory_to_zip(self, zipf, src_dir, arc_prefix):
        """Recursively add directory contents to zip, handling symlinks properly."""
        try:
            items = os.listdir(src_dir)
        except (OSError, PermissionError) as e:
            self._log_with_timestamp(f"Cannot access directory {src_dir}: {e}", 'wrn')
            return
            
        for item in items:
            src_path = os.path.join(src_dir, item)
            arc_path = os.path.join(arc_prefix, item) if arc_prefix else item
            
            # Normalize path separators for zip format
            arc_path = arc_path.replace(os.sep, '/')
            
            if os.path.islink(src_path):
                # Handle symbolic links (both files and directories)
                self._add_symlink_to_zip(zipf, src_path, arc_path)
                    
            elif os.path.isdir(src_path):
                # Regular directory - add directory entry and recurse
                if not arc_path.endswith('/'):
                    arc_path += '/'
                zipf.writestr(arc_path, '')  # Add directory entry
                
                # Recurse into directory
                self._add_directory_to_zip(zipf, src_path, arc_path.rstrip('/'))
                
            elif os.path.isfile(src_path):
                # Regular file
                zipf.write(src_path, arc_path)

    def _add_symlink_to_zip(self, zipf, symlink_path, arc_path):
        """Add a symbolic link to the zip file with proper attributes for cross-platform compatibility."""
        try:
            # Read the symlink target
            link_target = os.readlink(symlink_path)
            
            # Create ZipInfo for the symlink
            zip_info = zipfile.ZipInfo(arc_path)
            
            # Set proper external attributes for symlinks
            # Use the standard Unix symlink format that most unzip tools recognize
            zip_info.external_attr = (0o120000 | 0o755) << 16  # S_IFLNK | permissions
            
            # Set compression method - don't compress symlink targets
            zip_info.compress_type = zipfile.ZIP_STORED
            
            # Set file size to the length of the target path
            target_bytes = link_target.encode('utf-8')
            zip_info.file_size = len(target_bytes)
            zip_info.compress_size = len(target_bytes)
            
            # Set timestamps
            try:
                stat_info = os.lstat(symlink_path)  # lstat doesn't follow symlinks
                zip_info.date_time = time.localtime(stat_info.st_mtime)[:6]
            except OSError:
                # Use current time if stat fails
                zip_info.date_time = time.localtime()[:6]
            
            # Set the create_system to indicate Unix origin
            # This is essential for proper symlink recognition
            zip_info.create_system = 3  # Unix
            
            # Set version needed to extract
            # Version 2.0 is needed for symlinks
            zip_info.extract_version = 20
            
            # Write the symlink target as the file content
            zipf.writestr(zip_info, target_bytes)
            
            # Determine symlink type for logging
            symlink_type = "directory" if os.path.isdir(symlink_path) else "file"
            log.dbg(f"Added {symlink_type} symlink to archive: {arc_path} -> {link_target}")
            
        except (OSError, IOError) as e:
            self._log_with_timestamp(f"Failed to add symlink {symlink_path} to archive: {e}", 'wrn')
            # Fallback: try to add as regular file/directory
            try:
                if os.path.isdir(symlink_path):
                    # Add as regular directory
                    if not arc_path.endswith('/'):
                        arc_path += '/'
                    zipf.writestr(arc_path, '')
                    self._add_directory_to_zip(zipf, symlink_path, arc_path.rstrip('/'))
                else:
                    # Add as regular file
                    zipf.write(symlink_path, arc_path)
            except Exception as fallback_e:
                self._log_with_timestamp(f"Fallback also failed for {symlink_path}: {fallback_e}", 'wrn')

    def _verify_archive_symlinks(self, archive_path):
        """Verify that symlinks are properly stored in the archive."""
        try:
            with zipfile.ZipFile(archive_path, 'r') as zipf:
                symlink_count = 0
                file_symlinks = 0
                dir_symlinks = 0
                
                for info in zipf.infolist():
                    # Check if this entry represents a symlink
                    # Look for Unix symlink file type in external attributes
                    unix_mode = (info.external_attr >> 16) & 0o777777
                    is_symlink = (unix_mode & 0o170000) == 0o120000  # S_IFLNK
                    
                    if is_symlink:
                        symlink_count += 1
                        try:
                            target = zipf.read(info.filename).decode('utf-8')
                            # Check if the original symlink pointed to a directory
                            # Use heuristics since we can't easily determine from zip
                            if os.path.isabs(target) or '/' in target:
                                # This is a heuristic - if target contains path separators,
                                # it might be a directory symlink
                                symlink_type = "directory (likely)"
                                dir_symlinks += 1
                            else:
                                symlink_type = "file (likely)"
                                file_symlinks += 1
                            log.dbg(f"Verified {symlink_type} symlink in archive: {info.filename} -> {target}")
                        except Exception as e:
                            self._log_with_timestamp(f"Could not read symlink target for {info.filename}: {e}", 'wrn')
                
                if symlink_count > 0:
                    self._log_with_timestamp(f"Archive contains {symlink_count} symbolic links "
                                           f"({file_symlinks} files, {dir_symlinks} directories)")
                    self._log_with_timestamp("Note: Symlinks should work properly when extracted with standard unzip tools")
                else:
                    self._log_with_timestamp("No symbolic links found in archive", 'dbg')
                    
        except Exception as e:
            self._log_with_timestamp(f"Could not verify symlinks in archive: {e}", 'wrn')
