# Copyright 2025 NXP
# SPDX-License-Identifier: BSD-3-Clause

"""
Configuration loading and management utility.

This module provides functionality to load and manage board, device, and custom
configurations including controlled-access configurations and merging.
"""

import os
import yaml
from typing import Dict, List, Any, Optional, Callable
from .device_board_mapper import DeviceBoardMapper


class BoardConfig:
    """Represents a board configuration."""

    def __init__(self, name: str, repo_list: List[str], example_list: List[str],
                 optional_repos: List[str] = None, optional_examples: List[str] = None,
                 board_dependencies: List[str] = None,
                 devices_int_paths: List[str] = None,
                 examples_int_paths: List[str] = None,
                 devices_filter: List[str] = None,
                 drivers_filter: List[str] = None):
        self.name = name
        self.repo_list = repo_list or []
        self.example_list = example_list or []
        self.optional_repos = optional_repos or []
        self.optional_examples = optional_examples or []
        self.board_dependencies = board_dependencies or []
        # Migration: sub-paths taken from the live mcuxsdk/devices_int/ repo and merged into the
        # package's public mcuxsdk/devices/ tree (package-only; the live workspace is never modified).
        self.devices_int_paths = devices_int_paths or []
        # Migration: sub-paths taken from the live mcuxsdk/examples_int/ repo and merged into the
        # package's public mcuxsdk/examples/ tree (package-only). Use full relative paths such as
        # "_boards/mcxw23evk", "_devices/MCXW235", or any example category. Migrated _boards/_devices
        # entries are also promoted to the public example.yml format so `west list_project` sees them.
        self.examples_int_paths = examples_int_paths or []
        # Tailoring: sub-paths under mcuxsdk/devices/ to keep during packaging (empty = keep all)
        self.devices_filter = devices_filter or []
        # Tailoring: folder names under mcuxsdk/drivers/ to keep during packaging (empty = keep all)
        self.drivers_filter = drivers_filter or []
        self.source_boards = []  # For device configs

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            'board_name': self.name,
            'repo_list': self.repo_list,
            'example_list': self.example_list,
            'optional_repos': self.optional_repos,
            'optional_examples': self.optional_examples,
            'board_dependencies': self.board_dependencies,
            'devices_int_paths': self.devices_int_paths,
            'examples_int_paths': self.examples_int_paths,
            'devices_filter': self.devices_filter,
            'drivers_filter': self.drivers_filter,
        }
        if self.source_boards:
            result['source_boards'] = self.source_boards
        return result

    def get_all_filtering_boards(self) -> List[str]:
        """Get all boards that should be preserved during filtering (self + dependencies)."""
        boards = [self.name]
        boards.extend(self.board_dependencies)
        return list(dict.fromkeys(boards))  # Remove duplicates while preserving order

    def get_all_repos(self, include_optional: bool = False) -> List[str]:
        """Get all repositories, optionally including optional ones."""
        repos = list(self.repo_list)
        if include_optional:
            repos.extend(self.optional_repos)
        return repos
    
    def get_all_examples(self, include_optional: bool = False) -> List[str]:
        """Get all examples, optionally including optional ones."""
        examples = list(self.example_list)
        if include_optional:
            examples.extend(self.optional_examples)
        return examples


