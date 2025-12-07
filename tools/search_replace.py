#!/usr/bin/env python3
"""
SEARCH/REPLACE tool for making code edits
"""

import sys
import json
import re


def get_schema():
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "search_replace",
            "description": "Make a SEARCH/REPLACE edit to a file",
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


def execute(args):
    """Execute the search/replace operation"""
    filepath = args.get("filepath")
    search = args.get("search")
    replace = args.get("replace")
    
    if not all([filepath, search is not None, replace is not None]):
        return {"success": False, "error": "Missing required arguments"}
    
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            
        if search not in content:
            return {"success": False, "error": "Search text not found in file"}
            
        # Replace first occurrence
        new_content = content.replace(search, replace, 1)
        
        with open(filepath, 'w') as f:
            f.write(new_content)
            
        return {"success": True, "message": f"Replaced in {filepath}"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--schema":
        print(json.dumps(get_schema()))
    else:
        # Read JSON input from stdin
        input_data = sys.stdin.read()
        args = json.loads(input_data)
        result = execute(args)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
