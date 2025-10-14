# Copyright 2025 NXP
# SPDX-License-Identifier: BSD-3-Clause

"""
Device to board mapping utility.

This module provides functionality to map device names to their supported boards
by parsing tool data and validating board configurations.
"""

import os
import yaml
import subprocess
from typing import List, Dict, Optional


class DeviceBoardMapper:
    """Independent utility for mapping devices to boards using tool data."""
    
    def __init__(self, workspace_root: str, manifest_dir: str):
        self.workspace_root = workspace_root
        self.manifest_dir = manifest_dir
        self._device_cache = None
    
    def get_boards_for_device(self, device_name: str) -> List[str]:
        """Get list of validated board names that support the specified device."""
        device_map = self._get_device_to_board_mapping()
        
        if not device_map:
            raise ValueError("No device-to-board mapping available. Tool data may be missing.")
        
        # Search for matching device (case-insensitive)
        device_name_lower = device_name.lower()
        matched_boards = []
        matched_device_name = None
        
        # Try exact match first
        for device, boards in device_map.items():
            if device.lower() == device_name_lower:
                matched_boards = boards
                matched_device_name = device
                break
        
        # If no exact match, try partial match
        if not matched_boards:
            for device, boards in device_map.items():
                if device_name_lower in device.lower() or device.lower() in device_name_lower:
                    matched_boards = boards
                    matched_device_name = device
                    break
        
        if not matched_boards:
            error_msg = self._format_device_not_found_error(device_name, device_map)
            raise ValueError(error_msg)
        
        # Validate board configurations exist
        validated_boards = self._validate_board_configs(matched_boards)
        if not validated_boards:
            error_msg = self._format_no_valid_boards_error(device_name, matched_device_name, matched_boards)
            raise ValueError(error_msg)
        
        return validated_boards
    
    def get_available_devices(self) -> Dict[str, List[str]]:
        """Get all available devices and their supported boards."""
        return self._get_device_to_board_mapping()
    
    def get_available_boards(self) -> List[str]:
        """Get list of all available board configuration files."""
        boards_dir = os.path.join(self.manifest_dir, 'boards')
        
        if not os.path.isdir(boards_dir):
            return []
        
        boards = []
        try:
            for filename in os.listdir(boards_dir):
                if filename.endswith(('.yml', '.yaml')):
                    board_name = os.path.splitext(filename)[0]
                    boards.append(board_name)
        except (OSError, PermissionError):
            pass
        
        return sorted(boards)
    
    def _format_device_not_found_error(self, device_name: str, device_map: Dict[str, List[str]]) -> str:
        """Format a user-friendly error message when device is not found."""
        if not device_map:
            return f"Device '{device_name}' not found. No devices available in tool data."
        
        # Group devices by similarity for better readability
        all_devices = sorted(device_map.keys())
        
        # Try to find similar devices (partial matches)
        similar_devices = []
        device_lower = device_name.lower()
        for device in all_devices:
            if (device_lower in device.lower() or 
                device.lower() in device_lower or
                any(part in device.lower() for part in device_lower.split()) or
                any(part in device_lower for part in device.lower().split())):
                similar_devices.append(device)
        
        error_lines = [f"Device '{device_name}' not found."]
        
        if similar_devices:
            error_lines.append("\nSimilar devices found:")
            for device in similar_devices[:5]:  # Limit to 5 most similar
                boards = device_map[device]
                boards_str = ', '.join(boards[:3])  # Show first 3 boards
                if len(boards) > 3:
                    boards_str += f" (and {len(boards) - 3} more)"
                error_lines.append(f"  • {device}")
                error_lines.append(f"    Boards: {boards_str}")
        
        error_lines.append(f"\nAll available devices ({len(all_devices)} total):")
        
        # Group devices by common prefixes for better organization
        device_groups = self._group_devices_by_family(all_devices)
        
        for family, devices in device_groups.items():
            if family:
                error_lines.append(f"\n  {family} family:")
                for device in devices:
                    error_lines.append(f"    • {device}")
            else:
                error_lines.append(f"\n  Other devices:")
                for device in devices:
                    error_lines.append(f"    • {device}")
        
        return '\n'.join(error_lines)
    
    def _format_no_valid_boards_error(self, device_name: str, matched_device: str, 
                                     recommended_boards: List[str]) -> str:
        """Format error when no valid board configurations are found."""
        error_lines = [
            f"Device '{device_name}' found as '{matched_device}', but no valid board configurations exist.",
            f"\nRecommended boards for this device: {', '.join(recommended_boards)}",
            "\nThese board configuration files are missing from the manifest/boards/ directory.",
            "This may indicate:",
            "  • The board configurations haven't been created yet",
            "  • The tool data is newer than the available board configurations",
            "  • There's a mismatch between tool data and manifest configuration"
        ]
        
        # Show available board configurations
        available_boards = self.get_available_boards()
        if available_boards:
            error_lines.append(f"\nAvailable board configurations ({len(available_boards)} total):")
            # Group boards for better readability
            board_groups = self._group_boards_by_family(available_boards)
            for family, boards in board_groups.items():
                if family:
                    error_lines.append(f"\n  {family} boards:")
                    for board in boards:
                        error_lines.append(f"    • {board}")
                else:
                    error_lines.append(f"\n  Other boards:")
                    for board in boards:
                        error_lines.append(f"    • {board}")
        
        return '\n'.join(error_lines)
    
    def _group_devices_by_family(self, devices: List[str]) -> Dict[str, List[str]]:
        """Group devices by family for better organization."""
        groups = {}
        
        # Common device family patterns
        family_patterns = {
            'MCXW': 'Wireless MCX Series',
            'KW': 'Wireless Kinetis KW',
            'RW': 'Wireless RW',
            'MCX': 'MCX Series',
            'LPC': 'LPC',
            'MIMXRT': 'i.MX RT',
            'MKE': 'Kinetis KE',
            'MC56F': 'Digital Signal Controller',
            'MIMX': 'i.MX',
            'K': 'Kinetis K/L/M Series',
        }
        
        for device in devices:
            device_upper = device.upper()
            family_found = False
            
            for pattern, family_name in family_patterns.items():
                if device_upper.startswith(pattern):
                    if family_name not in groups:
                        groups[family_name] = []
                    groups[family_name].append(device)
                    family_found = True
                    break
            
            if not family_found:
                if 'Other' not in groups:
                    groups['Other'] = []
                groups['Other'].append(device)
        
        # Sort devices within each group
        for family in groups:
            groups[family].sort()
        
        return groups
    
    def _group_boards_by_family(self, boards: List[str]) -> Dict[str, List[str]]:
        """Group boards by family for better organization."""
        groups = {}
        
        # Common board family patterns
        family_patterns = {
            'frdmmcx': 'FRDM-MCX',
            'mcx': 'MCX EVK',
            'frdmk': 'FRDM-Kinetis',
            'frdmlpc': 'FRDM-LPC',
            'lpc': 'LPC',
            'evk': 'EVK',
            'mimxrt': 'i.MX RT',
        }
        
        for board in boards:
            board_lower = board.lower()
            family_found = False
            
            for pattern, family_name in family_patterns.items():
                if board_lower.startswith(pattern):
                    if family_name not in groups:
                        groups[family_name] = []
                    groups[family_name].append(board)
                    family_found = True
                    break
            
            if not family_found:
                if 'Other' not in groups:
                    groups['Other'] = []
                groups['Other'].append(board)
        
        # Sort boards within each group
        for family in groups:
            groups[family].sort()
        
        return groups
    
    def _get_device_to_board_mapping(self) -> Dict[str, List[str]]:
        """Build and cache device-to-board mapping."""
        if self._device_cache is not None:
            return self._device_cache
        
        self._device_cache = self._build_device_mapping()
        return self._device_cache
    
    def _build_device_mapping(self) -> Dict[str, List[str]]:
        """Build device-to-board mapping from tool data."""
        tool_data_dir = os.path.join(
            self.workspace_root, 
            "mcuxsdk", "tool_data", "recommended_boards"
        )
        
        # Ensure tool data is available
        if not os.path.isdir(tool_data_dir):
            self._download_tool_data()
        
        device_map = {}
        
        try:
            yaml_files = [
                os.path.join(tool_data_dir, f) 
                for f in os.listdir(tool_data_dir) 
                if f.endswith(('.yml', '.yaml'))
            ]
            
            for yaml_file in yaml_files:
                device_data = self._parse_device_yaml(yaml_file)
                device_map.update(device_data)
                
        except (OSError, PermissionError):
            pass
        
        return device_map
    
    def _parse_device_yaml(self, yaml_file: str) -> Dict[str, List[str]]:
        """Parse a single device YAML file."""
        device_data = {}
        
        try:
            with open(yaml_file, 'r', encoding='utf-8') as f:
                yaml_content = yaml.safe_load(f) or {}
            
            recommended_boards_list = yaml_content.get('recommended_boards', [])
            
            for device_entry in recommended_boards_list:
                if not isinstance(device_entry, dict):
                    continue
                
                device_name = device_entry.get('full_name')
                if not device_name:
                    continue
                
                device_boards = device_entry.get('recommended_boards', [])
                board_list = [
                    board_entry['id'] 
                    for board_entry in device_boards 
                    if isinstance(board_entry, dict) and board_entry.get('id')
                ]
                
                if board_list:
                    if device_name in device_data:
                        # Merge and deduplicate
                        existing = set(device_data[device_name])
                        new_boards = set(board_list)
                        device_data[device_name] = sorted(list(existing.union(new_boards)))
                    else:
                        device_data[device_name] = sorted(board_list)
                        
        except (yaml.YAMLError, IOError):
            pass
        
        return device_data
    
    def _validate_board_configs(self, board_list: List[str]) -> List[str]:
        """Validate that board configuration files exist."""
        boards_dir = os.path.join(self.manifest_dir, 'boards')
        validated = []
        
        for board_id in board_list:
            config_file = os.path.join(boards_dir, f'{board_id}.yml')
            if os.path.isfile(config_file):
                validated.append(board_id)
        
        return validated
    
    def _download_tool_data(self):
        """Download tool data if not available."""
        try:
            cmd = ['west', 'update', 'mcuxsdk-tool-data', '-n', '-o=--depth=1']
            subprocess.run(cmd, cwd=self.workspace_root, check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass  # Fail silently, will be handled by caller