class ConfigLoader:
    """Independent configuration loader for boards, devices, and custom configs."""
    
    def __init__(self, workspace_root: str, manifest_dir: str, logger: Callable[[str, str], None] = None):
        self.workspace_root = workspace_root
        self.manifest_dir = manifest_dir
        self.logger = logger or self._default_logger
        self.device_mapper = DeviceBoardMapper(workspace_root, manifest_dir, logger)
    
    def _default_logger(self, message: str, level: str = 'inf'):
        """Default logger that does nothing."""
        pass
    
    def load_board_config(self, board_name: str) -> BoardConfig:
        """Load configuration for a specific board."""
        self.logger(f"Loading board configuration: {board_name}", 'dbg')
        
        config_path = os.path.join(self.manifest_dir, 'boards', f'{board_name}.yml')
        self.logger(f"Looking for config file: {config_path}", 'dbg')

        if os.path.isfile(config_path):
            # Public config is the base; controlled-access config (bifrost) adds overlay fields.
            self.logger(f"Loading main configuration from: {config_path}", 'dbg')
            with open(config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}

            controlled_data = self._load_controlled_access_config(board_name)
            if controlled_data:
                self.logger(f"Merging controlled-access configuration for board: {board_name}", 'dbg')
                data = self._merge_controlled_access(data, controlled_data)
        else:
            # No public config: a fully controlled-access board may be defined solely in
            # bifrost/boards/<board>.yml (repo_list, example_list, and any overlay fields all live
            # there). Fall back to that file as the complete base configuration.
            self.logger(f"No public board config at {config_path}; trying controlled-access config", 'dbg')
            data = self._load_controlled_access_config(board_name)
            if not data:
                self.logger(f"Board configuration file not found: {config_path}", 'err')
                raise FileNotFoundError(self._format_board_not_found_error(board_name))
            self.logger(f"Using controlled-access config as the base for board: {board_name}", 'inf')

        config = self._create_board_config(board_name, data)
        self.logger(f"Successfully loaded board config: {board_name} (repos: {len(config.repo_list)}, examples: {len(config.example_list)})", 'inf')
        
        return config
    
    def load_device_config(self, device_name: str) -> BoardConfig:
        """Load merged configuration for a device (multiple boards)."""
        self.logger(f"Loading device configuration: {device_name}", 'dbg')
        
        board_names = self.device_mapper.get_boards_for_device(device_name)
        self.logger(f"Device '{device_name}' maps to boards: {board_names}", 'inf')
        
        # Load and merge all board configurations
        merged_repos = set()
        merged_examples = set()
        merged_optional_repos = set()
        merged_optional_examples = set()
        merged_dependencies = set()
        merged_devices_int_paths = set()
        merged_examples_int_paths = set()
        merged_devices_filter = set()
        merged_drivers_filter = set()

        successful_boards = []
        failed_boards = []

        for board_name in board_names:
            try:
                self.logger(f"Loading configuration for board: {board_name}", 'dbg')
                board_config = self.load_board_config(board_name)
                merged_repos.update(board_config.repo_list)
                merged_examples.update(board_config.example_list)
                merged_optional_repos.update(board_config.optional_repos)
                merged_optional_examples.update(board_config.optional_examples)
                merged_dependencies.update(board_config.board_dependencies)
                merged_devices_int_paths.update(board_config.devices_int_paths)
                merged_examples_int_paths.update(board_config.examples_int_paths)
                merged_devices_filter.update(board_config.devices_filter)
                merged_drivers_filter.update(board_config.drivers_filter)
                successful_boards.append(board_name)
                self.logger(f"Successfully merged config from board: {board_name} (deps: {board_config.board_dependencies})", 'dbg')
            except Exception as e:
                failed_boards.append(board_name)
                self.logger(f"Failed to load config for board {board_name}: {e}", 'wrn')
                continue  # Skip failed board configs

        if failed_boards:
            self.logger(f"Failed to load {len(failed_boards)} board configs: {failed_boards}", 'wrn')

        if not successful_boards:
            error_msg = f"No valid board configurations found for device '{device_name}'"
            self.logger(error_msg, 'err')
            raise ValueError(error_msg)

        # Create merged configuration
        config = BoardConfig(
            name=f"{device_name}_device",
            repo_list=sorted(list(merged_repos)),
            example_list=sorted(list(merged_examples)),
            optional_repos=sorted(list(merged_optional_repos)),
            optional_examples=sorted(list(merged_optional_examples)),
            board_dependencies=sorted(list(merged_dependencies)),
            devices_int_paths=sorted(list(merged_devices_int_paths)),
            examples_int_paths=sorted(list(merged_examples_int_paths)),
            devices_filter=sorted(list(merged_devices_filter)),
            drivers_filter=sorted(list(merged_drivers_filter)),
        )
        config.source_boards = board_names

        self.logger(f"Successfully created merged device config: {device_name} "
                    f"(repos: {len(config.repo_list)}, examples: {len(config.example_list)}, "
                    f"optional_repos: {len(config.optional_repos)}, optional_examples: {len(config.optional_examples)}, "
                    f"dependencies: {len(config.board_dependencies)}, "
                    f"devices_int_paths: {len(config.devices_int_paths)}, "
                    f"examples_int_paths: {len(config.examples_int_paths)}, "
                    f"devices_filter: {len(config.devices_filter)}, "
                    f"drivers_filter: {len(config.drivers_filter)})", 'inf')
        
        return config
    
    def load_custom_config(self, config_file: str) -> BoardConfig:
        """Load configuration from a custom YAML file."""
        self.logger(f"Loading custom configuration from: {config_file}", 'dbg')
        
        if not os.path.isfile(config_file):
            self.logger(f"Custom configuration file not found: {config_file}", 'err')
            error_msg = self._format_custom_config_not_found_error(config_file)
            raise FileNotFoundError(error_msg)
        
        with open(config_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        
        board_name = data.get('board_name', 'custom')
        config = self._create_board_config(board_name, data)
        
        self.logger(f"Successfully loaded custom config: {board_name} "
                   f"(repos: {len(config.repo_list)}, examples: {len(config.example_list)})", 'inf')
        
        return config
    
    def get_filtering_boards(self, config: BoardConfig) -> List[str]:
        """Get board names for filtering purposes."""
        if hasattr(config, 'source_boards') and config.source_boards:
            # For device configs, collect dependencies from all source boards
            all_boards = set(config.source_boards)
            
            # Load each source board config to get its dependencies
            for board_name in config.source_boards:
                try:
                    board_config = self.load_board_config(board_name)
                    all_boards.update(board_config.board_dependencies)
                    self.logger(f"Added dependencies for board {board_name}: {board_config.board_dependencies}", 'dbg')
                except Exception as e:
                    self.logger(f"Failed to load dependencies for board {board_name}: {e}", 'wrn')
                    continue
            
            boards = sorted(list(all_boards))
            self.logger(f"Using source boards and dependencies for filtering: {boards}", 'dbg')
            return boards
        
        # For single board configs, include the board and its dependencies
        boards = config.get_all_filtering_boards()
        self.logger(f"Using config board and dependencies for filtering: {boards}", 'dbg')
        return boards
    
    def _format_board_not_found_error(self, board_name: str) -> str:
        """Format a user-friendly error message when board is not found."""
        available_boards = self.device_mapper.get_available_boards()
        
        if not available_boards:
            return f"Board configuration '{board_name}' not found. No board configurations available."
        
        # Find similar board names
        similar_boards = []
        board_lower = board_name.lower()
        for board in available_boards:
            if (board_lower in board.lower() or 
                board.lower() in board_lower or
                any(part in board.lower() for part in board_lower.split('_')) or
                any(part in board_lower for part in board.lower().split('_'))):
                similar_boards.append(board)
        
        error_lines = [f"Board configuration '{board_name}' not found."]
        
        if similar_boards:
            error_lines.append(f"\nSimilar boards found:")
            for board in similar_boards[:5]:  # Limit to 5 most similar
                error_lines.append(f"  • {board}")
        
        error_lines.append(f"\nAll available boards ({len(available_boards)} total):")
        
        # Group boards by family for better readability
        board_groups = self.device_mapper._group_boards_by_family(available_boards)
        
        for family, boards in board_groups.items():
            if family and family != 'Other':
                error_lines.append(f"\n  {family}:")
                for board in boards:
                    error_lines.append(f"    • {board}")
        
        # Show 'Other' category last
        if 'Other' in board_groups:
            error_lines.append(f"\n  Other boards:")
            for board in board_groups['Other']:
                error_lines.append(f"    • {board}")
        
        return '\n'.join(error_lines)
    
    def _format_custom_config_not_found_error(self, config_file: str) -> str:
        """Format error message for missing custom configuration file."""
        error_lines = [
            f"Custom configuration file not found: {config_file}",
            "",
            "Custom configuration files should be YAML files with the following structure:",
            "",
            "  board_name: my_custom_board",
            "  repo_list:",
            "    - core",
            "    - mcuxsdk-examples",
            "  example_list:",
            "    - demo_apps/hello_world",
            "    - demo_apps/led_blinky",
            "  optional_repos:",
            "    - mcuxsdk-middleware-usb",
            "  optional_examples:",
            "    - middleware_examples/usb",
            "",
            f"Please ensure the file exists and is accessible: {os.path.abspath(config_file)}"
        ]
        
        return '\n'.join(error_lines)
    
    def _load_controlled_access_config(self, board_name: str) -> Optional[Dict]:
        """Load controlled-access configuration if available."""
        controlled_path = os.path.join(
            self.workspace_root, 'bifrost', 'boards', f'{board_name}.yml'
        )
        
        self.logger(f"Checking for controlled-access config: {controlled_path}", 'dbg')
        
        if not os.path.isfile(controlled_path):
            self.logger(f"No controlled-access config found for board: {board_name}", 'dbg')
            return None
        
        try:
            with open(controlled_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            self.logger(f"Loaded controlled-access config for board: {board_name}", 'dbg')
            return data
        except (yaml.YAMLError, IOError) as e:
            self.logger(f"Error loading controlled-access config for {board_name}: {e}", 'wrn')
            return None
    
    def _merge_controlled_access(self, base_data: Dict, controlled_data: Dict) -> Dict:
        """Merge controlled-access data with base configuration."""
        merged = base_data.copy()

        list_fields = [
            ('optional_repos', 'optional repos'),
            ('optional_examples', 'optional examples'),
            ('devices_int_paths', 'devices_int paths'),
            ('examples_int_paths', 'examples_int paths'),
            ('devices_filter', 'devices filter'),
            ('drivers_filter', 'drivers filter'),
        ]

        for field, label in list_fields:
            base_values = merged.get(field, [])
            controlled_values = controlled_data.get(field, [])
            if controlled_values:
                base_values.extend(controlled_values)
                merged[field] = list(dict.fromkeys(base_values))
                self.logger(f"Merged {len(controlled_values)} controlled-access {label}", 'dbg')

        return merged
    
    def _create_board_config(self, name: str, data: Dict) -> BoardConfig:
        """Create BoardConfig from dictionary data."""
        config = BoardConfig(
            name=name,
            repo_list=data.get('repo_list', []),
            example_list=data.get('example_list', []),
            optional_repos=data.get('optional_repos', []),
            optional_examples=data.get('optional_examples', []),
            board_dependencies=data.get('board_dependencies', []),
            devices_int_paths=data.get('devices_int_paths', []),
            examples_int_paths=data.get('examples_int_paths', []),
            devices_filter=data.get('devices_filter', []),
            drivers_filter=data.get('drivers_filter', []),
        )

        self.logger(f"Created BoardConfig for {name}: "
                    f"repos={len(config.repo_list)}, examples={len(config.example_list)}, "
                    f"optional_repos={len(config.optional_repos)}, optional_examples={len(config.optional_examples)}, "
                    f"dependencies={len(config.board_dependencies)}, "
                    f"devices_int_paths={len(config.devices_int_paths)}, "
                    f"examples_int_paths={len(config.examples_int_paths)}, "
                    f"devices_filter={len(config.devices_filter)}, "
                    f"drivers_filter={len(config.drivers_filter)}", 'dbg')

        return config
