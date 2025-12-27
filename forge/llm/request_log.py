"""
Request logging for LLM API calls.

Saves request/response pairs to /tmp for debugging and cost analysis.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Directory for request dumps
DEBUG_DIR = Path("/tmp/forge_debug")


@dataclass
class RequestLogEntry:
    """A single logged request/response pair."""

    request_file: str
    response_file: str | None = None
    timestamp: float = field(default_factory=time.time)
    model: str = ""
    streaming: bool = False
    actual_cost: float | None = None
    generation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_file": self.request_file,
            "response_file": self.response_file,
            "timestamp": self.timestamp,
            "model": self.model,
            "streaming": self.streaming,
            "actual_cost": self.actual_cost,
            "generation_id": self.generation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RequestLogEntry":
        return cls(
            request_file=data["request_file"],
            response_file=data.get("response_file"),
            timestamp=data.get("timestamp", 0),
            model=data.get("model", ""),
            streaming=data.get("streaming", False),
            actual_cost=data.get("actual_cost"),
            generation_id=data.get("generation_id"),
        )

    @classmethod
    def from_files(cls, request_file: str, response_file: str | None) -> "RequestLogEntry | None":
        """Reconstruct an entry from saved file paths.

        Returns None if the request file doesn't exist (files may be cleaned up).
        """
        request_path = Path(request_file)
        if not request_path.exists():
            return None

        # Parse request to get model and streaming info
        try:
            request_data = json.loads(request_path.read_text())
            model = request_data.get("model", "")
            streaming = request_data.get("stream", False)
        except (json.JSONDecodeError, OSError):
            model = ""
            streaming = False

        # Get timestamp from filename (e.g., request_1234567890123.json)
        try:
            timestamp_ms = int(request_path.stem.split("_")[-1])
            timestamp = timestamp_ms / 1000.0
        except (ValueError, IndexError):
            timestamp = request_path.stat().st_mtime

        # Check if response file exists
        if response_file and not Path(response_file).exists():
            response_file = None

        return cls(
            request_file=request_file,
            response_file=response_file,
            timestamp=timestamp,
            model=model,
            streaming=streaming,
        )


class RequestLog:
    """Tracks all request/response pairs for a session."""

    def __init__(self) -> None:
        self.entries: list[RequestLogEntry] = []
        DEBUG_DIR.mkdir(exist_ok=True)

    def log_request(
        self,
        payload: dict[str, Any],
        model: str,
        streaming: bool = False,
    ) -> RequestLogEntry:
        """Log a request and return the entry for later update with response."""
        timestamp = int(time.time() * 1000)
        prefix = "request_stream" if streaming else "request"
        request_file = str(DEBUG_DIR / f"{prefix}_{timestamp}.json")

        # Write request
        Path(request_file).write_text(json.dumps(payload, indent=2))

        entry = RequestLogEntry(
            request_file=request_file,
            model=model,
            streaming=streaming,
            timestamp=time.time(),
        )
        self.entries.append(entry)
        return entry

    def log_response(
        self,
        entry: RequestLogEntry,
        response: dict[str, Any],
        actual_cost: float | None = None,
        generation_id: str | None = None,
    ) -> None:
        """Log the response for a previously logged request."""
        # Generate response filename from request filename
        request_path = Path(entry.request_file)
        response_file = str(
            request_path.parent / f"response_{request_path.stem.split('_', 1)[1]}.json"
        )

        # Write response
        Path(response_file).write_text(json.dumps(response, indent=2))

        entry.response_file = response_file
        entry.actual_cost = actual_cost
        entry.generation_id = generation_id

    def get_entries(self) -> list[RequestLogEntry]:
        """Get all logged entries."""
        return self.entries

    def clear(self) -> None:
        """Clear all entries (does not delete files)."""
        self.entries = []


# Global instance for the current session
REQUEST_LOG = RequestLog()
