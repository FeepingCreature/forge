"""
Tool manager for discovering and executing tools
"""

import os
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Optional


class ToolManager:
    """Manages tools available to the LLM"""
    
    def __init__(self, repo=None, branch_name: Optional[str] = None, tools_dir: str = "./tools"):
        self.tools_dir = Path(tools_dir)
        self.tools_dir.mkdir(exist_ok=True)
        self.repo = repo
        self.branch_name = branch_name
        
        # Pending changes accumulated during AI turn
        self.pending_changes = {}
        
        # Schema cache
        self._schema_cache = {}
        
    def discover_tools(self, force_refresh: bool = False) -> List[Dict]:
        """Discover all tools and get their schemas"""
        if not force_refresh and self._schema_cache:
            return list(self._schema_cache.values())
        
        tools = []
        self._schema_cache = {}
        
        if not self.tools_dir.exists():
            return tools
            
        for tool_file in self.tools_dir.iterdir():
            if tool_file.is_file() and os.access(tool_file, os.X_OK):
                schema = self._get_tool_schema(tool_file)
                if schema:
                    tools.append(schema)
                    self._schema_cache[tool_file.name] = schema
                    
        return tools
        
    def _get_tool_schema(self, tool_path: Path) -> Optional[Dict]:
        """Get tool schema by calling tool with --schema"""
        try:
            result = subprocess.run(
                [str(tool_path), "--schema"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                return json.loads(result.stdout)
        except Exception as e:
            print(f"Error getting schema for {tool_path}: {e}")
            
        return None
        
    def execute_tool(self, tool_name: str, args: Dict) -> Dict:
        """Execute a tool with given arguments (git-aware)"""
        tool_path = self.tools_dir / tool_name
        
        if not tool_path.exists():
            return {"error": f"Tool {tool_name} not found"}
        
        # Build context for tool
        context = {}
        
        # If tool needs file content, get it from git
        if 'filepath' in args and self.repo and self.branch_name:
            filepath = args['filepath']
            current_content = self.repo.get_file_content(filepath, self.branch_name)
            
            # Check if we have pending changes for this file
            if filepath in self.pending_changes:
                current_content = self.pending_changes[filepath]
            
            context['current_content'] = current_content
        
        # Add context to args
        tool_input = {
            'args': args,
            'context': context
        }
            
        try:
            result = subprocess.run(
                [str(tool_path)],
                input=json.dumps(tool_input),
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                tool_result = json.loads(result.stdout)
                
                # If tool returned new content, accumulate it
                if 'new_content' in tool_result and 'filepath' in args:
                    self.pending_changes[args['filepath']] = tool_result['new_content']
                
                return tool_result
            else:
                return {"error": result.stderr}
        except Exception as e:
            return {"error": str(e)}
    
    def get_pending_changes(self) -> Dict[str, str]:
        """Get all pending changes accumulated during AI turn"""
        return self.pending_changes.copy()
    
    def clear_pending_changes(self):
        """Clear pending changes after commit"""
        self.pending_changes = {}
