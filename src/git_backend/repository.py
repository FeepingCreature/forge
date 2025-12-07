"""
Git repository management using pygit2
"""

import pygit2
from pathlib import Path


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
        branch = self.repo.branches.create(branch_name, head.peel())
        
        return branch_name
        
    def commit_changes(self, message, files=None):
        """Create a commit with changes"""
        # TODO: Implement direct commit creation without touching working directory
        pass
        
    def get_file_content(self, filepath, commit=None):
        """Get file content from a specific commit or HEAD"""
        if commit is None:
            commit = self.repo.head.peel()
            
        try:
            entry = commit.tree[filepath]
            blob = self.repo[entry.id]
            return blob.data.decode('utf-8')
        except KeyError:
            return None
