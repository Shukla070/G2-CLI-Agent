"""Adversarial tests for `lantern.security.sandbox`.

This is deliberately the most exhaustive test file in the project.
Nothing downstream of the sandbox can be trusted unless this module's
containment guarantee is airtight, so every category of escape
attempt from the architecture doc gets its own explicit test rather
than being folded into a generic "bad path" case.
"""

from __future__ import annotations

import sys

import pytest  # type: ignore

from lantern.security.sandbox import Reason, Sandbox, SandboxViolationError


@pytest.fixture
def workspace(tmp_path):
    """A small, realistic workspace: some real files, some real dirs."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "file.txt").write_text("hello")
    (tmp_path / "output").mkdir()
    return tmp_path


@pytest.fixture
def sandbox(workspace):
    return Sandbox.create(workspace)


# ---------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------


class TestSandboxCreate:
    def test_captures_resolved_root(self, workspace, sandbox):
        assert sandbox.root == workspace.resolve()

    def test_rejects_nonexistent_root(self, tmp_path):
        missing = tmp_path / "does_not_exist"
        with pytest.raises(NotADirectoryError):
            Sandbox.create(missing)

    def test_rejects_file_as_root(self, tmp_path):
        f = tmp_path / "not_a_dir.txt"
        f.write_text("x")
        with pytest.raises(NotADirectoryError):
            Sandbox.create(f)

    def test_accepts_string_root(self, workspace):
        sb = Sandbox.create(str(workspace))
        assert sb.root == workspace.resolve()


# ---------------------------------------------------------------------
# Ordinary valid paths
# ---------------------------------------------------------------------


class TestValidPaths:
    @pytest.mark.parametrize(
        "raw",
        [
            "notes/file.txt",
            "./notes/file.txt",
            ".",
            "new_dir/new_file.txt",  # doesn't exist yet — writes must work
            "deeply/nested/new/path.txt",  # multiple nonexistent components
            "file..with..dots.txt",  # ".." as a substring, not a path segment
            "my..project/file.txt",
            "output",
        ],
    )
    def test_resolves_inside_root(self, sandbox, workspace, raw):
        resolved = sandbox.resolve(raw)
        assert resolved.is_relative_to(workspace.resolve())

    def test_dot_resolves_to_root_itself(self, sandbox, workspace):
        assert sandbox.resolve(".") == workspace.resolve()

    def test_existing_file_resolves_to_itself(self, sandbox, workspace):
        resolved = sandbox.resolve("notes/file.txt")
        assert resolved == (workspace / "notes" / "file.txt").resolve()


# ---------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------


class TestMalformedInput:
    @pytest.mark.parametrize(
        "raw,reason",
        [
            ("", Reason.EMPTY),
            ("   ", Reason.EMPTY),
            ("notes/\x00file.txt", Reason.NULL_BYTE),
            ("notes\\file.txt", Reason.BACKSLASH),
            ("..\\..\\etc\\passwd", Reason.BACKSLASH),
        ],
    )
    def test_rejected_with_reason(self, sandbox, raw, reason):
        with pytest.raises(SandboxViolationError) as exc_info:
            sandbox.resolve(raw)
        assert exc_info.value.reason == reason


# ---------------------------------------------------------------------
# Absolute / drive-letter / UNC paths
# ---------------------------------------------------------------------


class TestAbsolutePaths:
    @pytest.mark.parametrize(
        "raw,reason",
        [
            ("/etc/passwd", Reason.ABSOLUTE_PATH),
            ("/", Reason.ABSOLUTE_PATH),
            ("/notes/file.txt", Reason.ABSOLUTE_PATH),
            ("C:/Windows/System32", Reason.DRIVE_LETTER),
            ("c:/windows", Reason.DRIVE_LETTER),
            ("D:/", Reason.DRIVE_LETTER),
            ("//server/share/file.txt", Reason.UNC_PATH),
        ],
    )
    def test_rejected_with_reason(self, sandbox, raw, reason):
        with pytest.raises(SandboxViolationError) as exc_info:
            sandbox.resolve(raw)
        assert exc_info.value.reason == reason


# ---------------------------------------------------------------------
# Home-directory expansion
# ---------------------------------------------------------------------


class TestHomeExpansion:
    @pytest.mark.parametrize(
        "raw",
        [
            "~",
            "~/x",
            "~root",  # tilde + username, no separator — easy to miss with an exact "~" check
            "~root/x",
            "sub/~snap/file.txt",  # mid-path segment, not just the first one
        ],
    )
    def test_rejected(self, sandbox, raw):
        with pytest.raises(SandboxViolationError) as exc_info:
            sandbox.resolve(raw)
        assert exc_info.value.reason == Reason.HOME_EXPANSION


# ---------------------------------------------------------------------
# Explicit ".." traversal
# ---------------------------------------------------------------------


class TestParentTraversal:
    @pytest.mark.parametrize(
        "raw",
        [
            "..",
            "../etc/passwd",
            "notes/../../etc/passwd",
            "notes/../output/x.txt",  # lands back INSIDE root after normalization —
            # rejected anyway, on syntax alone (see module docstring).
        ],
    )
    def test_rejected(self, sandbox, raw):
        with pytest.raises(SandboxViolationError) as exc_info:
            sandbox.resolve(raw)
        assert exc_info.value.reason == Reason.PARENT_TRAVERSAL


# ---------------------------------------------------------------------
# Symlink escapes — the case string-only validation always misses
# ---------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require admin privileges on Windows")
class TestSymlinkEscapes:
    def test_symlink_as_intermediate_segment_escapes(
        self, workspace, sandbox, tmp_path_factory
    ):
        outside_dir = tmp_path_factory.mktemp("outside")
        (outside_dir / "secret.txt").write_text("top secret")
        (workspace / "shortcut").symlink_to(outside_dir)

        with pytest.raises(SandboxViolationError) as exc_info:
            sandbox.resolve("shortcut/secret.txt")
        assert exc_info.value.reason == Reason.OUTSIDE_ROOT

    def test_symlink_as_final_target_escapes(self, workspace, sandbox, tmp_path_factory):
        outside_dir = tmp_path_factory.mktemp("outside")
        secret = outside_dir / "secret.txt"
        secret.write_text("top secret")
        (workspace / "link_to_secret").symlink_to(secret)

        with pytest.raises(SandboxViolationError) as exc_info:
            sandbox.resolve("link_to_secret")
        assert exc_info.value.reason == Reason.OUTSIDE_ROOT

    def test_symlink_pointing_inside_root_is_allowed(self, workspace, sandbox):
        target = workspace / "notes" / "file.txt"
        (workspace / "alias.txt").symlink_to(target)

        resolved = sandbox.resolve("alias.txt")
        assert resolved == target.resolve()

    def test_symlinked_directory_pointing_inside_root_is_allowed(self, workspace, sandbox):
        (workspace / "notes_alias").symlink_to(workspace / "notes")

        resolved = sandbox.resolve("notes_alias/file.txt")
        assert resolved == (workspace / "notes" / "file.txt").resolve()

    def test_error_message_does_not_leak_outside_path(
        self, workspace, sandbox, tmp_path_factory
    ):
        outside_dir = tmp_path_factory.mktemp("outside_leak_check")
        secret = outside_dir / "secret.txt"
        secret.write_text("top secret")
        (workspace / "leaky_link").symlink_to(secret)

        with pytest.raises(SandboxViolationError) as exc_info:
            sandbox.resolve("leaky_link")

        message = str(exc_info.value)
        assert str(secret) not in message
        assert str(outside_dir) not in message


# ---------------------------------------------------------------------
# Applies identically regardless of intended operation
# ---------------------------------------------------------------------


class TestOperationAgnostic:
    """`resolve()` doesn't know or care whether the caller intends a
    read, write, move, or delete — containment must hold identically
    for all of them. This is what the Action Gate relies on: it calls
    `resolve()` once, up front, before dispatching to any tool.
    """

    @pytest.mark.parametrize("raw", ["../etc/passwd", "/etc/passwd", "~/.ssh/id_rsa"])
    def test_escape_attempts_rejected_regardless_of_hypothetical_operation(
        self, sandbox, raw
    ):
        # The point being tested: there is no separate "read mode" vs
        # "write mode" leniency anywhere in this API.
        with pytest.raises(SandboxViolationError):
            sandbox.resolve(raw)