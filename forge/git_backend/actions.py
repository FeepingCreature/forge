"""
Git actions with undo support.

Each action is a class with perform() and undo() methods.
Actions are recorded in the session's action log for undo capability.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import pygit2

from forge.git_backend.repository import ForgeRepository


class GitAction(ABC):
    """Base class for undoable git actions."""

    @abstractmethod
    def perform(self) -> None:
        """Execute the action."""
        ...

    @abstractmethod
    def undo(self) -> None:
        """Undo the action."""
        ...

    @abstractmethod
    def description(self) -> str:
        """Human-readable description of the action."""
        ...


@dataclass
class MergeAction(GitAction):
    """
    Merge a commit into a target branch.

    The merge creates a new commit on the target branch with two parents:
    the target branch's previous HEAD and the source commit.
    """

    repo: ForgeRepository
    source_oid: str  # Commit to merge
    target_branch: str  # Branch to merge into
    result_oid: str = ""  # Filled after perform()
    previous_target_oid: str = ""  # For undo
    timestamp: datetime = field(default_factory=datetime.now)

    def perform(self) -> None:  # noqa: PLR0911
        """Execute the merge."""
        # Store previous target for undo
        target_commit = self.repo.get_branch_head(self.target_branch)
        self.previous_target_oid = str(target_commit.id)

        source_commit = self.repo.repo[self.source_oid]
        if not isinstance(source_commit, pygit2.Commit):
            raise ValueError(f"Source {self.source_oid} is not a commit")

        # Delete .forge/session.json from the merge to avoid conflicts
        # We do a three-way merge of trees, excluding session.json
        merge_result = self.repo.repo.merge_trees(
            ancestor=self.repo.repo.merge_base(target_commit.id, source_commit.id),
            ours=target_commit.tree,
            theirs=source_commit.tree,
        )

        if merge_result.conflicts:
            # Collect conflicting file paths
            conflict_paths: list[str] = []
            for conflict in merge_result.conflicts:
                # conflict is a tuple of (ancestor, ours, theirs) IndexEntry objects
                for entry in conflict:
                    if entry is not None:
                        conflict_paths.append(entry.path)
            # Deduplicate
            conflict_paths = sorted(set(conflict_paths))

            # Print to console
            print(f"Merge conflict in {len(conflict_paths)} file(s):")
            for path in conflict_paths:
                print(f"  - {path}")

            # Raise with file list
            files_str = ", ".join(conflict_paths[:5])
            if len(conflict_paths) > 5:
                files_str += f", ... ({len(conflict_paths) - 5} more)"
            raise ValueError(f"Merge has conflicts in: {files_str}")

        # Build the merged tree, but remove .forge/session.json if present
        tree_oid = self._build_merge_tree_without_session(merge_result)

        # Create merge commit
        signature = pygit2.Signature("Forge AI", "ai@forge.dev")

        # Get source branch name for message if available
        source_branch = self._find_branch_for_commit(self.source_oid)
        if source_branch:
            message = f"Merge branch '{source_branch}' into {self.target_branch}"
        else:
            message = f"Merge commit {self.source_oid[:7]} into {self.target_branch}"

        commit_oid = self.repo.repo.create_commit(
            f"refs/heads/{self.target_branch}",
            signature,
            signature,
            message,
            tree_oid,
            [target_commit.id, source_commit.id],  # Two parents for merge
        )

        self.result_oid = str(commit_oid)

    def _build_merge_tree_without_session(self, merge_result: pygit2.Index) -> pygit2.Oid:
        """Build tree from merge result, removing .forge/session.json."""
        # Write the index to a tree
        tree_oid = merge_result.write_tree(self.repo.repo)

        # Check if .forge/session.json exists in the tree
        tree = self.repo.repo.get(tree_oid)
        assert isinstance(tree, pygit2.Tree)

        try:
            forge_entry = tree[".forge"]
            forge_tree = self.repo.repo.get(forge_entry.id)
            if isinstance(forge_tree, pygit2.Tree):
                try:
                    forge_tree["session.json"]
                    # session.json exists, need to remove it
                    return self._remove_session_from_tree(tree)
                except KeyError:
                    pass
        except KeyError:
            pass

        # No session.json to remove
        return tree_oid

    def _remove_session_from_tree(self, tree: pygit2.Tree) -> pygit2.Oid:
        """Remove .forge/session.json from a tree."""
        # Build new .forge subtree without session.json
        forge_entry = tree[".forge"]
        forge_tree = self.repo.repo.get(forge_entry.id)
        assert isinstance(forge_tree, pygit2.Tree)

        forge_builder = self.repo.repo.TreeBuilder(forge_tree)
        forge_builder.remove("session.json")
        new_forge_oid = forge_builder.write()

        # Build new root tree with updated .forge
        root_builder = self.repo.repo.TreeBuilder(tree)
        root_builder.insert(".forge", new_forge_oid, pygit2.GIT_FILEMODE_TREE)
        return root_builder.write()

    def _find_branch_for_commit(self, oid: str) -> str | None:
        """Find a branch name that points to this commit."""
        for branch_name in self.repo.repo.branches.local:
            branch = self.repo.repo.branches[branch_name]
            if str(branch.peel(pygit2.Commit).id) == oid:
                return branch_name
        return None

    def undo(self) -> None:
        """Undo the merge by resetting the branch to its previous HEAD."""
        if not self.previous_target_oid:
            raise ValueError("Cannot undo: no previous state recorded")

        # Reset branch to previous commit
        branch = self.repo.repo.branches[self.target_branch]
        branch.set_target(pygit2.Oid(hex=self.previous_target_oid))

    def description(self) -> str:
        """Human-readable description."""
        source_branch = self._find_branch_for_commit(self.source_oid)
        if source_branch:
            return f"Merge '{source_branch}' into '{self.target_branch}'"
        return f"Merge {self.source_oid[:7]} into '{self.target_branch}'"


def check_merge_clean(repo: ForgeRepository, source_oid: str, target_branch: str) -> bool:
    """
    Quick check if a merge would be clean (no conflicts).

    Returns True if merge would succeed without conflicts.
    """
    target_commit = repo.get_branch_head(target_branch)
    source_commit: pygit2.Commit = repo.repo[source_oid]  # type: ignore[assignment]

    # Find merge base
    merge_base = repo.repo.merge_base(target_commit.id, source_commit.id)
    if not merge_base:
        # No common ancestor - could still merge, but risky
        return False

    # Check if already merged (source is ancestor of target or vice versa)
    if merge_base == source_commit.id:
        # Source is already in target's history
        return True
    if merge_base == target_commit.id:
        # Target is behind source - fast-forward possible
        return True

    # Do a merge tree check
    merge_result = repo.repo.merge_trees(
        ancestor=merge_base,
        ours=target_commit.tree,
        theirs=source_commit.tree,
    )

    return not merge_result.conflicts


class GitActionLog:
    """In-memory log of git actions for undo capability."""

    def __init__(self) -> None:
        self._actions: list[GitAction] = []

    def record(self, action: GitAction) -> None:
        """Record an action that was performed."""
        self._actions.append(action)

    def can_undo(self) -> bool:
        """Check if there are actions to undo."""
        return len(self._actions) > 0

    def undo_last(self) -> GitAction | None:
        """Undo the last action and return it."""
        if not self._actions:
            return None
        action = self._actions.pop()
        action.undo()
        return action

    def get_actions(self) -> list[GitAction]:
        """Get all recorded actions."""
        return list(self._actions)

    def clear(self) -> None:
        """Clear the action log."""
        self._actions.clear()
