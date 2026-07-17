# -*- coding: utf-8 -*-
# Copyright 2025 NXP
# SPDX-License-Identifier: BSD-3-Clause

import os
import re
import zipfile
import shutil
import tempfile
import subprocess
import time
import yaml
from typing import List, Optional, Set, Callable
from dataclasses import dataclass
from west.manifest import Manifest

@dataclass
class PackageOptions:
    """Package creation options."""
    include_git: bool = False
    generate_docs: bool = False
    board_filter: List[str] = None
    # Migration: sub-paths from the live mcuxsdk/devices_int/ repo, merged into the package's
    # public mcuxsdk/devices/ tree (package staging only; live workspace untouched).
    devices_int_paths: List[str] = None
    # Migration: sub-paths from the live mcuxsdk/examples_int/ repo, merged into the package's
    # public mcuxsdk/examples/ tree (package staging only). Use full relative paths, e.g.
    # "_boards/mcxw23evk", "_devices/MCXW235", or any category dir. Migrated _boards/_devices
    # entries are also promoted to the public example.yml format for `west list_project`.
    examples_int_paths: List[str] = None
    # Tailoring: sub-paths under mcuxsdk/devices/ to keep; all others are removed (empty = keep all)
    devices_filter: List[str] = None
    # Tailoring: folder names under mcuxsdk/drivers/ to keep; all others are removed (empty = keep all)
    drivers_filter: List[str] = None

    def __post_init__(self):
        if self.board_filter is None:
            self.board_filter = []
        if self.devices_int_paths is None:
            self.devices_int_paths = []
        if self.examples_int_paths is None:
            self.examples_int_paths = []
        if self.devices_filter is None:
            self.devices_filter = []
        if self.drivers_filter is None:
            self.drivers_filter = []


