# Copyright 2025 NXP
# SPDX-License-Identifier: BSD-3-Clause

"""
Configuration loading and management utility.

This module provides functionality to load and manage board, device, and custom
configurations including controlled-access configurations and merging.
"""

import os
import yaml
from typing import Dict, List, Any, Optional
from .device_board_mapper import DeviceBoardMapper


class BoardConfig:
    """Represents a board configuration."""
    
    def __init__(self, name: str, repo_list: List[str], example_list: List[str], 
                 optional_repos: List[str] = None, optional_examples: List[str] = None):
        self.name = name
        self.repo_list = repo_list or []
        self.example_list = example_list or []
        self.optional_repos = optional_repos or []
        self.optional_examples = optional_examples or []
        self.source_boards = []  # For device configs
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            'board_name': self.name,
            'repo_list': self.repo_list,
            'example_list': self.example_list,
            'optional_repos': self.optional_repos,
            'optional_examples': self.optional_examples
        }
        if self.source_boards:
            result['source_boards'] = self.source_boards
        return result
    
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
    
    def __init__(self, workspace_root: str, manifest_dir: str):
        self.workspace_root = workspace_root
        self.manifest_dir = manifest_dir
        self.device_mapper = DeviceBoardMapper(workspace_root, manifest_dir)
    
    def load_board_config(self, board_name: str) -> BoardConfig:
        """Load configuration for a specific board."""
        config_path = os.path.join(self.manifest_dir, 'boards', f'{board_name}.yml')
        
        if not os.path.isfile(config_path):
            error_msg = self._format_board_not_found_error(board_name)
            raise FileNotFoundError(error_msg)
        
        # Load main configuration
        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        
        # Load controlled-access configuration if available
        controlled_data = self._load_controlled_access_config(board_name)
        if controlled_data:
            data = self._merge_controlled_access(data, controlled_data)
        
        return self._create_board_config(board_name, data)
    
    def load_device_config(self, device_name: str) -> BoardConfig:
        """Load merged configuration for a device (multiple boards)."""
        board_names = self.device_mapper.get_boards_for_device(device_name)
        
        # Load and merge all board configurations
        merged_repos = set()
        merged_examples = set()
        merged_optional_repos = set()
        merged_optional_examples = set()
        
        for board_name in board_names:
            try:
                board_config = self.load_board_config(board_name)
                merged_repos.update(board_config.repo_list)
                merged_examples.update(board_config.example_list)
                merged_optional_repos.update(board_config.optional_repos)
                merged_optional_examples.update(board_config.optional_examples)
            except Exception:
                continue  # Skip failed board configs
        
        # Create merged configuration
        config = BoardConfig(
            name=f"{device_name}_device",
            repo_list=sorted(list(merged_repos)),
            example_list=sorted(list(merged_examples)),
            optional_repos=sorted(list(merged_optional_repos)),
            optional_examples=sorted(list(merged_optional_examples))
        )
        config.source_boards = board_names
        
        return config
    
    def load_custom_config(self, config_file: str) -> BoardConfig:
        """Load configuration from a custom YAML file."""
        if not os.path.isfile(config_file):
            error_msg = self._format_custom_config_not_found_error(config_file)
            raise FileNotFoundError(error_msg)
        
        with open(config_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        
        board_name = data.get('board_name', 'custom')
        return self._create_board_config(board_name, data)
    
    def get_filtering_boards(self, config: BoardConfig) -> List[str]:
        """Get board names for filtering purposes."""
        if hasattr(config, 'source_boards') and config.source_boards:
            return config.source_boards
        return [config.name]
    
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
        
        if not os.path.isfile(controlled_path):
            return None
        
        try:
            with open(controlled_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except (yaml.YAMLError, IOError):
            return None
    
    def _merge_controlled_access(self, base_data: Dict, controlled_data: Dict) -> Dict:
        """Merge controlled-access data with base configuration."""
        merged = base_data.copy()
        
        # Merge optional repositories
        base_optional_repos = merged.get('optional_repos', [])
        controlled_optional_repos = controlled_data.get('optional_repos', [])
        if controlled_optional_repos:
            base_optional_repos.extend(controlled_optional_repos)
            merged['optional_repos'] = list(dict.fromkeys(base_optional_repos))
        
        # Merge optional examples
        base_optional_examples = merged.get('optional_examples', [])
        controlled_optional_examples = controlled_data.get('optional_examples', [])
        if controlled_optional_examples:
            base_optional_examples.extend(controlled_optional_examples)
            merged['optional_examples'] = list(dict.fromkeys(base_optional_examples))
        
        return merged
    
    def _create_board_config(self, name: str, data: Dict) -> BoardConfig:
        """Create BoardConfig from dictionary data."""
        return BoardConfig(
            name=name,
            repo_list=data.get('repo_list', []),
            example_list=data.get('example_list', []),
            optional_repos=data.get('optional_repos', []),
            optional_examples=data.get('optional_examples', [])
        )
