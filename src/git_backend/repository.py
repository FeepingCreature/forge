"""
Git repository management using pygit2
"""

import pygit2
from pathlib import Path
from typing import Dict, Optional


class ForgeRepository:
    """Manages git repository operations for Forge"""
    
    def __init__(self, repo_path=None):
        """Initialize repository"""
        if repo_path is None:
            repo_path = self._find_repo()
        
        self.repo = pygit2.Repository(repo_path)
        
    def _find_repo(self):
        """Find git repository in current directory or parents"""
        current = Path.cwd()
        while current != current.parent:
            if (current / ".git").exists():
                return str(current)
            current = current.parent
        raise ValueError("Not in a git repository")
        
    def create_session_branch(self, session_name):
        """Create a new branch for an AI session"""
        # Get current HEAD
        head = self.repo.head
        
        # Create new branch
        branch_name = f"forge/session/{session_name}"
        
        # Check if branch already exists
        if branch_name in self.repo.branches:
            return branch_name
        
        branch = self.repo.branches.create(branch_name, head.peel())
        
        return branch_name
    
    def get_branch_head(self, branch_name: str) -> Optional[pygit2.Commit]:
        """Get the head commit of a branch"""
        try:
            branch = self.repo.branches[branch_name]
            return branch.peel(pygit2.Commit)
        except KeyError:
            return None
    
    def get_file_content(self, filepath: str, branch_name: Optional[str] = None) -> Optional[str]:
        """Get file content from a branch or HEAD"""
        if branch_name:
            commit = self.get_branch_head(branch_name)
        else:
            commit = self.repo.head.peel()
        
        if not commit:
            return None
            
        try:
            entry = commit.tree[filepath]
            blob = self.repo[entry.id]
            return blob.data.decode('utf-8')
        except (KeyError, AttributeError):
            return None
    
    def create_tree_from_changes(self, base_branch: str, changes: Dict[str, str]) -> pygit2.Oid:
        """
        Create a new tree with changes applied to base branch
        
        Args:
            base_branch: Branch name to use as base
            changes: Dict of filepath -> new_content
            
        Returns:
            OID of the new tree
        """
        # Get base commit
        base_commit = self.get_branch_head(base_branch)
        if not base_commit:
            raise ValueError(f"Branch {base_branch} not found")
        
        # Start with base tree
        base_tree = base_commit.tree
        
        # Build new tree with changes
        tree_builder = self.repo.TreeBuilder(base_tree)
        
        for filepath, content in changes.items():
            # Create blob for new content
            blob_oid = self.repo.create_blob(content.encode('utf-8'))
            
            # Add to tree (handles nested paths)
            self._add_to_tree(tree_builder, filepath, blob_oid, base_tree)
        
        # Write the tree
        tree_oid = tree_builder.write()
        return tree_oid
    
    def _add_to_tree(self, tree_builder, filepath: str, blob_oid: pygit2.Oid, base_tree):
        """Add a file to tree, handling nested directories"""
        parts = filepath.split('/')
        
        if len(parts) == 1:
            # Simple file in root
            tree_builder.insert(parts[0], blob_oid, pygit2.GIT_FILEMODE_BLOB)
        else:
            # Handle nested paths by recursively building subtrees
            dir_name = parts[0]
            rest_path = '/'.join(parts[1:])
            
            # Get or create subtree
            subtree = None
            if base_tree:
                try:
                    subtree_entry = base_tree[dir_name]
                    subtree = self.repo[subtree_entry.id]
                    subtree_builder = self.repo.TreeBuilder(subtree)
                except KeyError:
                    # Directory doesn't exist, create new tree
                    subtree_builder = self.repo.TreeBuilder()
            else:
                # No base tree, create new
                subtree_builder = self.repo.TreeBuilder()
            
            # Recursively add to subtree
            self._add_to_tree(subtree_builder, rest_path, blob_oid, subtree)
            
            # Write subtree and add to parent
            subtree_oid = subtree_builder.write()
            tree_builder.insert(dir_name, subtree_oid, pygit2.GIT_FILEMODE_TREE)
    
    def commit_tree(self, tree_oid: pygit2.Oid, message: str, branch_name: str, 
                    author_name: str = "Forge AI", author_email: str = "ai@forge.dev") -> pygit2.Oid:
        """
        Create a commit from a tree on a specific branch
        
        Args:
            tree_oid: OID of the tree to commit
            message: Commit message
            branch_name: Branch to commit to
            author_name: Author name
            author_email: Author email
            
        Returns:
            OID of the new commit
        """
        # Get parent commit
        parent_commit = self.get_branch_head(branch_name)
        if not parent_commit:
            raise ValueError(f"Branch {branch_name} not found")
        
        # Create signature
        signature = pygit2.Signature(author_name, author_email)
        
        # Create commit
        commit_oid = self.repo.create_commit(
            f'refs/heads/{branch_name}',  # Update branch ref
            signature,  # author
            signature,  # committer
            message,
            tree_oid,
            [parent_commit.oid]  # parents
        )
        
        return commit_oid
    
    def get_all_files(self, branch_name: Optional[str] = None) -> list[str]:
        """Get list of all files in repository"""
        if branch_name:
            commit = self.get_branch_head(branch_name)
        else:
            commit = self.repo.head.peel()
        
        if not commit:
            return []
        
        files = []
        
        def walk_tree(tree, path=""):
            for entry in tree:
                entry_path = f"{path}/{entry.name}" if path else entry.name
                if entry.type == 'tree':
                    # Recursively walk subdirectories
                    subtree = self.repo[entry.oid]
                    walk_tree(subtree, entry_path)
                else:
                    files.append(entry_path)
        
        walk_tree(commit.tree)
        return files
