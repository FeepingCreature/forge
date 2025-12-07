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
    
    def __init__(self, tools_dir: str = "./tools"):
        self.tools_dir = Path(tools_dir)
        self.tools_dir.mkdir(exist_ok=True)
        
    def discover_tools(self) -> List[Dict]:
        """Discover all tools and get their schemas"""
        tools = []
        
        if not self.tools_dir.exists():
            return tools
            
        for tool_file in self.tools_dir.iterdir():
            if tool_file.is_file() and os.access(tool_file, os.X_OK):
                schema = self._get_tool_schema(tool_file)
                if schema:
                    tools.append(schema)
                    
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
        """Execute a tool with given arguments"""
        tool_path = self.tools_dir / tool_name
        
        if not tool_path.exists():
            return {"error": f"Tool {tool_name} not found"}
            
        try:
            result = subprocess.run(
                [str(tool_path)],
                input=json.dumps(args),
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return json.loads(result.stdout)
            else:
                return {"error": result.stderr}
        except Exception as e:
            return {"error": str(e)}
