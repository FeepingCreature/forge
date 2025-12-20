"""
Git repository management using pygit2
"""

import contextlib
from pathlib import Path

import pygit2

from forge.git_backend.commit_types import CommitType, format_commit_message, parse_commit_type


class ForgeRepository:
    """Manages git repository operations for Forge"""

    def __init__(self, repo_path: str | None = None) -> None:
        """Initialize repository"""
        if repo_path is None:
            repo_path = self._find_repo()

        self.repo = pygit2.Repository(repo_path)

    def _find_repo(self) -> str:
        """Find git repository in current directory or parents"""
        current = Path.cwd()
        while current != current.parent:
            if (current / ".git").exists():
                return str(current)
            current = current.parent
        raise ValueError("Not in a git repository")

    def create_session_branch(self, session_name: str) -> str:
        """Create a new branch for an AI session"""
        # Get current HEAD
        head = self.repo.head

        # Create new branch
        branch_name = f"forge/session/{session_name}"

        # Check if branch already exists
        if branch_name in self.repo.branches:
            return branch_name

        commit = head.peel(pygit2.Commit)
        self.repo.branches.create(branch_name, commit)

        return branch_name

    def get_branch_head(self, branch_name: str) -> pygit2.Commit:
        """Get the head commit of a branch"""
        branch = self.repo.branches[branch_name]
        return branch.peel(pygit2.Commit)

    def get_file_content(self, filepath: str, branch_name: str | None = None) -> str:
        """Get file content from a branch or HEAD"""
        if branch_name:
            commit = self.get_branch_head(branch_name)
        else:
            commit = self.repo.head.peel(pygit2.Commit)

        entry = commit.tree[filepath]
        blob = self.repo[entry.id]
        assert isinstance(blob, pygit2.Blob)
        return blob.data.decode("utf-8")

    def create_tree_from_changes(
        self,
        base_branch: str,
        changes: dict[str, str],
        deletions: set[str] | None = None,
    ) -> pygit2.Oid:
        """
        Create a new tree with changes applied to base branch.

        Properly handles multiple files in the same directory by building
        a nested structure first, then constructing trees recursively.

        Args:
            base_branch: Branch name to use as base
            changes: Dict of filepath -> new_content
            deletions: Set of filepaths to delete

        Returns:
            OID of the new tree
        """
        # Get base commit
        base_commit = self.get_branch_head(base_branch)
        base_tree = base_commit.tree

        # Build nested structure for changes: {name: blob_oid} or {name: {nested...}}
        nested_changes: dict = {}
        for filepath, content in changes.items():
            blob_oid = self.repo.create_blob(content.encode("utf-8"))
            parts = filepath.split("/")
            current = nested_changes
            for part in parts[:-1]:
                if part not in current or not isinstance(current[part], dict):
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = blob_oid

        # Build nested structure for deletions
        nested_deletions: set = deletions or set()

        # Recursively build tree
        tree_oid = self._build_tree_recursive(base_tree, nested_changes, nested_deletions, "")
        return tree_oid

    def _build_tree_recursive(
        self,
        base_tree: pygit2.Tree | None,
        changes: dict,
        deletions: set[str],
        current_path: str,
    ) -> pygit2.Oid:
        """
        Recursively build a tree from nested changes structure.

        Args:
            base_tree: The base tree at this level (or None for new dirs)
            changes: Nested dict of changes at this level
            deletions: Full set of deletion paths (we filter by current_path)
            current_path: Path prefix for this level (for deletion matching)
        """
        tree_builder = self.repo.TreeBuilder(base_tree) if base_tree else self.repo.TreeBuilder()

        # Find deletions at this level and subdirectories that need recursion for deletions
        subdirs_with_deletions: dict[str, set[str]] = {}
        for del_path in list(deletions):
            # Check if this deletion is at or below the current level
            if current_path:
                if not del_path.startswith(current_path + "/"):
                    continue
                relative = del_path[len(current_path) + 1 :]
            else:
                relative = del_path

            parts = relative.split("/")
            if len(parts) == 1:
                # Direct deletion at this level
                with contextlib.suppress(KeyError):
                    tree_builder.remove(parts[0])
            else:
                # Deletion in a subdirectory - we need to recurse into it
                subdir_name = parts[0]
                if subdir_name not in subdirs_with_deletions:
                    subdirs_with_deletions[subdir_name] = set()
                subdirs_with_deletions[subdir_name].add(del_path)

        # Process subdirectories that have deletions but no changes
        for subdir_name in subdirs_with_deletions:
            if subdir_name in changes:
                # Will be handled below in the changes loop
                continue

            # Need to recurse into this subdir just for deletions
            subtree = None
            if base_tree:
                try:
                    entry = base_tree[subdir_name]
                    obj = self.repo[entry.id]
                    if isinstance(obj, pygit2.Tree):
                        subtree = obj
                except KeyError:
                    pass

            if subtree:
                subpath = f"{current_path}/{subdir_name}" if current_path else subdir_name
                subtree_oid = self._build_tree_recursive(subtree, {}, deletions, subpath)
                tree_builder.insert(subdir_name, subtree_oid, pygit2.GIT_FILEMODE_TREE)

        # Process all changes at this level
        for name, value in changes.items():
            if isinstance(value, dict):
                # It's a subdirectory - recurse
                subtree = None
                if base_tree:
                    try:
                        entry = base_tree[name]
                        obj = self.repo[entry.id]
                        if isinstance(obj, pygit2.Tree):
                            subtree = obj
                    except KeyError:
                        pass

                subpath = f"{current_path}/{name}" if current_path else name
                subtree_oid = self._build_tree_recursive(subtree, value, deletions, subpath)
                tree_builder.insert(name, subtree_oid, pygit2.GIT_FILEMODE_TREE)
            else:
                # It's a file - value is the blob_oid
                tree_builder.insert(name, value, pygit2.GIT_FILEMODE_BLOB)

        return tree_builder.write()

    def commit_tree(
        self,
        tree_oid: pygit2.Oid,
        message: str,
        branch_name: str,
        author_name: str = "Forge AI",
        author_email: str = "ai@forge.dev",
        commit_type: CommitType = CommitType.MAJOR,
    ) -> pygit2.Oid:
        """
        Create a commit from a tree on a specific branch with smart amending.

        Handles commit type logic:
        - PREPARE: Merges with next major commit or concatenates with other prepare commits
        - FOLLOW_UP: Amends previous major commit
        - MAJOR: Standalone commit, automatically absorbs any PREPARE commits

        Args:
            tree_oid: OID of the tree to commit
            message: Commit message (without type prefix)
            branch_name: Branch to commit to
            author_name: Author name
            author_email: Author email
            commit_type: Type of commit for smart amending

        Returns:
            OID of the new commit
        """
        # Get parent commit
        parent_commit = self.get_branch_head(branch_name)

        # Parse parent commit type
        parent_type, parent_message = parse_commit_type(parent_commit.message)

        # Handle commit type logic
        if commit_type == CommitType.FOLLOW_UP:
            # Amend previous major commit
            if parent_type == CommitType.MAJOR:
                # Amend the major commit, keeping its message
                return self.amend_commit(
                    branch_name,
                    {},  # No additional file changes, tree already built
                    new_message=parent_message,
                    new_tree_oid=tree_oid,
                )
            elif parent_type == CommitType.PREPARE:
                # Find the last major commit before prepare commits
                # For now, just create a new commit - this is an edge case
                pass

        elif commit_type == CommitType.PREPARE:
            # If parent is also PREPARE, concatenate messages
            if parent_type == CommitType.PREPARE:
                combined_message = f"{parent_message}\n{message}"
                # Re-add [prepare] prefix since parse_commit_type stripped it
                formatted_combined = format_commit_message(CommitType.PREPARE, combined_message)
                return self.amend_commit(
                    branch_name,
                    {},  # No additional file changes, tree already built
                    new_message=formatted_combined,
                    new_tree_oid=tree_oid,
                )

        elif commit_type == CommitType.MAJOR:
            # Check if there are PREPARE commits to absorb
            absorbed_oid = self.absorb_prepare_commits(branch_name, message)
            if absorbed_oid:
                # PREPARE commits were absorbed, now amend with our tree
                return self.amend_commit(
                    branch_name, {}, new_message=message, new_tree_oid=tree_oid
                )
            # No PREPARE commits, fall through to create normal commit

        # Default: create new commit with formatted message
        formatted_message = format_commit_message(commit_type, message)

        # Create signature
        signature = pygit2.Signature(author_name, author_email)

        # Create commit
        commit_oid = self.repo.create_commit(
            f"refs/heads/{branch_name}",  # Update branch ref
            signature,  # author
            signature,  # committer
            formatted_message,
            tree_oid,
            [parent_commit.id],  # parents
        )

        return commit_oid

    def amend_commit(
        self,
        branch_name: str,
        additional_changes: dict[str, str],
        new_message: str | None = None,
        new_tree_oid: pygit2.Oid | None = None,
    ) -> pygit2.Oid:
        """
        Amend the HEAD commit on a branch with additional changes.

        Args:
            branch_name: Branch to amend
            additional_changes: Dict of filepath -> new_content to add to commit
            new_message: Optional new commit message (keeps original if None)
            new_tree_oid: Optional pre-built tree OID (if provided, ignores additional_changes)

        Returns:
            OID of the new (amended) commit
        """
        # Get current HEAD commit
        head_commit = self.get_branch_head(branch_name)

        # Get parent(s) of HEAD
        parents = [p.id for p in head_commit.parents]

        # Use provided tree or build new one
        if new_tree_oid is not None:
            tree_oid = new_tree_oid
        elif additional_changes:
            # Build nested structure and create tree properly
            nested_changes: dict = {}
            for filepath, content in additional_changes.items():
                blob_oid = self.repo.create_blob(content.encode("utf-8"))
                parts = filepath.split("/")
                current = nested_changes
                for part in parts[:-1]:
                    if part not in current or not isinstance(current[part], dict):
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = blob_oid

            tree_oid = self._build_tree_recursive(head_commit.tree, nested_changes, set(), "")
        else:
            # No changes, use existing tree
            tree_oid = head_commit.tree.id

        # Use original message if no new message provided
        message = new_message if new_message is not None else head_commit.message

        # Create signature (preserve original author, update committer)
        author = head_commit.author
        committer = pygit2.Signature("Forge AI", "ai@forge.dev")

        # Create new commit with same parents as original
        # Don't update ref yet - create_commit with ref expects first parent to be current tip
        new_commit_oid = self.repo.create_commit(
            None,  # Don't update any ref yet
            author,
            committer,
            message,
            tree_oid,
            parents,  # Same parents as original commit
        )

        # Now force-update the branch to point to the new commit
        branch = self.repo.branches[branch_name]
        branch.set_target(new_commit_oid)

        return new_commit_oid

    def absorb_prepare_commits(self, branch_name: str, major_message: str) -> pygit2.Oid | None:
        """
        Absorb all [prepare] commits into a new major commit.

        Walks back from HEAD, collecting all consecutive [prepare] commits,
        then creates a single major commit with the given message.

        Args:
            branch_name: Branch to operate on
            major_message: Message for the major commit

        Returns:
            OID of new major commit, or None if no prepare commits found
        """
        head_commit = self.get_branch_head(branch_name)

        # Walk back collecting prepare commits
        prepare_commits: list[pygit2.Commit] = []
        current = head_commit

        while True:
            commit_type, _ = parse_commit_type(current.message)
            if commit_type != CommitType.PREPARE:
                break

            prepare_commits.append(current)

            # Move to parent
            if not current.parents:
                break
            current = current.parents[0]

        if not prepare_commits:
            return None

        # The tree we want is from the HEAD (latest prepare commit)
        tree_oid = head_commit.tree.id

        # Parent is the commit before the first prepare commit
        parent_commit = current

        # Create signature
        signature = pygit2.Signature("Forge AI", "ai@forge.dev")

        # Create new major commit
        new_commit_oid = self.repo.create_commit(
            None,  # Don't update ref yet
            signature,
            signature,
            major_message,  # Use major message, not prepare messages
            tree_oid,
            [parent_commit.id],
        )

        # Force-update branch
        branch = self.repo.branches[branch_name]
        branch.set_target(new_commit_oid)

        return new_commit_oid

    def get_file_blob_oid(self, filepath: str, branch_name: str | None = None) -> str:
        """Get the blob OID (content hash) for a file"""
        if branch_name:
            commit = self.get_branch_head(branch_name)
        else:
            commit = self.repo.head.peel(pygit2.Commit)

        entry = commit.tree[filepath]
        return str(entry.id)

    def get_all_files(self, branch_name: str | None = None) -> list[str]:
        """Get list of all files in repository"""
        if branch_name:
            commit = self.get_branch_head(branch_name)
        else:
            commit = self.repo.head.peel(pygit2.Commit)

        files: list[str] = []

        def walk_tree(tree: pygit2.Tree, path: str = "") -> None:
            for entry in tree:
                assert entry.name is not None, "Tree entry name should never be None"
                entry_path = f"{path}/{entry.name}" if path else entry.name
                obj = self.repo[entry.id]
                assert isinstance(obj, (pygit2.Tree, pygit2.Blob)), (
                    f"Unexpected git object type: {type(obj)}"
                )
                if isinstance(obj, pygit2.Tree):
                    # Recursively walk subdirectories
                    walk_tree(obj, entry_path)
                else:
                    files.append(entry_path)

        walk_tree(commit.tree)
        return files
