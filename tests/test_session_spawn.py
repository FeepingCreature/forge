"""Tests for session spawn/resume/wait/merge flow."""

import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from forge.session.registry import SessionRegistry
from forge.session.runner import SessionRunner, SessionState
from forge.tools.context import ToolContext, get_tool_api_version


class TestSessionRegistry:
    """Tests for SessionRegistry."""
    
    def test_register_and_get(self):
        """Test registering and retrieving a runner."""
        registry = SessionRegistry()
        
        mock_runner = MagicMock(spec=SessionRunner)
        mock_runner.state = SessionState.IDLE
        mock_runner._parent_session = None
        mock_runner._child_sessions = set()
        mock_runner._yield_message = None
        mock_runner.state_changed = MagicMock()
        mock_runner.state_changed.connect = MagicMock()
        
        registry.register("test-branch", mock_runner)
        
        assert registry.get("test-branch") is mock_runner
        assert "test-branch" in registry.get_all()
    
    def test_unregister(self):
        """Test unregistering a runner."""
        registry = SessionRegistry()
        
        mock_runner = MagicMock(spec=SessionRunner)
        mock_runner.state_changed = MagicMock()
        mock_runner.state_changed.connect = MagicMock()
        
        registry.register("test-branch", mock_runner)
        registry.unregister("test-branch")
        
        assert registry.get("test-branch") is None
    
    def test_get_session_states(self):
        """Test getting all session states."""
        registry = SessionRegistry()
        
        mock_runner = MagicMock(spec=SessionRunner)
        mock_runner.state = SessionState.RUNNING
        mock_runner._parent_session = None
        mock_runner._child_sessions = {"child-branch"}
        mock_runner._yield_message = None
        mock_runner.state_changed = MagicMock()
        mock_runner.state_changed.connect = MagicMock()
        
        registry.register("parent-branch", mock_runner)
        
        states = registry.get_session_states()
        assert "parent-branch" in states
        assert states["parent-branch"]["state"] == SessionState.RUNNING
        assert states["parent-branch"]["has_children"] is True
        assert "child-branch" in states["parent-branch"]["children"]
    
    def test_parent_child_tracking(self):
        """Test parent/child relationship tracking."""
        registry = SessionRegistry()
        
        # Create parent
        parent_runner = MagicMock(spec=SessionRunner)
        parent_runner.state = SessionState.WAITING_CHILDREN
        parent_runner._parent_session = None
        parent_runner._child_sessions = {"child-branch"}
        parent_runner._yield_message = "Waiting for child"
        parent_runner.state_changed = MagicMock()
        parent_runner.state_changed.connect = MagicMock()
        
        # Create child
        child_runner = MagicMock(spec=SessionRunner)
        child_runner.state = SessionState.IDLE
        child_runner._parent_session = "parent-branch"
        child_runner._child_sessions = set()
        child_runner._yield_message = "Task complete"
        child_runner.state_changed = MagicMock()
        child_runner.state_changed.connect = MagicMock()
        
        registry.register("parent-branch", parent_runner)
        registry.register("child-branch", child_runner)
        
        # Check children states
        children_states = registry.get_children_states("parent-branch")
        assert "child-branch" in children_states
        assert children_states["child-branch"] == SessionState.IDLE


class TestToolApiVersionDetection:
    """Tests for tool API version detection via type annotations."""
    
    def test_detects_v1_no_annotation(self):
        """Test that no annotation defaults to v1."""
        def execute(vfs, args):
            pass
        assert get_tool_api_version(execute) == 1
    
    def test_detects_v1_vfs_annotation(self):
        """Test that VFS annotation is detected as v1."""
        from forge.vfs.base import VFS
        def execute(vfs: VFS, args: dict) -> dict:
            pass
        assert get_tool_api_version(execute) == 1
    
    def test_detects_v2_toolcontext_annotation(self):
        """Test that ToolContext annotation is detected as v2."""
        def execute(ctx: ToolContext, args: dict) -> dict:
            pass
        assert get_tool_api_version(execute) == 2
    
    def test_detects_v2_string_annotation(self):
        """Test that string 'ToolContext' annotation is detected as v2."""
        def execute(ctx: "ToolContext", args: dict) -> dict:
            pass
        assert get_tool_api_version(execute) == 2


