"""
Binary file detection for VFS.

Binary files are excluded from:
- File listings (list_files)
- Repository summaries
- Context loading

They are still included when materializing to disk for tests/commands.
"""

# Common binary file extensions that should never be loaded as text
BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".svg",
        ".tiff",
        ".tif",
        ".psd",
        ".ai",
        ".eps",
        # Audio
        ".mp3",
        ".wav",
        ".ogg",
        ".flac",
        ".aac",
        ".wma",
        ".m4a",
        # Video
        ".mp4",
        ".avi",
        ".mkv",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        # Archives
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".jar",
        ".war",
        ".ear",
        # Executables and libraries
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".o",
        ".a",
        ".lib",
        ".pyc",
        ".pyo",
        ".class",
        ".wasm",
        # Documents (binary formats)
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
        # Fonts
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        # Data files
        ".db",
        ".sqlite",
        ".sqlite3",
        ".pickle",
        ".pkl",
        ".npy",
        ".npz",
        ".h5",
        ".hdf5",
        ".parquet",
        # Misc
        ".iso",
        ".img",
        ".dmg",
        ".deb",
        ".rpm",
        ".msi",
        ".apk",
        ".ipa",
        # Game engines (Unreal)
        ".uasset",
        ".umap",
        ".uexp",
        ".ubulk",
        # Debug/build artifacts
        ".pdb",
        ".sym",
        ".debug",
        ".exp",
        ".res",
        ".pch",
        ".gch",
    }
)


def is_binary_file(filepath: str) -> bool:
    """Check if a file should be treated as binary based on extension.

    Args:
        filepath: Path to check (just needs extension)

    Returns:
        True if file appears to be binary
    """
    # Get extension (lowercase for comparison)
    dot_idx = filepath.rfind(".")
    if dot_idx == -1:
        return False

    ext = filepath[dot_idx:].lower()
    return ext in BINARY_EXTENSIONS
