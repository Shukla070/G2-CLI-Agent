"""Tests for `lantern.workspace`.

Builds real temp-directory workspaces (including hidden files, symlinks,
and nested category-overlap cases) and verifies `build_workspace_index`
classifies and excludes things correctly.
"""

from __future__ import annotations

import sys

# pyright: reportMissingImports=false
from pathlib import PurePosixPath

import pytest

from lantern.security.sandbox import Sandbox
from lantern.workspace import FileCategory, build_workspace_index


def _write(path, content="placeholder content for a test fixture file.\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def workspace(tmp_path):
    """A realistic-shaped mini workspace covering every classification case."""
    root = tmp_path

    # Source documents, across multiple folders.
    _write(root / "research_notes" / "coastal_chapter_notes.md")
    _write(root / "research_notes" / "typography_research.txt")
    _write(root / "drafts" / "coastal_chapter_draft_v1.docx")
    _write(root / "correspondence" / "author_notes_j_alvarez.txt")

    # Policy documents, both conventions, one nested (not top-level).
    _write(root / "policies" / "POLICY_embargo.md")
    _write(root / "reference_material" / "policies" / "citation_policy.md")
    _write(root / "meeting_records" / "POLICY_meeting_ground_rules.txt")

    # Output folder, nested arbitrarily.
    _write(root / "output" / "editors_note.md")
    _write(root / "output" / "drafts" / "generated_summary.md")

    # Overlap / precedence case: a policy file living inside an output tree.
    _write(root / "output" / "policies" / "POLICY_output_handling.md")

    # Hidden files/dirs — must be excluded entirely.
    _write(root / ".lantern" / "sessions" / "abc123.json", content="{}")
    _write(root / ".env", content="ANTHROPIC_API_KEY=unused-in-tests\n")

    # Non-extractable but still enumerable source file.
    _write(root / "reference_material" / "cover_photo.jpeg", content="not real jpeg bytes")

    return root


@pytest.fixture
def sandbox(workspace):
    return Sandbox.create(workspace)


class TestClassification:
    def test_ordinary_files_classified_as_source(self, sandbox):
        index = build_workspace_index(sandbox)
        source_names = {f.relative_path.as_posix() for f in index.sources}
        assert "research_notes/coastal_chapter_notes.md" in source_names
        assert "drafts/coastal_chapter_draft_v1.docx" in source_names
        assert "correspondence/author_notes_j_alvarez.txt" in source_names

    def test_policy_dir_convention_matches_at_any_depth(self, sandbox):
        index = build_workspace_index(sandbox)
        policy_names = {f.relative_path.as_posix() for f in index.policies}
        assert "policies/POLICY_embargo.md" in policy_names
        assert "reference_material/policies/citation_policy.md" in policy_names

    def test_policy_prefix_convention_matches_outside_policies_dir(self, sandbox):
        index = build_workspace_index(sandbox)
        policy_names = {f.relative_path.as_posix() for f in index.policies}
        assert "meeting_records/POLICY_meeting_ground_rules.txt" in policy_names

    def test_output_dir_convention_matches_nested(self, sandbox):
        index = build_workspace_index(sandbox)
        output_names = {f.relative_path.as_posix() for f in index.outputs}
        assert "output/editors_note.md" in output_names
        assert "output/drafts/generated_summary.md" in output_names

    def test_policy_classification_wins_over_output_on_overlap(self, sandbox):
        index = build_workspace_index(sandbox)
        overlap_path = "output/policies/POLICY_output_handling.md"
        policy_names = {f.relative_path.as_posix() for f in index.policies}
        output_names = {f.relative_path.as_posix() for f in index.outputs}
        assert overlap_path in policy_names
        assert overlap_path not in output_names


class TestExclusions:
    def test_hidden_directory_excluded_entirely(self, sandbox):
        index = build_workspace_index(sandbox)
        all_paths = {f.relative_path.as_posix() for f in index.files}
        assert not any(".lantern" in p for p in all_paths)

    def test_hidden_file_excluded_entirely(self, sandbox):
        index = build_workspace_index(sandbox)
        all_paths = {f.relative_path.as_posix() for f in index.files}
        assert ".env" not in all_paths

    @pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require admin privileges on Windows")
    def test_symlinked_file_excluded(self, workspace, sandbox):
        target = workspace / "research_notes" / "coastal_chapter_notes.md"
        (workspace / "shortcut_to_notes.md").symlink_to(target)

        index = build_workspace_index(sandbox)
        all_paths = {f.relative_path.as_posix() for f in index.files}
        assert "shortcut_to_notes.md" not in all_paths

    @pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require admin privileges on Windows")
    def test_symlinked_directory_not_recursed_into(self, workspace, sandbox, tmp_path_factory):
        outside_dir = tmp_path_factory.mktemp("outside")
        _write(outside_dir / "secret.txt", "top secret")
        (workspace / "external_link").symlink_to(outside_dir)

        index = build_workspace_index(sandbox)
        all_paths = {f.relative_path.as_posix() for f in index.files}
        assert not any("external_link" in p for p in all_paths)
        assert not any("secret" in p for p in all_paths)


class TestWorkspaceFileProperties:
    def test_extractable_extensions(self, sandbox):
        index = build_workspace_index(sandbox)
        by_name = {f.name: f for f in index.files}
        assert by_name["coastal_chapter_notes.md"].is_extractable is True
        assert by_name["coastal_chapter_draft_v1.docx"].is_extractable is True
        assert by_name["typography_research.txt"].is_extractable is True

    def test_non_extractable_extension(self, sandbox):
        index = build_workspace_index(sandbox)
        by_name = {f.name: f for f in index.files}
        assert by_name["cover_photo.jpeg"].is_extractable is False

    def test_size_bytes_matches_actual_content(self, sandbox, workspace):
        index = build_workspace_index(sandbox)
        by_name = {f.name: f for f in index.files}
        expected_size = (workspace / "research_notes" / "coastal_chapter_notes.md").stat().st_size
        assert by_name["coastal_chapter_notes.md"].size_bytes == expected_size

    def test_absolute_path_is_contained_in_sandbox_root(self, sandbox):
        index = build_workspace_index(sandbox)
        for f in index.files:
            assert f.absolute_path.is_relative_to(sandbox.root)


class TestFindByName:
    def test_matches_multiple_similarly_named_files(self, sandbox):
        index = build_workspace_index(sandbox)
        matches = index.find_by_name("coastal")
        names = {f.name for f in matches}
        assert names == {"coastal_chapter_notes.md", "coastal_chapter_draft_v1.docx"}

    def test_case_insensitive(self, sandbox):
        index = build_workspace_index(sandbox)
        assert index.find_by_name("COASTAL") == index.find_by_name("coastal")

    def test_no_match_returns_empty_tuple(self, sandbox):
        index = build_workspace_index(sandbox)
        assert index.find_by_name("nonexistent_fragment_xyz") == ()


class TestDeterminism:
    def test_files_are_sorted_by_relative_path(self, sandbox):
        index = build_workspace_index(sandbox)
        paths = [f.relative_path.as_posix() for f in index.files]
        assert paths == sorted(paths)

    def test_root_matches_sandbox_root(self, sandbox):
        index = build_workspace_index(sandbox)
        assert index.root == sandbox.root


class TestCategoryEnumValues:
    def test_category_string_values_match_expected(self):
        # Locking these down: they'll be serialized into prompts/transcripts,
        # so an accidental rename should fail a test, not surface silently.
        assert FileCategory.SOURCE.value == "source"
        assert FileCategory.POLICY.value == "policy"
        assert FileCategory.OUTPUT.value == "output"