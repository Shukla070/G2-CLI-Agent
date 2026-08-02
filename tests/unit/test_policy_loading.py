"""Tests for policy loading — load_policy_block().

Verifies that discovered policy files are loaded, formatted into a
<workspace_policies> block, and that extraction failures are handled
gracefully (skipped with a warning, not a crash).
"""

from __future__ import annotations

import pytest
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock

from lantern.security.sandbox import Sandbox
from lantern.workspace import build_workspace_index, WorkspaceFile, WorkspaceIndex, FileCategory
from lantern.policy import load_policy_block


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def workspace_with_policies(tmp_path: Path) -> Path:
    """Workspace with policy files and source files."""
    # Source files
    notes = tmp_path / "research_notes"
    notes.mkdir()
    (notes / "study.txt").write_text(
        "Some research content about coastal erosion and its effects.",
        encoding="utf-8",
    )

    # Policy files
    policies = tmp_path / "policies"
    policies.mkdir()
    (policies / "embargo_policy.md").write_text(
        "# Embargo Policy\n\n"
        "Embargoed documents must not be quoted in public material.\n"
        "The coastal survey data is under strict embargo until release.",
        encoding="utf-8",
    )
    (policies / "citation_policy.md").write_text(
        "# Citation Policy\n\n"
        "All generated material must include source filenames.\n"
        "Direct quotes require attribution to the original author.",
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def workspace_no_policies(tmp_path: Path) -> Path:
    """Workspace with no policy files."""
    notes = tmp_path / "research_notes"
    notes.mkdir()
    (notes / "study.txt").write_text("Content.", encoding="utf-8")
    return tmp_path


def _real_extract(path: Path) -> str:
    """Simple text extraction for tests — just read as text."""
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestLoadPolicyBlock:
    def test_loads_single_policy(self, workspace_with_policies: Path) -> None:
        # Use a workspace with just one policy
        sandbox = Sandbox.create(workspace_with_policies)
        index = build_workspace_index(sandbox)

        result = load_policy_block(index, _real_extract)

        assert "<workspace_policies>" in result
        assert "</workspace_policies>" in result
        assert "Embargo Policy" in result

    def test_loads_multiple_policies(self, workspace_with_policies: Path) -> None:
        sandbox = Sandbox.create(workspace_with_policies)
        index = build_workspace_index(sandbox)

        result = load_policy_block(index, _real_extract)

        assert "Embargo Policy" in result
        assert "Citation Policy" in result
        assert "2 policy document(s) loaded" in result

    def test_policy_files_have_separators(self, workspace_with_policies: Path) -> None:
        sandbox = Sandbox.create(workspace_with_policies)
        index = build_workspace_index(sandbox)

        result = load_policy_block(index, _real_extract)

        # Each policy should be separated with its filename
        assert "--- policies/embargo_policy.md ---" in result
        assert "--- policies/citation_policy.md ---" in result

    def test_no_policies_returns_empty_block(self, workspace_no_policies: Path) -> None:
        sandbox = Sandbox.create(workspace_no_policies)
        index = build_workspace_index(sandbox)

        result = load_policy_block(index, _real_extract)

        assert "<workspace_policies>" in result
        assert "</workspace_policies>" in result
        assert "No policy documents found" in result

    def test_extraction_failure_is_graceful(self, workspace_with_policies: Path) -> None:
        """If extraction fails for one policy, the others should still load."""
        sandbox = Sandbox.create(workspace_with_policies)
        index = build_workspace_index(sandbox)

        call_count = 0

        def _failing_on_first(path: Path) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated extraction failure")
            return path.read_text(encoding="utf-8")

        result = load_policy_block(index, _failing_on_first)

        # Should still have the block delimiters
        assert "<workspace_policies>" in result
        assert "</workspace_policies>" in result
        # One policy should have loaded
        assert "1 policy document(s) loaded" in result
        assert "1 failed to load" in result

    def test_all_extractions_fail(self, workspace_with_policies: Path) -> None:
        sandbox = Sandbox.create(workspace_with_policies)
        index = build_workspace_index(sandbox)

        def _always_fail(path: Path) -> str:
            raise RuntimeError("Everything is broken")

        result = load_policy_block(index, _always_fail)

        assert "<workspace_policies>" in result
        assert "failed to extract text from any of them" in result

    def test_empty_policy_file_skipped(self, tmp_path: Path) -> None:
        """A policy file with empty/whitespace-only content should be skipped."""
        policies = tmp_path / "policies"
        policies.mkdir()
        (policies / "empty_policy.md").write_text("   \n  \n  ", encoding="utf-8")

        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "file.txt").write_text("Content.", encoding="utf-8")

        sandbox = Sandbox.create(tmp_path)
        index = build_workspace_index(sandbox)

        result = load_policy_block(index, _real_extract)

        # The empty policy should be skipped
        assert "failed to extract text from any of them" in result

    def test_policy_prefix_convention(self, tmp_path: Path) -> None:
        """Files with POLICY_ prefix should also be loaded."""
        (tmp_path / "POLICY_special.md").write_text(
            "# Special Policy\n\nThis is a special rule.",
            encoding="utf-8",
        )
        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "file.txt").write_text("Content.", encoding="utf-8")

        sandbox = Sandbox.create(tmp_path)
        index = build_workspace_index(sandbox)

        result = load_policy_block(index, _real_extract)

        assert "Special Policy" in result
        assert "POLICY_special.md" in result

    def test_source_files_not_included(self, workspace_with_policies: Path) -> None:
        """Source documents must NOT appear in the policy block."""
        sandbox = Sandbox.create(workspace_with_policies)
        index = build_workspace_index(sandbox)

        result = load_policy_block(index, _real_extract)

        assert "coastal erosion" not in result.lower()  # Source content
        assert "study.txt" not in result  # Source filename


class TestPolicyBlockFormat:
    def test_block_starts_with_tag(self, workspace_with_policies: Path) -> None:
        sandbox = Sandbox.create(workspace_with_policies)
        index = build_workspace_index(sandbox)

        result = load_policy_block(index, _real_extract)
        assert result.startswith("<workspace_policies>")

    def test_block_ends_with_tag(self, workspace_with_policies: Path) -> None:
        sandbox = Sandbox.create(workspace_with_policies)
        index = build_workspace_index(sandbox)

        result = load_policy_block(index, _real_extract)
        assert result.strip().endswith("</workspace_policies>")

    def test_policy_text_between_tags(self, workspace_with_policies: Path) -> None:
        sandbox = Sandbox.create(workspace_with_policies)
        index = build_workspace_index(sandbox)

        result = load_policy_block(index, _real_extract)

        # Extract content between tags
        start = result.index("<workspace_policies>") + len("<workspace_policies>")
        end = result.index("</workspace_policies>")
        content = result[start:end]

        assert "must not be quoted" in content
        assert "source filenames" in content
