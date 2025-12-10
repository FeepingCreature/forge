#!/usr/bin/env python3
"""
SEARCH/REPLACE tool for making code edits (git-aware)
"""

import sys
import json


def get_schema():
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "search_replace",
            "description": "Make a SEARCH/REPLACE edit to a file. Works on git content, not filesystem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to edit"
                    },
                    "search": {
                        "type": "string",
                        "description": "Exact text to search for"
                    },
                    "replace": {
                        "type": "string",
                        "description": "Text to replace with"
                    }
                },
                "required": ["filepath", "search", "replace"]
            }
        }
    }


def execute(tool_input):
    """Execute the search/replace operation on git content"""
    args = tool_input.get('args', {})
    context = tool_input.get('context', {})
    
    filepath = args.get("filepath")
    search = args.get("search")
    replace = args.get("replace")
    
    if not all([filepath, search is not None, replace is not None]):
        return {"success": False, "error": "Missing required arguments"}
    
    # Get current content from context (provided by ToolManager from git)
    content = context['current_content']
    
    if search not in content:
        return {"success": False, "error": "Search text not found in file"}
    
    # Replace first occurrence
    new_content = content.replace(search, replace, 1)
    
    return {
        "success": True,
        "message": f"Replaced in {filepath}",
        "new_content": new_content
    }


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--schema":
        print(json.dumps(get_schema()))
    else:
        # Read JSON input from stdin
        input_data = sys.stdin.read()
        tool_input = json.loads(input_data)
        result = execute(tool_input)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