class PackageCreator:
    """Independent utility for creating filtered SDK packages."""
    
    def __init__(self, workspace_root: str, logger: Callable[[str, str], None] = None):
        self.workspace_root = workspace_root
        self.logger = logger or self._default_logger
    
    def _default_logger(self, message: str, level: str = 'inf'):
        """Default logger that does nothing."""
        pass
    
    def create_package(self, output_path: str, projects: List, repo_list: List[str], 
                      example_list: List[str], options: PackageOptions) -> None:
        """Create a filtered package with the specified options."""
        self.logger(f"Starting package creation: {output_path}", 'inf')
        self.logger(f"Package options: git={options.include_git}, docs={options.generate_docs}, "
                   f"board_filter={options.board_filter}", 'dbg')
        
        temp_dir = tempfile.mkdtemp(prefix='sdk_package_')
        self.logger(f"Created temporary directory: {temp_dir}", 'dbg')
        
        try:
            start_time = time.time()
            
            # Copy repositories with filtering
            self.logger("Starting repository copy phase", 'inf')
            copy_start = time.time()
            self._copy_repositories(temp_dir, projects, options)
            copy_elapsed = time.time() - copy_start
            self.logger(f"Repository copy completed in {copy_elapsed:.2f} seconds", 'inf')

            # Clean west manifest filter settings in temp directory
            self.logger("Cleaning west manifest filter settings", 'inf')
            self._clean_west_filters(temp_dir)
            
            # Handle documentation
            if options.generate_docs:
                self.logger("Starting documentation generation phase", 'inf')
                doc_start = time.time()
                self._generate_documentation(temp_dir, options.board_filter)
                doc_elapsed = time.time() - doc_start
                self.logger(f"Documentation generation completed in {doc_elapsed:.2f} seconds", 'inf')
            else:
                self.logger("Removing documentation directory", 'dbg')
                self._remove_docs_directory(temp_dir)

            # Filter examples
            self.logger("Starting example filtering phase", 'inf')
            filter_start = time.time()
            self._remove_unwanted_directory(temp_dir, repo_list)
            self._filter_examples(temp_dir, repo_list, example_list, options.board_filter)
            filter_elapsed = time.time() - filter_start
            self.logger(f"Example filtering completed in {filter_elapsed:.2f} seconds", 'inf')

            # Tailor devices and drivers trees
            if options.devices_filter:
                self.logger("Starting devices tree tailoring phase", 'inf')
                dev_start = time.time()
                # Only device-family folders provided by separate device projects (mcuxsdk/devices/<Family>)
                # are eligible for trimming; core (mcuxsdk repo) content under devices/ is always kept.
                device_families = self._device_project_families(projects)
                self._filter_devices(temp_dir, options.devices_filter, device_families)
                self.logger(f"Devices tailoring completed in {time.time() - dev_start:.2f} seconds", 'inf')

            if options.drivers_filter:
                self.logger("Starting drivers tree tailoring phase", 'inf')
                drv_start = time.time()
                self._filter_drivers(temp_dir, options.drivers_filter)
                self.logger(f"Drivers tailoring completed in {time.time() - drv_start:.2f} seconds", 'inf')

            # Selective migration: copy only the configured sub-paths from internal repos.
            # The internal repos are excluded from _copy_repositories upstream (in update_board),
            # so only the listed sub-paths reach the package staging directory.
            if options.devices_int_paths:
                self.logger("Starting devices_int migration phase", 'inf')
                di_start = time.time()
                self._migrate_devices_int(temp_dir, options.devices_int_paths)
                self.logger(f"devices_int migration completed in {time.time() - di_start:.2f} seconds", 'inf')

            if options.examples_int_paths:
                self.logger("Starting examples_int migration phase", 'inf')
                ei_start = time.time()
                self._migrate_examples_int(temp_dir, options.examples_int_paths)
                self.logger(f"examples_int migration completed in {time.time() - ei_start:.2f} seconds", 'inf')
                # Promote migrated internal boards/devices to the public example.yml format so
                # `west list_project` can discover them (see _promote_internal_examples).
                self.logger("Starting internal example.yml promotion phase", 'inf')
                pr_start = time.time()
                self._promote_internal_examples(temp_dir, options.examples_int_paths)
                self.logger(f"example.yml promotion completed in {time.time() - pr_start:.2f} seconds", 'inf')

            # Create final archive
            self.logger("Starting archive creation phase", 'inf')
            archive_start = time.time()
            self._create_archive(temp_dir, output_path)
            archive_elapsed = time.time() - archive_start
            self.logger(f"Archive creation completed in {archive_elapsed:.2f} seconds", 'inf')
            
            total_elapsed = time.time() - start_time
            self.logger(f"Package creation completed successfully in {total_elapsed:.2f} seconds", 'inf')
            
        except Exception as e:
            self.logger(f"Package creation failed: {e}", 'err')
            raise
        finally:
            if os.path.exists(temp_dir):
                self.logger(f"Cleaning up temporary directory: {temp_dir}", 'dbg')
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    self.logger(f"Cleaning tmp folder failed: {e}, please delete manually {temp_dir}.", 'wrn')

    def _clean_west_filters(self, temp_dir: str) -> None:
        """Clean west manifest filter settings in the temporary directory."""
        try:
            # Remove manifest.project-filter setting
            self.logger("Removing manifest.project-filter setting", 'dbg')
            result = subprocess.run(
                ['west', 'config', '-d', 'manifest.project-filter'],
                cwd=temp_dir,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                self.logger("Successfully removed manifest.project-filter", 'dbg')
            else:
                self.logger(f"manifest.project-filter not set or already removed", 'dbg')
            
            # Remove manifest.group-filter setting
            self.logger("Removing manifest.group-filter setting", 'dbg')
            result = subprocess.run(
                ['west', 'config', '-d', 'manifest.group-filter'],
                cwd=temp_dir,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                self.logger("Successfully removed manifest.group-filter", 'dbg')
            else:
                self.logger(f"manifest.group-filter not set or already removed", 'dbg')
                
            self.logger("West manifest filter settings cleaned", 'inf')
            
        except subprocess.CalledProcessError as e:
            self.logger(f"Warning: Failed to clean west filters: {e}", 'wrn')
            # Don't fail the entire operation if filter cleanup fails
            pass
        except Exception as e:
            self.logger(f"Warning: Unexpected error cleaning west filters: {e}", 'wrn')
            pass

    def _copy_repositories(self, temp_dir: str, projects: List, options: PackageOptions) -> None:
        """Copy repositories with filtering."""
        self.logger(f"Copying {len(projects)} repositories to temporary directory", 'inf')
        
        # Get boards to exclude from filtering
        excluded_boards = self._get_excluded_boards(options.board_filter)
        self.logger(f"Excluding {len(excluded_boards)} boards from filtering: {sorted(list(excluded_boards))}", 'dbg')
        
        # Copy workspace metadata
        for meta_dir in ['.west', 'manifests']:
            src = os.path.join(self.workspace_root, meta_dir)
            if os.path.exists(src):
                dst = os.path.join(temp_dir, meta_dir)
                self.logger(f"Copying workspace metadata: {meta_dir}", 'dbg')
                self._copy_with_filtering(src, dst, excluded_boards, options.include_git)
            else:
                self.logger(f"Workspace metadata directory not found: {meta_dir}", 'dbg')
        
        # Internal repos with selective migration configured are NOT copied wholesale here.
        # They remain checked out/updated in the live workspace, but only the sub-paths listed
        # in devices_int_paths / examples_int_paths are migrated into the package later.
        migration_skip = set()
        if options.devices_int_paths:
            migration_skip.add('mcu-sdk-devices-int')
        if options.examples_int_paths:
            migration_skip.add('mcu-sdk-examples-int')

        # Copy project repositories
        for project in projects:
            if project.name in migration_skip:
                self.logger(
                    f"Skipping full copy of {project.name}; only configured sub-paths will be migrated", 'inf')
                continue
            self.logger(f"Copying project: {project.name} ({project.path})", 'dbg')
            self._copy_project(temp_dir, project, excluded_boards, options.include_git)

    def _copy_project(self, temp_dir: str, project, excluded_boards: Set[str],
                     include_git: bool) -> None:
        """Copy a single project."""
        src = os.path.join(self.workspace_root, project.path)
        if not os.path.exists(src):
            self.logger(f"Project source directory not found: {src}", 'wrn')
            return
            
        dst = os.path.join(temp_dir, project.path)
        
        if project.name == "core" or project.name == "mcu-sdk-doc":
            self.logger(f"Using git tree copy for project: {project.name}", 'dbg')
            self._copy_project_with_git_tree(src, dst, excluded_boards, include_git)
        else:
            # Apply board filtering only to examples
            boards_to_exclude = excluded_boards if project.path == "mcuxsdk/examples" else set()
            if boards_to_exclude:
                self.logger(f"Applying board filtering to project {project.name}: excluding {len(boards_to_exclude)} boards", 'dbg')
            else:
                self.logger(f"No board filtering applied to project: {project.name}", 'dbg')
            self._copy_with_filtering(src, dst, boards_to_exclude, include_git)

    def _copy_git_folder(self, src_dir: str, dst_dir: str) -> None:
        """Copy .git folder from source to destination."""
        git_src = os.path.join(src_dir, '.git')
        git_dst = os.path.join(dst_dir, '.git')
        
        if not os.path.exists(git_src):
            self.logger(f"No .git folder found in: {src_dir}", 'dbg')
            return
        
        try:
            if os.path.isfile(git_src):
                # .git is a file (worktree or submodule)
                self.logger(f"Copying .git file (worktree/submodule): {git_src}", 'dbg')
                shutil.copy2(git_src, git_dst)
            elif os.path.isdir(git_src):
                # .git is a directory (normal repository)
                self.logger(f"Copying .git directory: {git_src}", 'dbg')
                shutil.copytree(git_src, git_dst, symlinks=True, ignore_dangling_symlinks=True)
            elif os.path.islink(git_src):
                # .git is a symlink
                self.logger(f"Copying .git symlink: {git_src}", 'dbg')
                self._copy_symlink(git_src, git_dst)
                
        except (OSError, IOError, shutil.Error) as e:
            # Log the error but don't fail the entire operation
            self.logger(f"Failed to copy .git folder from {git_src}: {e}", 'wrn')
            pass

    def _copy_project_with_git_tree(self, src_dir: str, dest_dir: str, excluded_boards: Set[str], 
                           include_git: bool, rel_path: str = "") -> None:
        """Copy project using git ls-tree to get repository structure."""
        
        try:
            # Get the current HEAD tree
            cmd = ['git', 'ls-tree', '-r', '--name-only', 'HEAD']
            self.logger(f"Getting git tree for: {src_dir}", 'dbg')
            result = subprocess.run(cmd, cwd=src_dir, capture_output=True, text=True, check=True)
            
            tracked_files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
            self.logger(f"Found {len(tracked_files)} tracked files in git repository", 'dbg')

            if not tracked_files:
                self.logger(f"No tracked files found in git repository: {src_dir}", 'wrn')
                return

            copied_files = 0
            excluded_files = 0
            
            for rel_file_path in tracked_files:
                src_file = os.path.join(src_dir, rel_file_path)
                dst_file = os.path.join(dest_dir, rel_file_path)
                
                if not os.path.exists(src_file):
                    continue
                
                if self._should_exclude_path(rel_file_path, excluded_boards, include_git):
                    excluded_files += 1
                    continue
                
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                
                if os.path.islink(src_file):
                    self._copy_symlink(src_file, dst_file)
                elif os.path.isfile(src_file):
                    shutil.copy2(src_file, dst_file)
                
                copied_files += 1
                    
            self.logger(f"Git tree copy completed: {copied_files} files copied, {excluded_files} files excluded", 'dbg')
            
        except subprocess.CalledProcessError as e:
            self.logger(f"Git ls-tree command failed for {src_dir}: {e}", 'wrn')
            pass

        if include_git:
            self.logger(f"Including .git history for: {src_dir}", 'dbg')
            self._copy_git_folder(src_dir, dest_dir)
    
    def _copy_with_filtering(self, src_dir: str, dest_dir: str, excluded_boards: Set[str], 
                           include_git: bool, rel_path: str = "") -> None:
        """Recursively copy with filtering."""
        if not os.path.exists(src_dir):
            self.logger(f"Source directory does not exist: {src_dir}", 'wrn')
            return
        
        os.makedirs(dest_dir, exist_ok=True)
        
        try:
            items = os.listdir(src_dir)
        except (OSError, PermissionError) as e:
            self.logger(f"Cannot read directory {src_dir}: {e}", 'wrn')
            return
        
        copied_items = 0
        excluded_items = 0
        
        for item in items:
            src_item = os.path.join(src_dir, item)
            dest_item = os.path.join(dest_dir, item)
            item_rel_path = os.path.join(rel_path, item) if rel_path else item
            
            # Apply filtering rules
            if self._should_exclude_path(item_rel_path, excluded_boards, include_git):
                excluded_items += 1
                continue
            
            if os.path.islink(src_item):
                self._copy_symlink(src_item, dest_item)
                copied_items += 1
            elif os.path.isdir(src_item):
                self._copy_with_filtering(src_item, dest_item, excluded_boards, include_git, item_rel_path)
                copied_items += 1
            elif os.path.isfile(src_item):
                os.makedirs(os.path.dirname(dest_item), exist_ok=True)
                shutil.copy2(src_item, dest_item)
                copied_items += 1
        
        if rel_path == "":  # Only log for top-level directory
            self.logger(f"Directory copy completed for {src_dir}: {copied_items} items copied, {excluded_items} items excluded", 'dbg')
    
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
            self.logger(f"Copied symlink: {src_path} -> {link_target}", 'dbg')
        except (OSError, IOError) as e:
            # Fallback to copying content
            self.logger(f"Failed to copy symlink {src_path}, falling back to content copy: {e}", 'dbg')
            try:
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dest_path, symlinks=False)
                else:
                    shutil.copy2(src_path, dest_path)
            except Exception as fallback_e:
                self.logger(f"Fallback copy also failed for {src_path}: {fallback_e}", 'wrn')
                pass
    
    def _get_excluded_boards(self, target_boards: List[str]) -> Set[str]:
        """Get board names to exclude from filtering."""
        if not target_boards:
            self.logger("No target boards specified, no board filtering will be applied", 'dbg')
            return set()
        
        target_set = set(target_boards)
        excluded = set()
        
        boards_dir = os.path.join(self.workspace_root, "mcuxsdk", "examples", "_boards")
        self.logger(f"Scanning boards directory for filtering: {boards_dir}", 'dbg')
        
        if os.path.isdir(boards_dir):
            try:
                all_boards = []
                for name in os.listdir(boards_dir):
                    board_path = os.path.join(boards_dir, name)
                    if os.path.isdir(board_path):
                        all_boards.append(name)
                        if name not in target_set:
                            excluded.add(name)
                
                self.logger(f"Found {len(all_boards)} total boards, excluding {len(excluded)} boards", 'dbg')
                self.logger(f"Target boards (including dependencies):{sorted(target_set)}", 'dbg')
                
            except (OSError, PermissionError) as e:
                self.logger(f"Error reading boards directory {boards_dir}: {e}", 'wrn')
                pass
        else:
            self.logger(f"Boards directory not found: {boards_dir}", 'wrn')
        
        return excluded
    
    def _generate_documentation(self, temp_dir: str, board_list: List[str]) -> None:
        """Generate documentation for specified boards."""
        if not board_list:
            self.logger("No boards specified for documentation generation", 'dbg')
            return
        
        self.logger(f"Generating documentation for {len(board_list)} boards: {board_list}", 'inf')
        
        docs_dir = os.path.join(temp_dir, "mcuxsdk", "docs")
        os.makedirs(docs_dir, exist_ok=True)
        
        successful_docs = 0
        failed_docs = 0
        
        for board_name in board_list:
            try:
                self.logger(f"Generating PDF documentation for board: {board_name}", 'dbg')
                cmd = ['west', 'doc', 'pdf', '--board', board_name]
                result = subprocess.run(cmd, cwd=temp_dir, check=True, capture_output=True, text=True)
                
                # Move generated PDF to final location
                pdf_src = os.path.join(temp_dir, "mcuxsdk", "docs", "_build", "latex", f"mcuxsdk-{board_name}.pdf")
                if os.path.exists(pdf_src):
                    pdf_dest = os.path.join(docs_dir, f"mcuxsdk-{board_name}.pdf")
                    shutil.move(pdf_src, pdf_dest)
                    successful_docs += 1
                    self.logger(f"Generated documentation: mcuxsdk-{board_name}.pdf", 'dbg')
                else:
                    self.logger(f"Expected PDF not found for board {board_name}: {pdf_src}", 'wrn')
                    failed_docs += 1
                    
            except subprocess.CalledProcessError as e:
                failed_docs += 1
                self.logger(f"Documentation generation failed for board {board_name}: {e}", 'wrn')
                if e.stderr:
                    self.logger(f"Doc generation error output: {e.stderr.strip()}", 'dbg')
                continue
        
        # Clean up build directory
        build_dir = os.path.join(temp_dir, "mcuxsdk", "docs", "_build")
        if os.path.exists(build_dir):
            self.logger("Cleaning up documentation build directory", 'dbg')
            shutil.rmtree(build_dir)
        
        self.logger(f"Documentation generation completed: {successful_docs} successful, {failed_docs} failed", 'inf')
    
    def _remove_docs_directory(self, temp_dir: str) -> None:
        """Remove docs directory."""
        docs_dir = os.path.join(temp_dir, "mcuxsdk", "docs")
        if os.path.exists(docs_dir):
            self.logger(f"Removing docs directory: {docs_dir}", 'dbg')
            shutil.rmtree(docs_dir)
        else:
            self.logger("No docs directory found to remove", 'dbg')

    def _remove_unwanted_directory(self, temp_dir: str, repo_list: List[str]) -> None:
        # Remove unwanted project directories
        removed_projects = 0
        manifest = Manifest.from_topdir()
        for project in manifest.projects:
            if project.name not in repo_list and project.path != "manifests":
                proj_dir = os.path.join(temp_dir, project.path)
                if os.path.exists(proj_dir):
                    shutil.rmtree(proj_dir)
                    self.logger(f"Removed unwanted project directory {proj_dir} ", 'dbg')
                    removed_projects += 1
        
        if removed_projects > 0:
            self.logger(f"Removed {removed_projects} unwanted project directories", 'inf')

    def _filter_examples(self, temp_dir: str, repo_list: List[str], 
                        example_list: List[str], board_filter: List[str]) -> None:
        """Filter examples based on the provided lists."""
        examples_root = os.path.join(temp_dir, "mcuxsdk", "examples")
        if not os.path.exists(examples_root):
            self.logger(f"Examples directory not found: {examples_root}", 'wrn')
            return
        
        self.logger(f"Filtering examples with {len(example_list)} target examples and {len(board_filter)} board filters", 'inf')
        
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
        
        self.logger(f"Target paths: {len(target_paths)} paths, Parent paths: {len(parent_paths)} paths", 'dbg')
        
        # Remove unwanted directories
        removed_count = self._remove_unwanted_examples(examples_root, "", target_paths, parent_paths)
        self.logger(f"Removed {removed_count} unwanted example directories", 'inf')
        
        # Filter example.yml files
        filtered_count = self._filter_example_yml_files(temp_dir, board_filter)
        self.logger(f"Filtered {filtered_count} example.yml files", 'inf')
    
    def _remove_unwanted_examples(self, current_dir: str, relative_path: str, 
                                 target_paths: Set[str], parent_paths: Set[str]) -> int:
        """Recursively remove unwanted example directories."""
        if not os.path.exists(current_dir):
            return 0
        
        try:
            items = os.listdir(current_dir)
        except (OSError, PermissionError) as e:
            self.logger(f"Cannot read directory {current_dir}: {e}", 'wrn')
            return 0
        
        removed_count = 0
        
        for item in items:
            item_path = os.path.join(current_dir, item)
            item_relative = os.path.join(relative_path, item).replace('\\', '/') if relative_path else item
            
            if os.path.isdir(item_path):
                if item_relative in target_paths:
                    self.logger(f"Keeping target path: {item_relative}", 'dbg')
                    continue  # Keep target path
                elif item_relative in parent_paths:
                    # Keep parent but recurse
                    self.logger(f"Keeping parent path and recursing: {item_relative}", 'dbg')
                    removed_count += self._remove_unwanted_examples(item_path, item_relative, target_paths, parent_paths)
                else:
                    # Remove unwanted directory
                    try:
                        self.logger(f"Removing unwanted example directory: {item_relative}", 'dbg')
                        shutil.rmtree(item_path)
                        removed_count += 1
                    except (OSError, IOError) as e:
                        self.logger(f"Failed to remove directory {item_path}: {e}", 'wrn')
                        pass
        
        return removed_count
    
    def _filter_example_yml_files(self, temp_dir: str, target_boards: List[str]) -> int:
        """Filter example.yml files to include only target boards."""
        if not target_boards:
            self.logger("No target boards specified, skipping example.yml filtering", 'dbg')
            return 0
        
        #change examples dir to the mcuxsdk folder, because now examples not limit in examples folder.
        examples_dir = os.path.join(temp_dir, "mcuxsdk")
        if not os.path.exists(examples_dir):
            self.logger(f"Examples directory not found for yml filtering: {examples_dir}", 'wrn')
            return 0
        
        self.logger(f"Filtering example.yml files for boards: {target_boards}", 'dbg')
        
        # Find all example.yml files
        yml_files = []
        for root, dirs, files in os.walk(examples_dir):
            for file in files:
                if file == 'example.yml':
                    yml_files.append(os.path.join(root, file))
        
        self.logger(f"Found {len(yml_files)} example.yml files to process", 'dbg')
        
        filtered_count = 0
        for yml_path in yml_files:
            if self._filter_single_example_yml(yml_path, target_boards):
                filtered_count += 1
        
        return filtered_count
    
    def _filter_single_example_yml(self, yml_file: str, target_boards: List[str]) -> bool:
        """Filter a single example.yml file."""
        try:
            with open(yml_file, 'r', encoding='utf-8') as f:
                yaml_data = yaml.safe_load(f)
            
            if not yaml_data or not isinstance(yaml_data, dict):
                return False
            
            modified = False
            
            for example_name, example_config in yaml_data.items():
                if not isinstance(example_config, dict) or 'boards' not in example_config:
                    continue
                
                boards_config = example_config['boards']
                if not isinstance(boards_config, dict):
                    continue
                
                original_board_count = len(boards_config)
                
                # Filter boards
                filtered_boards = {
                    board_key: board_value 
                    for board_key, board_value in boards_config.items()
                    if any(target_board in board_key for target_board in target_boards)
                }
                
                if len(filtered_boards) != original_board_count:
                    example_config['boards'] = filtered_boards
                    modified = True
                    self.logger(f"Filtered example {example_name} in {yml_file}: "
                               f"{original_board_count} -> {len(filtered_boards)} boards", 'dbg')
            
            # Write back if modified
            if modified:
                with open(yml_file, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(yaml_data, f, default_flow_style=False, sort_keys=False)
                return True
                    
        except (yaml.YAMLError, IOError) as e:
            self.logger(f"Error processing example.yml file {yml_file}: {e}", 'wrn')
            pass
        
        return False
    
    @staticmethod
    def _device_project_families(projects: List) -> Set[str]:
        """Top-level devices/ folder names that come from separate device projects.

        These (e.g. RT, MCX, Wireless from the mcux-devices-* projects, mounted at
        mcuxsdk/devices/<Family>) are the only entries devices_filter may trim. Everything else
        under devices/ belongs to the core mcuxsdk repo and is always kept.
        """
        prefix = "mcuxsdk/devices/"
        families: Set[str] = set()
        for p in projects:
            pp = str(getattr(p, 'path', '')).replace('\\', '/')
            if pp.startswith(prefix):
                rest = pp[len(prefix):].strip('/')
                if rest:
                    families.add(rest.split('/')[0])
        return families

    def _filter_devices(self, temp_dir: str, devices_filter: List[str],
                        device_families: Optional[Set[str]] = None) -> None:
        """Trim mcuxsdk/devices/ to only the sub-paths listed in devices_filter.

        Only device-family folders provided by separate device projects (device_families) are
        eligible for removal; core mcuxsdk-repo content under devices/ (arm, xtensa, Kconfig, ...)
        is always kept. Files at any level are always kept. Entry forms (relative to devices/):
          - "arm"            keep the whole family tree
          - "Wireless/MCXW235"  keep that device dir (plus its family's root files)
          - "RT/*"           keep only RT/'s ROOT FILES and exclude ALL of RT/'s subfolders
                             (useful when the actual device is added later by migration)
        """
        devices_root = os.path.join(temp_dir, "mcuxsdk", "devices")
        if not os.path.isdir(devices_root):
            self.logger(f"devices/ directory not found in package staging dir: {devices_root}", 'wrn')
            return

        device_families = device_families or set()

        # Normalise filter paths to forward slashes
        normalised = [p.replace('\\', '/').strip('/') for p in devices_filter]

        # Build exact-keep and parent sets.
        #  - target_paths: directories kept in full.
        #  - parent_paths: directories kept but recursed into (children still evaluated). A parent
        #    with no matching target beneath it keeps its files while dropping every subfolder,
        #    which is exactly what a "<dir>/*" entry requests.
        target_paths: Set[str] = set()
        parent_paths: Set[str] = set()
        for path in normalised:
            if path.endswith('/*'):
                base = path[:-2].strip('/')
                if not base:
                    continue
                parts = base.split('/')
                for i in range(1, len(parts) + 1):
                    parent_paths.add('/'.join(parts[:i]))
                continue
            target_paths.add(path)
            parts = path.split('/')
            for i in range(1, len(parts)):
                parent_paths.add('/'.join(parts[:i]))

        # Always keep core (mcuxsdk-repo) top-level entries: only separate device-project families
        # are trimmable. A core family dir is kept in full by adding it to target_paths.
        try:
            for entry in os.listdir(devices_root):
                if os.path.isdir(os.path.join(devices_root, entry)) and entry not in device_families:
                    target_paths.add(entry)
        except (OSError, PermissionError) as e:
            self.logger(f"Cannot read devices/ for core-keep pass: {e}", 'wrn')

        removed = self._remove_unwanted_examples(devices_root, "", target_paths, parent_paths)
        self.logger(f"Devices tailoring: removed {removed} unwanted entries from devices/", 'inf')

    def _filter_drivers(self, temp_dir: str, drivers_filter: List[str]) -> None:
        """Trim mcuxsdk/drivers/ to only the top-level folders listed in drivers_filter.

        Each entry is a folder name directly inside mcuxsdk/drivers/ (e.g. "lpuart", "gpio").
        Files at the top level of drivers/ are always kept. Sub-folders not in the whitelist
        are removed entirely.
        """
        drivers_root = os.path.join(temp_dir, "mcuxsdk", "drivers")
        if not os.path.isdir(drivers_root):
            self.logger(f"drivers/ directory not found in package staging dir: {drivers_root}", 'wrn')
            return

        keep_set = set(drivers_filter)
        removed = 0

        try:
            for entry in os.listdir(drivers_root):
                entry_path = os.path.join(drivers_root, entry)
                if os.path.isdir(entry_path) and entry not in keep_set:
                    try:
                        shutil.rmtree(entry_path)
                        removed += 1
                        self.logger(f"Drivers tailoring: removed drivers/{entry}", 'dbg')
                    except (OSError, IOError) as e:
                        self.logger(f"Failed to remove drivers/{entry}: {e}", 'wrn')
        except (OSError, PermissionError) as e:
            self.logger(f"Cannot read drivers/ directory: {e}", 'wrn')
            return

        self.logger(f"Drivers tailoring: removed {removed} unwanted entries from drivers/", 'inf')

    def _migrate_devices_int(self, temp_dir: str, devices_int_paths: List[str]) -> None:
        """Merge sub-paths from the live workspace's devices_int/ into the package's devices/ tree.

        Source:      <workspace_root>/mcuxsdk/devices_int/<path>/
        Destination: <temp_dir>/mcuxsdk/devices/<path>/

        The internal content is merged into the public devices/ tree so the package looks like a
        normal SDK with no devices_int/ folder. The live workspace is never modified; this copies
        into the package staging dir only. Skips any path whose source directory does not exist.
        """
        devices_int_root = os.path.join(self.workspace_root, "mcuxsdk", "devices_int")
        if not os.path.isdir(devices_int_root):
            self.logger(
                "mcuxsdk/devices_int/ not found in workspace — skipping devices_int migration. "
                "Ensure mcu-sdk-devices-int is checked out.", 'wrn')
            return

        copied_paths = 0
        for rel_path in devices_int_paths:
            src = os.path.join(devices_int_root, rel_path.replace('/', os.sep))
            dst = os.path.join(temp_dir, "mcuxsdk", "devices", rel_path.replace('/', os.sep))

            if not os.path.exists(src):
                self.logger(f"devices_int path not found, skipping: {rel_path}", 'wrn')
                continue

            try:
                if os.path.isdir(src):
                    self.logger(f"Migrating devices_int/{rel_path} -> devices/{rel_path}", 'dbg')
                    self._copy_with_filtering(src, dst, set(), False)
                else:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                self._rewrite_internal_paths(dst)
                copied_paths += 1
                self.logger(f"Migrated devices_int/{rel_path} into devices/{rel_path}", 'dbg')
            except (OSError, IOError, shutil.Error) as e:
                self.logger(f"Failed to migrate devices_int/{rel_path}: {e}", 'wrn')

        self.logger(
            f"devices_int migration: {copied_paths}/{len(devices_int_paths)} paths merged into devices/", 'inf')

    def _migrate_examples_int(self, temp_dir: str, examples_int_paths: List[str]) -> None:
        """Merge sub-paths from the live workspace's examples_int/ into the package's examples/ tree.

        Source:      <workspace_root>/mcuxsdk/examples_int/<path>/
        Destination: <temp_dir>/mcuxsdk/examples/<path>/

        Each entry in examples_int_paths is a forward-slash path relative to mcuxsdk/examples_int/,
        e.g. "_boards/mcxw23evk", "_devices/MCXW235", or any example category directory. The internal
        content is merged into the public examples/ tree so the package looks like a normal SDK with
        no examples_int/ folder. The live workspace is never modified; this copies into the package
        staging dir only. Skips paths that do not exist in the workspace (logs a warning).
        Requires mcu-sdk-examples-int to be checked out in the workspace.
        """
        examples_int_root = os.path.join(self.workspace_root, "mcuxsdk", "examples_int")
        if not os.path.isdir(examples_int_root):
            self.logger(
                "mcuxsdk/examples_int/ not found in workspace — skipping examples_int migration. "
                "Ensure mcu-sdk-examples-int is checked out.", 'wrn')
            return

        copied_paths = 0
        for rel_path in examples_int_paths:
            src = os.path.join(examples_int_root, rel_path.replace('/', os.sep))
            dst = os.path.join(temp_dir, "mcuxsdk", "examples", rel_path.replace('/', os.sep))

            if not os.path.exists(src):
                self.logger(f"examples_int/{rel_path} not found, skipping", 'wrn')
                continue

            try:
                if os.path.isdir(src):
                    self.logger(f"Migrating examples_int/{rel_path} -> examples/{rel_path}", 'dbg')
                    self._copy_with_filtering(src, dst, set(), False)
                else:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                self._rewrite_internal_paths(dst)
                copied_paths += 1
                self.logger(f"Migrated examples_int/{rel_path} into examples/{rel_path}", 'dbg')
            except (OSError, IOError, shutil.Error) as e:
                self.logger(f"Failed to migrate examples_int/{rel_path}: {e}", 'wrn')

        self.logger(
            f"examples_int migration: {copied_paths}/{len(examples_int_paths)} paths merged into examples/", 'inf')

    # ------------------------------------------------------------------
    # Internal path rewrite (repoint _int paths to the public trees)
    # ------------------------------------------------------------------

    # Build-relevant text files whose hardcoded internal paths must be repointed after migration.
    _PATH_REWRITE_SUFFIXES = ('.cmake', '.txt', '.yml', '.yaml', '.conf', '.overlay', '.cfg', '.ld')

    def _rewrite_internal_paths(self, dest: str) -> None:
        """Repoint `examples_int/` and `devices_int/` references in migrated build files to public.

        Migrated content may hardcode internal paths (e.g. a board variable.cmake setting
        `board_root examples_int/_boards` or including `devices_int/<...>/variable.cmake`). Because
        the content is merged into the public examples/ and devices/ trees, those prefixes must be
        rewritten so the package builds without manual edits. Only build-relevant text files are
        touched; `dest` may be a file or a directory tree.
        """
        if os.path.isfile(dest):
            self._rewrite_internal_paths_in_file(dest)
            return
        for root, _dirs, files in os.walk(dest):
            for name in files:
                if (name == 'CMakeLists.txt' or name.startswith('Kconfig')
                        or name.endswith(self._PATH_REWRITE_SUFFIXES)):
                    self._rewrite_internal_paths_in_file(os.path.join(root, name))

    def _rewrite_internal_paths_in_file(self, path: str) -> None:
        """Rewrite internal path prefixes in a single file (no-op for binary/unchanged files)."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
        except (OSError, IOError, UnicodeDecodeError):
            return  # unreadable or binary; skip
        # Match whole tokens so both path prefixes ("devices_int/...") and bare values
        # (e.g. `mcux_set_variable(device_root devices_int)`, used to build Kconfig rsource paths)
        # are repointed to the public trees, while similarly-named tokens (devices_int_paths,
        # xxx_devices_int) are left untouched by the \b boundaries.
        updated = re.sub(r'\bexamples_int\b', 'examples', content)
        updated = re.sub(r'\bdevices_int\b', 'devices', updated)
        if updated != content:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(updated)
                self.logger(f"Rewrote internal paths in {path}", 'dbg')
            except (OSError, IOError) as e:
                self.logger(f"Failed to rewrite internal paths in {path}: {e}", 'wrn')

    # ------------------------------------------------------------------
    # Internal example.yml promotion (internal enablement -> public format)
    # ------------------------------------------------------------------

    def _promote_internal_examples(self, temp_dir: str, examples_int_paths: List[str]) -> None:
        """Convert migrated internal _boards/_devices example.yml entries into the public format.

        Internal boards/devices store their example enablement inverted: each
        examples_int/_boards/<board>/<core>/example.yml lists the example names the board supports
        plus a `board.toolchains` default. The public flow instead expects each app's own
        examples/<category>/<app>/example.yml to list the instance under its `boards:` map
        (as `<board>@<core>: [<toolchain deltas>]`). Without this conversion `west list_project`
        cannot discover the migrated board because its enablement lives nowhere the public flow reads.

        This mirrors `west npi_move` (bifrost/scripts/west_commands/move_npi.py): for every example an
        instance enables, inject `<instance_key>: <toolchains-or-[]>` into that app's `boards:` (board)
        or `devices:` (device) map, then rewrite the migrated instance example.yml to the public form
        (only the `<instance_type>.toolchains` default). Runs only for _boards/<name> and
        _devices/<name> entries in examples_int_paths.
        """
        instances = self._collect_promotion_instances(temp_dir, examples_int_paths)
        if not instances:
            self.logger("Promotion: no _boards/_devices instances to promote", 'dbg')
            return
        self._apply_promotion_to_apps(temp_dir, instances)
        # Normalize each migrated instance example.yml to the public (toolchains-only) form.
        for inst in instances:
            self._clean_instance_example_yml(inst)

    @staticmethod
    def _promotion_delta(value) -> List[str]:
        """Return the public boards:/devices: value (toolchain delta list) for an internal entry.

        Internal entries may be None (`hello_world:`), a dict (`{toolchains: [...]}`), or already a
        list. The public map value is always the toolchain delta list; empty means "use the
        instance default toolchains" resolved from the instance example.yml.
        """
        if value is None:
            return []
        if isinstance(value, dict):
            return value.get('toolchains', []) or []
        if isinstance(value, list):
            return value
        return []

    def _collect_promotion_instances(self, temp_dir: str, examples_int_paths: List[str]) -> List[dict]:
        """Build the list of migrated board/device instances to promote.

        Each returned dict has: key (public map key, e.g. "fpga_imxrtl375@cm33_core0"), map_name
        ('boards'|'devices'), toolchains_key ('board.toolchains'|'device.toolchains'), toolchains
        (the default list or None), entries ({example_name: raw_value}), source_yml (migrated file).
        """
        examples_root = os.path.join(temp_dir, "mcuxsdk", "examples")
        seen = set()
        instances = []
        for rel_path in examples_int_paths:
            parts = rel_path.replace('\\', '/').strip('/').split('/')
            if len(parts) < 2 or parts[0] not in ('_boards', '_devices'):
                continue  # not an instance path (plain example dir); nothing to promote
            instance_type = 'board' if parts[0] == '_boards' else 'device'
            map_name = 'boards' if instance_type == 'board' else 'devices'
            name = parts[1]
            if (parts[0], name) in seen:
                continue
            seen.add((parts[0], name))

            instance_dir = os.path.join(examples_root, parts[0], name)
            if not os.path.isdir(instance_dir):
                self.logger(f"Promotion: migrated instance dir not found, skipping: {parts[0]}/{name}", 'wrn')
                continue

            # Single-instance layout (<name>/example.yml -> "<name>") or
            # per-core layout (<name>/<core>/example.yml -> "<name>@<core>").
            yml_specs = []
            top_yml = os.path.join(instance_dir, "example.yml")
            if os.path.isfile(top_yml):
                yml_specs.append((name, top_yml))
            else:
                for entry in sorted(os.listdir(instance_dir)):
                    core_yml = os.path.join(instance_dir, entry, "example.yml")
                    if os.path.isfile(core_yml):
                        yml_specs.append((f"{name}@{entry}", core_yml))

            if not yml_specs:
                self.logger(f"Promotion: no example.yml found under {parts[0]}/{name}, skipping", 'wrn')
                continue

            toolchains_key = f"{instance_type}.toolchains"
            for key, yml_path in yml_specs:
                data = self._load_yaml(yml_path)
                if not isinstance(data, dict):
                    continue
                entries = {k: v for k, v in data.items() if k != toolchains_key}
                instances.append({
                    'key': key,
                    'map_name': map_name,
                    'toolchains_key': toolchains_key,
                    'toolchains': data.get(toolchains_key),
                    'entries': entries,
                    'source_yml': yml_path,
                })
                self.logger(f"Promotion: instance '{key}' enables {len(entries)} example(s)", 'dbg')
        return instances

    def _apply_promotion_to_apps(self, temp_dir: str, instances: List[dict]) -> None:
        """Inject instance enablement into public app example.yml boards:/devices: maps.

        Walks examples/ once (pruning _boards/_devices) and applies every instance's entries to each
        matching app example.yml, preserving leading comment lines.
        """
        examples_root = os.path.join(temp_dir, "mcuxsdk", "examples")
        updated_files = 0
        for root, dirs, files in os.walk(examples_root):
            if os.path.abspath(root) == os.path.abspath(examples_root):
                dirs[:] = [d for d in dirs if d not in ('_boards', '_devices')]
            if "example.yml" not in files:
                continue
            yml_path = os.path.join(root, "example.yml")
            rel = os.path.relpath(root, examples_root).replace('\\', '/')
            data, comments = self._load_yaml_with_comments(yml_path)
            if not isinstance(data, dict):
                continue

            updated = False
            for inst in instances:
                map_name = inst['map_name']
                key = inst['key']
                for ex_name, value in inst['entries'].items():
                    app = data.get(ex_name)
                    if not isinstance(app, dict) or map_name not in app:
                        continue  # example not in this file, or not an instance-mapped example
                    inst_map = app.get(map_name) or {}
                    if not isinstance(inst_map, dict):
                        continue
                    if key not in inst_map:
                        inst_map[key] = self._promotion_delta(value)
                        app[map_name] = inst_map
                        updated = True
                        self.logger(f"Promotion: added '{key}' to '{ex_name}' in {rel}/example.yml", 'dbg')

            if updated:
                self._dump_yaml_with_comments(yml_path, data, comments)
                updated_files += 1

        self.logger(f"Promotion: updated {updated_files} public example.yml file(s)", 'inf')

    def _clean_instance_example_yml(self, inst: dict) -> None:
        """Rewrite a migrated instance example.yml to the public (toolchains-only) form."""
        toolchains = inst['toolchains']
        if toolchains is None:
            return  # no default toolchains to normalize; leave the migrated file untouched
        try:
            self._dump_yaml_with_comments(inst['source_yml'], {inst['toolchains_key']: toolchains}, [])
            self.logger(f"Promotion: normalized {inst['source_yml']} to toolchains-only form", 'dbg')
        except (OSError, IOError) as e:
            self.logger(f"Promotion: failed to normalize {inst['source_yml']}: {e}", 'wrn')

    def _load_yaml(self, path: str):
        """Load a YAML file, returning parsed data or None on error."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except (OSError, IOError, yaml.YAMLError) as e:
            self.logger(f"Promotion: failed to read {path}: {e}", 'wrn')
            return None

    def _load_yaml_with_comments(self, path: str):
        """Load YAML, returning (data, comment_lines) so top-level comments survive a re-dump."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            comments = [ln for ln in lines if ln.startswith('#')]
            data = yaml.safe_load(''.join(lines))
            return data, comments
        except (OSError, IOError, yaml.YAMLError) as e:
            self.logger(f"Promotion: failed to read {path}: {e}", 'wrn')
            return None, []

    def _dump_yaml_with_comments(self, path: str, data, comments: List[str]) -> None:
        """Write YAML preserving key order, re-prepending collected comment lines."""
        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        if comments:
            with open(path, 'r+', encoding='utf-8') as f:
                content = f.readlines()
                f.seek(0, 0)
                f.writelines(comments + content)

    def _create_archive(self, temp_dir: str, output_path: str) -> None:
        """Create the final zip archive."""
        self.logger(f"Creating zip archive: {output_path}", 'inf')
        
        output_zip_dir = os.path.dirname(output_path)
        if output_zip_dir and not os.path.exists(output_zip_dir):
            os.makedirs(output_zip_dir)
        
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
            file_count = self._add_directory_to_zip(zipf, temp_dir, "")
        
        # Get final archive size
        if os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            self.logger(f"Archive created successfully: {file_count} files, {size_mb:.1f} MB", 'inf')
        else:
            self.logger(f"Archive creation completed but file not found: {output_path}", 'wrn')
    
    def _add_directory_to_zip(self, zipf: zipfile.ZipFile, src_dir: str, arc_prefix: str) -> int:
        """Add directory contents to zip file."""
        try:
            items = os.listdir(src_dir)
        except (OSError, PermissionError) as e:
            self.logger(f"Cannot read directory for zip {src_dir}: {e}", 'wrn')
            return 0
        
        file_count = 0
        
        for item in items:
            src_path = os.path.join(src_dir, item)
            arc_path = os.path.join(arc_prefix, item) if arc_prefix else item
            arc_path = arc_path.replace(os.sep, '/')
      
            if os.path.islink(src_path):
                self._add_symlink_to_zip(zipf, src_path, arc_path)
                file_count += 1
            elif os.path.isdir(src_path):
                if not arc_path.endswith('/'):
                    arc_path += '/'
                zipf.writestr(arc_path, '')
                file_count += 1
                file_count += self._add_directory_to_zip(zipf, src_path, arc_path.rstrip('/'))
            elif os.path.isfile(src_path):
                zipf.write(src_path, arc_path)
                file_count += 1
        
        return file_count
    
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
            self.logger(f"Added symlink to zip: {arc_path} -> {link_target}", 'dbg')
        except (OSError, IOError) as e:
            # Fallback to regular file
            self.logger(f"Failed to add symlink {symlink_path} to zip, using fallback: {e}", 'dbg')
            if os.path.isfile(symlink_path):
                zipf.write(symlink_path, arc_path)
