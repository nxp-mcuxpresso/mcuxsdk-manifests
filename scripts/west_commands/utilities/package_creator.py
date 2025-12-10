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
from typing import List, Optional, Set, Callable
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
            self._filter_examples(temp_dir, repo_list, example_list, options.board_filter)
            filter_elapsed = time.time() - filter_start
            self.logger(f"Example filtering completed in {filter_elapsed:.2f} seconds", 'inf')

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
                shutil.rmtree(temp_dir)
    
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
        
        # Copy project repositories
        for project in projects:
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
