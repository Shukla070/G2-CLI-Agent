"""Tests for `lantern.policy.is_policy_path`.

Pure-function tests, no filesystem needed — the point of keeping this
predicate dependency-free.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest  # pyright: ignore[reportMissingImports]

from lantern.policy import is_policy_path


class TestPolicyDirConvention:
    @pytest.mark.parametrize(
        "raw",
        [
            "policies/embargo.md",
            "Policies/embargo.md",
            "POLICIES/embargo.md",
            "reference_material/policies/citation.md",  # nested, not top-level
            "a/b/policies/c/d.md",  # deeply nested ancestor
        ],
    )
    def test_ancestor_dir_named_policies_matches(self, raw):
        assert is_policy_path(PurePosixPath(raw)) is True

    def test_dir_named_similarly_but_not_exactly_does_not_match(self):
        # "policyfolder" contains the word but isn't the convention.
        assert is_policy_path(PurePosixPath("policyfolder/x.md")) is False


class TestPolicyPrefixConvention:
    @pytest.mark.parametrize(
        "raw",
        [
            "POLICY_embargo.md",
            "policy_embargo.md",
            "Policy_Embargo.MD",
            "notes/POLICY_citation.txt",  # prefix rule applies regardless of directory
        ],
    )
    def test_filename_prefix_matches(self, raw):
        assert is_policy_path(PurePosixPath(raw)) is True

    @pytest.mark.parametrize(
        "raw",
        [
            "policy.md",  # no underscore — not the convention
            "mypolicy_notes.md",  # word appears, wrong position
            "notes/policyish.md",
        ],
    )
    def test_similar_but_non_matching_filenames_are_not_policy(self, raw):
        assert is_policy_path(PurePosixPath(raw)) is False


class TestOrdinaryFiles:
    @pytest.mark.parametrize(
        "raw",
        [
            "research_notes/coastal_chapter_notes.md",
            "drafts/coastal_chapter_draft_v1.docx",
            "correspondence/author_notes_j_alvarez.txt",
            "output/editors_note.md",
            "readme.md",
        ],
    )
    def test_ordinary_files_are_not_policy(self, raw):
        assert is_policy_path(PurePosixPath(raw)) is False


class TestEdgeCases:
    def test_empty_path_is_not_policy(self):
        assert is_policy_path(PurePosixPath()) is False

    def test_single_segment_policy_filename(self):
        assert is_policy_path(PurePosixPath("POLICY_root_level.md")) is True

    def test_single_segment_ordinary_filename(self):
        assert is_policy_path(PurePosixPath("notes.md")) is False