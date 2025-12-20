"""
Tool to set a project's license.

This exists because some LLM providers block license text in responses
(yes, really). We bundle the license files and write them directly.
"""

from pathlib import Path
from typing import Any

from forge.vfs.base import VFS

# Available licenses and their filenames
AVAILABLE_LICENSES = {
    "GPL-3.0": "GPL-3.0.txt",
    "LGPL-3.0": "LGPL-3.0.txt",
    "AGPL-3.0": "AGPL-3.0.txt",
    "MIT": "MIT.txt",
    "Apache-2.0": "Apache-2.0.txt",
    "BSD-3-Clause": "BSD-3-Clause.txt",
    "BSD-2-Clause": "BSD-2-Clause.txt",
    "MPL-2.0": "MPL-2.0.txt",
    "Unlicense": "Unlicense.txt",
}


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    license_list = ", ".join(AVAILABLE_LICENSES.keys())
    return {
        "type": "function",
        "function": {
            "name": "set_license",
            "description": "Set the project's license by writing a LICENSE file. Used to bypass API content filters on"
            + f' "viral" licenses. Do not add without user consent, may contain placeholders. Available licenses: {license_list}',
            "parameters": {
                "type": "object",
                "properties": {
                    "license": {
                        "type": "string",
                        "description": f"License identifier. One of: {license_list}",
                        "enum": list(AVAILABLE_LICENSES.keys()),
                    },
                },
                "required": ["license"],
            },
        },
    }


def execute(vfs: VFS, args: dict[str, Any]) -> dict[str, Any]:
    """Write the LICENSE file to the repository root."""
    license_id = args["license"]

    if license_id not in AVAILABLE_LICENSES:
        return {
            "success": False,
            "error": f"Unknown license: {license_id}. Available: {', '.join(AVAILABLE_LICENSES.keys())}",
        }

    # Find the license file in our bundled licenses
    licenses_dir = Path(__file__).parent.parent.parent / "licenses"
    license_file = licenses_dir / AVAILABLE_LICENSES[license_id]

    if not license_file.exists():
        return {
            "success": False,
            "error": f"License file not found: {license_file}. This is a bug in Forge.",
        }

    # Read the license text
    license_text = license_file.read_text()

    # Write to LICENSE in repo root
    vfs.write_file("LICENSE", license_text)

    return {
        "success": True,
        "message": f"Set license to {license_id}",
        "filepath": "LICENSE",
    }
