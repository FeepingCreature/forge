"""Tests for summary exclusion pattern matching (gitignore-style)."""

import pytest

from forge.ui.summary_exclusions_dialog import matches_pattern


class TestMatchesPattern:
    """Test gitignore-style pattern matching."""

    # --- Directory patterns ---

    def test_dir_pattern_matches_files_under_dir(self):
        """folder/ should match files under that folder anywhere."""
        assert matches_pattern("node_modules/foo.js", "node_modules/")
        assert matches_pattern("node_modules/bar/baz.js", "node_modules/")

    def test_dir_pattern_matches_nested(self):
        """folder/ should match folder anywhere in path."""
        assert matches_pattern("src/node_modules/foo.js", "node_modules/")
        assert matches_pattern("a/b/node_modules/c/d.js", "node_modules/")

    def test_dir_pattern_no_match_partial_name(self):
        """folder/ should not match partial directory names."""
        assert not matches_pattern("node_modules_backup/foo.js", "node_modules/")
        assert not matches_pattern("my_node_modules/foo.js", "node_modules/")

    def test_anchored_dir_pattern_root_only(self):
        """/folder/ should only match at root."""
        assert matches_pattern("build/output.js", "/build/")
        assert not matches_pattern("src/build/output.js", "/build/")

    # --- Extension patterns ---

    def test_extension_pattern_matches_anywhere(self):
        """*.ext should match that extension anywhere."""
        assert matches_pattern("foo.min.js", "*.min.js")
        assert matches_pattern("src/bar.min.js", "*.min.js")
        assert matches_pattern("a/b/c/baz.min.js", "*.min.js")

    def test_extension_pattern_no_match_different_ext(self):
        """*.ext should not match different extensions."""
        assert not matches_pattern("foo.js", "*.min.js")
        assert not matches_pattern("foo.min.css", "*.min.js")

    def test_simple_extension(self):
        """*.ext matches simple extensions."""
        assert matches_pattern("foo.pyc", "*.pyc")
        assert matches_pattern("src/bar.pyc", "*.pyc")

    # --- Exact file patterns ---

    def test_exact_file_at_root(self):
        """Exact filename should match."""
        assert matches_pattern("package-lock.json", "package-lock.json")

    def test_exact_file_matches_anywhere(self):
        """Exact filename without slash matches anywhere."""
        assert matches_pattern("src/package-lock.json", "package-lock.json")

    def test_anchored_exact_file(self):
        """/file should only match at root."""
        assert matches_pattern("README.md", "/README.md")
        assert not matches_pattern("docs/README.md", "/README.md")

    # --- Glob patterns with ** ---

    def test_double_star_glob(self):
        """**/pattern should match at any depth."""
        assert matches_pattern("test/foo.snap", "**/test/*.snap")
        assert matches_pattern("src/test/bar.snap", "**/test/*.snap")

    def test_double_star_prefix(self):
        """**/ at start matches any leading path."""
        assert matches_pattern("foo.test.js", "**/*.test.js")
        assert matches_pattern("src/components/Button.test.js", "**/*.test.js")

    # --- Path patterns ---

    def test_path_pattern_with_wildcard(self):
        """folder/*.ext matches in specific folder."""
        assert matches_pattern("tests/__snapshots__/foo.snap", "tests/__snapshots__/*.snap")
        # Should not match in different folder
        assert not matches_pattern("other/__snapshots__/foo.snap", "tests/__snapshots__/*.snap")

    # --- Negation patterns ---

    def test_negation_pattern_returns_false(self):
        """!pattern should return False (handled by caller)."""
        assert not matches_pattern("important.log", "!important.log")

    # --- Edge cases ---

    def test_empty_pattern(self):
        """Empty pattern should not match anything."""
        assert not matches_pattern("foo.js", "")

    def test_empty_filepath(self):
        """Empty filepath edge case."""
        assert not matches_pattern("", "*.js")

    def test_hidden_files(self):
        """Hidden files (dotfiles) should match."""
        assert matches_pattern(".gitignore", ".gitignore")
        assert matches_pattern("src/.env", ".env")

    def test_hidden_directories(self):
        """Hidden directories should match."""
        assert matches_pattern(".git/config", ".git/")
        assert matches_pattern("src/.cache/foo", ".cache/")


class TestDefaultExclusions:
    """Test that default exclusions work correctly."""

    def test_node_modules(self):
        assert matches_pattern("node_modules/lodash/index.js", "node_modules/")

    def test_pycache(self):
        assert matches_pattern("src/__pycache__/module.cpython-311.pyc", "__pycache__/")

    def test_venv(self):
        assert matches_pattern(".venv/lib/python3.11/site.py", ".venv/")
        assert matches_pattern("venv/bin/python", "venv/")

    def test_git(self):
        assert matches_pattern(".git/objects/ab/cd1234", ".git/")

    def test_minified_js(self):
        assert matches_pattern("dist/bundle.min.js", "*.min.js")

    def test_lock_files(self):
        assert matches_pattern("package-lock.json", "package-lock.json")
        assert matches_pattern("yarn.lock", "yarn.lock")
        assert matches_pattern("poetry.lock", "poetry.lock")

    def test_pyc_files(self):
        assert matches_pattern("module.pyc", "*.pyc")
        assert matches_pattern("src/utils.pyo", "*.pyo")

    def test_ds_store(self):
        assert matches_pattern(".DS_Store", ".DS_Store")
        assert matches_pattern("folder/.DS_Store", ".DS_Store")