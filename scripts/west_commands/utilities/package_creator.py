# -*- coding: utf-8 -*-
# Copyright 2025 NXP
# SPDX-License-Identifier: BSD-3-Clause

import os
import zipfile
import shutil
import tempfile
import subprocess
import time
import yaml
from typing import List, Optional, Set
from dataclasses import dataclass


@dataclass
class PackageOptions:
    """Package creation options."""
    include_git: bool = False
    generate_docs: bool = False
    board_filter: List[str] = None
    
    def __post_init__(self):
        if self.board_filter is None:
            self.board_filter = []


class PackageCreator:
    """Independent utility for creating filtered SDK packages."""
    
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
    
    def create_package(self, output_path: str, projects: List, repo_list: List[str], 
                      example_list: List[str], options: PackageOptions) -> None:
        """Create a filtered package with the specified options."""
        temp_dir = tempfile.mkdtemp(prefix='sdk_package_')
        
        try:
            # Copy repositories with filtering
            self._copy_repositories(temp_dir, projects, options)
            
            # Handle documentation
            if options.generate_docs:
                self._generate_documentation(temp_dir, options.board_filter)
            else:
                self._remove_docs_directory(temp_dir)
            
            # Filter examples
            self._filter_examples(temp_dir, repo_list, example_list, options.board_filter)
            
            # Create final archive
            self._create_archive(temp_dir, output_path)
            
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
    
    def _copy_repositories(self, temp_dir: str, projects: List, options: PackageOptions) -> None:
        """Copy repositories with filtering."""
        # Get boards to exclude from filtering
        excluded_boards = self._get_excluded_boards(options.board_filter)
        
        # Copy workspace metadata
        for meta_dir in ['.west', 'manifests']:
            src = os.path.join(self.workspace_root, meta_dir)
            if os.path.exists(src):
                dst = os.path.join(temp_dir, meta_dir)
                self._copy_with_filtering(src, dst, excluded_boards, options.include_git)
        
        # Copy project repositories
        for project in projects:
            self._copy_project(temp_dir, project, excluded_boards, options.include_git)
        
        # Copy mcuxsdk root files
        self._copy_mcuxsdk_root_files(temp_dir)
    
    def _copy_project(self, temp_dir: str, project, excluded_boards: Set[str], 
                     include_git: bool) -> None:
        """Copy a single project."""
        if project.name == "core":
            # Handle core project subdirectories
            for subdir in ["drivers", "arch", "cmake", "share", "scripts", "devices"]:
                src = os.path.join(self.workspace_root, "mcuxsdk", subdir)
                if os.path.exists(src):
                    dst = os.path.join(temp_dir, "mcuxsdk", subdir)
                    self._copy_with_filtering(src, dst, set(), include_git)
        else:
            src = os.path.join(self.workspace_root, project.path)
            if os.path.exists(src):
                dst = os.path.join(temp_dir, project.path)
                # Apply board filtering only to examples
                boards_to_exclude = excluded_boards if project.path == "mcuxsdk/examples" else set()
                self._copy_with_filtering(src, dst, boards_to_exclude, include_git)
    
    def _copy_with_filtering(self, src_dir: str, dest_dir: str, excluded_boards: Set[str], 
                           include_git: bool, rel_path: str = "") -> None:
        """Recursively copy with filtering."""
        if not os.path.exists(src_dir):
            return
        
        os.makedirs(dest_dir, exist_ok=True)
        
        try:
            items = os.listdir(src_dir)
        except (OSError, PermissionError):
            return
        
        for item in items:
            src_item = os.path.join(src_dir, item)
            dest_item = os.path.join(dest_dir, item)
            item_rel_path = os.path.join(rel_path, item) if rel_path else item
            
            # Apply filtering rules
            if self._should_exclude_path(item_rel_path, excluded_boards, include_git):
                continue
            
            if os.path.islink(src_item):
                self._copy_symlink(src_item, dest_item)
            elif os.path.isdir(src_item):
                self._copy_with_filtering(src_item, dest_item, excluded_boards, include_git, item_rel_path)
            elif os.path.isfile(src_item):
                os.makedirs(os.path.dirname(dest_item), exist_ok=True)
                shutil.copy2(src_item, dest_item)
    
    def _should_exclude_path(self, rel_path: str, excluded_boards: Set[str], include_git: bool) -> bool:
        """Check if a path should be excluded."""
        path_components = rel_path.split(os.sep)
        
        # Check for excluded board names
        for comp in path_components:
            if comp in excluded_boards:
                return True
        
        # Check for .git directories/files
        if not include_git and any(".git" in comp for comp in path_components):
            return True
        
        return False
    
    def _copy_symlink(self, src_path: str, dest_path: str) -> None:
        """Copy a symbolic link."""
        try:
            link_target = os.readlink(src_path)
            if os.path.exists(dest_path) or os.path.islink(dest_path):
                if os.path.isdir(dest_path) and not os.path.islink(dest_path):
                    shutil.rmtree(dest_path)
                else:
                    os.remove(dest_path)
            
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            os.symlink(link_target, dest_path)
        except (OSError, IOError):
            # Fallback to copying content
            try:
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dest_path, symlinks=False)
                else:
                    shutil.copy2(src_path, dest_path)
            except Exception:
                pass
    
    def _get_excluded_boards(self, target_boards: List[str]) -> Set[str]:
        """Get board names to exclude from filtering."""
        if not target_boards:
            return set()
        
        target_set = set(target_boards)
        excluded = set()
        
        boards_dir = os.path.join(self.workspace_root, "mcuxsdk", "examples", "_boards")
        if os.path.isdir(boards_dir):
            try:
                for name in os.listdir(boards_dir):
                    board_path = os.path.join(boards_dir, name)
                    if os.path.isdir(board_path) and name not in target_set:
                        excluded.add(name)
            except (OSError, PermissionError):
                pass
        
        return excluded
    
    def _copy_mcuxsdk_root_files(self, temp_dir: str) -> None:
        """Copy files directly under mcuxsdk/ folder."""
        mcuxsdk_dir = os.path.join(self.workspace_root, "mcuxsdk")
        if not os.path.exists(mcuxsdk_dir):
            return
        
        dest_mcuxsdk_dir = os.path.join(temp_dir, "mcuxsdk")
        os.makedirs(dest_mcuxsdk_dir, exist_ok=True)
        
        for item in os.listdir(mcuxsdk_dir):
            src_path = os.path.join(mcuxsdk_dir, item)
            if os.path.isfile(src_path) or os.path.islink(src_path):
                dest_path = os.path.join(dest_mcuxsdk_dir, item)
                if os.path.islink(src_path):
                    self._copy_symlink(src_path, dest_path)
                else:
                    shutil.copy2(src_path, dest_path)
    
    def _generate_documentation(self, temp_dir: str, board_list: List[str]) -> None:
        """Generate documentation for specified boards."""
        if not board_list:
            return
        
        docs_dir = os.path.join(temp_dir, "mcuxsdk", "docs")
        os.makedirs(docs_dir, exist_ok=True)
        
        for board_name in board_list:
            try:
                cmd = ['west', 'doc', 'pdf', '--board', board_name]
                subprocess.run(cmd, cwd=temp_dir, check=True, capture_output=True, text=True)
                
                # Move generated PDF to final location
                pdf_src = os.path.join(temp_dir, "mcuxsdk", "docs", "_build", "latex", f"mcuxsdk-{board_name}.pdf")
                if os.path.exists(pdf_src):
                    pdf_dest = os.path.join(docs_dir, f"mcuxsdk-{board_name}.pdf")
                    shutil.move(pdf_src, pdf_dest)
            except subprocess.CalledProcessError:
                continue
        
        # Clean up build directory
        build_dir = os.path.join(temp_dir, "mcuxsdk", "docs", "_build")
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir)
    
    def _remove_docs_directory(self, temp_dir: str) -> None:
        """Remove docs directory."""
        docs_dir = os.path.join(temp_dir, "mcuxsdk", "docs")
        if os.path.exists(docs_dir):
            shutil.rmtree(docs_dir)
    
    def _filter_examples(self, temp_dir: str, repo_list: List[str], 
                        example_list: List[str], board_filter: List[str]) -> None:
        """Filter examples based on the provided lists."""
        examples_root = os.path.join(temp_dir, "mcuxsdk", "examples")
        if not os.path.exists(examples_root):
            return
        
        # Build target paths
        target_paths = {"_boards", "_common"}
        target_paths.update(example.replace('\\', '/').strip('/') for example in example_list)
        
        # Build parent paths
        parent_paths = set()
        for target_path in target_paths:
            path_parts = target_path.split('/')
            for i in range(1, len(path_parts)):
                parent_path = '/'.join(path_parts[:i])
                parent_paths.add(parent_path)
        
        # Remove unwanted directories
        self._remove_unwanted_examples(examples_root, "", target_paths, parent_paths)
        
        # Filter example.yml files
        self._filter_example_yml_files(temp_dir, board_filter)
    
    def _remove_unwanted_examples(self, current_dir: str, relative_path: str, 
                                 target_paths: Set[str], parent_paths: Set[str]) -> None:
        """Recursively remove unwanted example directories."""
        if not os.path.exists(current_dir):
            return
        
        try:
            items = os.listdir(current_dir)
        except (OSError, PermissionError):
            return
        
        for item in items:
            item_path = os.path.join(current_dir, item)
            item_relative = os.path.join(relative_path, item).replace('\\', '/') if relative_path else item
            
            if os.path.isdir(item_path):
                if item_relative in target_paths:
                    continue  # Keep target path
                elif item_relative in parent_paths:
                    # Keep parent but recurse
                    self._remove_unwanted_examples(item_path, item_relative, target_paths, parent_paths)
                else:
                    # Remove unwanted directory
                    try:
                        shutil.rmtree(item_path)
                    except (OSError, IOError):
                        pass
    
    def _filter_example_yml_files(self, temp_dir: str, target_boards: List[str]) -> None:
        """Filter example.yml files to include only target boards."""
        if not target_boards:
            return
        
        examples_dir = os.path.join(temp_dir, "mcuxsdk", "examples")
        if not os.path.exists(examples_dir):
            return
        
        # Find all example.yml files
        for root, dirs, files in os.walk(examples_dir):
            for file in files:
                if file == 'example.yml':
                    yml_path = os.path.join(root, file)
                    self._filter_single_example_yml(yml_path, target_boards)
    
    def _filter_single_example_yml(self, yml_file: str, target_boards: List[str]) -> None:
        """Filter a single example.yml file."""
        try:
            with open(yml_file, 'r', encoding='utf-8') as f:
                yaml_data = yaml.safe_load(f)
            
            if not yaml_data or not isinstance(yaml_data, dict):
                return
            
            modified = False
            
            for example_name, example_config in yaml_data.items():
                if not isinstance(example_config, dict) or 'boards' not in example_config:
                    continue
                
                boards_config = example_config['boards']
                if not isinstance(boards_config, dict):
                    continue
                
                # Filter boards
                filtered_boards = {
                    board_key: board_value 
                    for board_key, board_value in boards_config.items()
                    if any(target_board in board_key for target_board in target_boards)
                }
                
                if len(filtered_boards) != len(boards_config):
                    example_config['boards'] = filtered_boards
                    modified = True
            
            # Write back if modified
            if modified:
                with open(yml_file, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(yaml_data, f, default_flow_style=False, sort_keys=False)
                    
        except (yaml.YAMLError, IOError):
            pass
    
    def _create_archive(self, temp_dir: str, output_path: str) -> None:
        """Create the final zip archive."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
            self._add_directory_to_zip(zipf, temp_dir, "")
    
    def _add_directory_to_zip(self, zipf: zipfile.ZipFile, src_dir: str, arc_prefix: str) -> None:
        """Add directory contents to zip file."""
        try:
            items = os.listdir(src_dir)
        except (OSError, PermissionError):
            return
        
        for item in items:
            src_path = os.path.join(src_dir, item)
            arc_path = os.path.join(arc_prefix, item) if arc_prefix else item
            arc_path = arc_path.replace(os.sep, '/')
            
            if os.path.islink(src_path):
                self._add_symlink_to_zip(zipf, src_path, arc_path)
            elif os.path.isdir(src_path):
                if not arc_path.endswith('/'):
                    arc_path += '/'
                zipf.writestr(arc_path, '')
                self._add_directory_to_zip(zipf, src_path, arc_path.rstrip('/'))
            elif os.path.isfile(src_path):
                zipf.write(src_path, arc_path)
    
    def _add_symlink_to_zip(self, zipf: zipfile.ZipFile, symlink_path: str, arc_path: str) -> None:
        """Add symlink to zip with proper attributes."""
        try:
            link_target = os.readlink(symlink_path)
            zip_info = zipfile.ZipInfo(arc_path)
            zip_info.external_attr = (0o120000 | 0o755) << 16
            zip_info.compress_type = zipfile.ZIP_STORED
            
            target_bytes = link_target.encode('utf-8')
            zip_info.file_size = len(target_bytes)
            zip_info.compress_size = len(target_bytes)
            zip_info.create_system = 3
            zip_info.extract_version = 20
            
            zipf.writestr(zip_info, target_bytes)
        except (OSError, IOError):
            # Fallback to regular file
            if os.path.isfile(symlink_path):
                zipf.write(symlink_path, arc_path)