class TestWaitSessionTool:
    """Tests for wait_session tool - reads state from session files on disk."""
    
    def test_wait_returns_ready_child(self):
        """Test that wait_session returns when a child is ready (completed state in file)."""
        from forge.tools.builtin.wait_session import execute
        
        # Create mock ToolContext
        mock_vfs = MagicMock()
        mock_repo = MagicMock()
        mock_repo.repo.branches.__contains__ = lambda self, x: x in ["parent-branch", "child-1", "child-2"]
        
        ctx = ToolContext(
            vfs=mock_vfs,
            repo=mock_repo,
            branch_name="parent-branch",
        )
        
        # Child session data - child-2 is completed
        child_sessions = {
            "child-1": {
                "parent_session": "parent-branch",
                "state": "running",
                "yield_message": None,
                "task": "Task 1",
            },
            "child-2": {
                "parent_session": "parent-branch",
                "state": "completed",
                "yield_message": "I finished the task successfully!",
                "task": "Task 2",
            },
        }
        
        # Mock get_branch_vfs to return child session data
        def mock_get_branch_vfs(branch):
            child_vfs = MagicMock()
            child_vfs.read_file.return_value = json.dumps(child_sessions.get(branch, {}))
            return child_vfs
        ctx.get_branch_vfs = mock_get_branch_vfs
        
        result = execute(ctx, {"branches": ["child-1", "child-2"]})
        
        assert result["success"] is True
        assert result["branch"] == "child-2"
        assert result["state"] == "completed"
        assert result["message"] == "I finished the task successfully!"
        assert result["ready"] is True
    
    def test_wait_yields_when_no_children_ready(self):
        """Test that wait_session yields when all children still running."""
        from forge.tools.builtin.wait_session import execute
        
        mock_vfs = MagicMock()
        mock_repo = MagicMock()
        mock_repo.repo.branches.__contains__ = lambda self, x: x in ["parent-branch", "child-1"]
        
        ctx = ToolContext(
            vfs=mock_vfs,
            repo=mock_repo,
            branch_name="parent-branch",
        )
        
        child_session = {
            "parent_session": "parent-branch",
            "state": "running",
            "yield_message": None,
            "task": "Still working",
        }
        
        def mock_get_branch_vfs(branch):
            child_vfs = MagicMock()
            child_vfs.read_file.return_value = json.dumps(child_session)
            return child_vfs
        ctx.get_branch_vfs = mock_get_branch_vfs
        
        result = execute(ctx, {"branches": ["child-1"]})
        
        assert result["success"] is True
        assert result["ready"] is False
        assert result.get("_yield") is True
        assert "child-1" in result.get("_yield_message", "")


class TestResumeSessionTool:
    """Tests for resume_session tool."""
    
    def test_resume_adds_message_and_starts(self):
        """Test that resume_session adds message and signals start."""
        from forge.tools.builtin.resume_session import execute
        
        mock_vfs = MagicMock()
        mock_repo = MagicMock()
        mock_repo.repo.branches.__contains__ = lambda self, x: x == "child-branch"
        
        ctx = ToolContext(
            vfs=mock_vfs,
            repo=mock_repo,
            branch_name="parent-branch",
        )
        
        # Existing child session
        child_session = {
            "messages": [{"role": "user", "content": "Initial task"}],
            "parent_session": "parent-branch",
            "state": "idle",
        }
        
        child_vfs = MagicMock()
        child_vfs.read_file.return_value = json.dumps(child_session)
        ctx.get_branch_vfs = MagicMock(return_value=child_vfs)
        
        result = execute(ctx, {
            "branch": "child-branch",
            "message": "Continue with this feedback",
        })
        
        assert result["success"] is True
        assert result["_start_session"] == "child-branch"
        assert result["_start_message"] == "Continue with this feedback"
        
        # Verify session was updated
        child_vfs.write_file.assert_called()
        write_args = child_vfs.write_file.call_args[0]
        written_session = json.loads(write_args[1])
        assert written_session["state"] == "running"
        assert len(written_session["messages"]) == 2
        assert written_session["messages"][1]["content"] == "Continue with this feedback"


class TestSessionRegistrySignals:
    """Test signal emission from SessionRegistry."""
    
    def test_session_registered_signal(self, qtbot):
        """Test that registering a session emits signal."""
        registry = SessionRegistry()
        
        mock_runner = MagicMock(spec=SessionRunner)
        mock_runner.state_changed = MagicMock()
        mock_runner.state_changed.connect = MagicMock()
        
        with qtbot.waitSignal(registry.session_registered, timeout=1000) as blocker:
            registry.register("test-branch", mock_runner)
        
        assert blocker.args == ["test-branch"]
    
    def test_session_unregistered_signal(self, qtbot):
        """Test that unregistering a session emits signal."""
        registry = SessionRegistry()
        
        mock_runner = MagicMock(spec=SessionRunner)
        mock_runner.state_changed = MagicMock()
        mock_runner.state_changed.connect = MagicMock()
        
        registry.register("test-branch", mock_runner)
        
        with qtbot.waitSignal(registry.session_unregistered, timeout=1000) as blocker:
            registry.unregister("test-branch")
        
        assert blocker.args == ["test-branch"]