"""
Git repository management using pygit2
"""

from pathlib import Path

import pygit2

from .commit_types import CommitType, format_commit_message, parse_commit_type


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

    def create_tree_from_changes(self, base_branch: str, changes: dict[str, str]) -> pygit2.Oid:
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

        # Start with base tree
        base_tree = base_commit.tree

        # Build new tree with changes
        tree_builder = self.repo.TreeBuilder(base_tree)

        for filepath, content in changes.items():
            # Create blob for new content
            blob_oid = self.repo.create_blob(content.encode("utf-8"))

            # Add to tree (handles nested paths)
            self._add_to_tree(tree_builder, filepath, blob_oid, base_tree)

        # Write the tree
        tree_oid = tree_builder.write()
        return tree_oid

    def _add_to_tree(
        self,
        tree_builder: pygit2.TreeBuilder,
        filepath: str,
        blob_oid: pygit2.Oid,
        base_tree: pygit2.Tree | None,
    ) -> None:
        """Add a file to tree, handling nested directories"""
        parts = filepath.split("/")

        if len(parts) == 1:
            # Simple file in root
            tree_builder.insert(parts[0], blob_oid, pygit2.GIT_FILEMODE_BLOB)
        else:
            # Handle nested paths by recursively building subtrees
            dir_name = parts[0]
            rest_path = "/".join(parts[1:])

            # Get or create subtree
            subtree: pygit2.Tree | None = None
            if base_tree:
                try:
                    subtree_entry = base_tree[dir_name]
                    subtree_obj = self.repo[subtree_entry.id]
                    assert isinstance(subtree_obj, pygit2.Tree)
                    subtree = subtree_obj
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
        else:
            # Create new tree with additional changes on top of HEAD's tree
            tree_builder = self.repo.TreeBuilder(head_commit.tree)

            for filepath, content in additional_changes.items():
                # Create blob for new content
                blob_oid = self.repo.create_blob(content.encode("utf-8"))

                # Add to tree (handles nested paths)
                self._add_to_tree(tree_builder, filepath, blob_oid, head_commit.tree)

            # Write the new tree
            tree_oid = tree_builder.write()

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
                assert isinstance(obj, (pygit2.Tree, pygit2.Blob)), f"Unexpected git object type: {type(obj)}"
                if isinstance(obj, pygit2.Tree):
                    # Recursively walk subdirectories
                    walk_tree(obj, entry_path)
                else:
                    files.append(entry_path)

        walk_tree(commit.tree)
        return files
