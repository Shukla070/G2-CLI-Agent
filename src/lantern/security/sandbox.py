"""Filesystem sandbox enforcement for Lantern.

This module is the single source of truth for "is this path allowed".
It is deliberately the most conservative, most heavily tested module in
the system: zero model discretion, zero convenience trade-offs.

Design invariants (see architecture doc, §10 "Security Sandbox"):

1. The workspace root is captured once, resolved to its real path, and
   treated as immutable for the life of the process.
2. All tool-supplied paths are workspace-relative by convention. A
   leading "/" is never treated as filesystem root.
3. Obviously malformed or suspicious input (null bytes, backslashes,
   drive letters, UNC paths, home-dir expansion, explicit ".."
   segments) is rejected outright, on syntax alone — we do not try to
   reason about where such a path *would* land after normalization.
   Even a ".." path that would land back inside the root
   (e.g. "notes/../output/x.txt") is rejected, on principle: no
   legitimate tool call needs ".." syntax.
4. Symlinks are fully resolved (Path.resolve()) BEFORE containment is
   checked, so a symlink that points outside the workspace is caught
   whether it's an intermediate path segment or the final target.
5. Any failure raises `SandboxViolationError` carrying a stable,
   machine-checkable `reason` code. Callers (the Action Gate) must
   treat this as an unconditional REFUSE — a refusal that happens
   *before* the confidence/approval system is even consulted. That
   ordering, not prompt wording, is what makes "a user confirmation
   cannot override the sandbox" true.

Residual risk (accepted, documented in architecture doc §16): a
TOCTOU window between `resolve()` returning a path and the caller's
actual filesystem operation using it (e.g. a symlink swapped in
between). Out of scope for a local, single-user CLI tool; real
protection here (chroot/containers) would be disproportionate.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path


class Reason(str, Enum):
    """Stable, machine-checkable rejection reasons.

    Kept as short codes (rather than only prose messages) so callers
    and tests can assert on *why* a path was rejected, not just *that*
    it was. String-valued so they serialize cleanly into transcripts.
    """

    EMPTY = "empty_path"
    NULL_BYTE = "null_byte"
    BACKSLASH = "backslash_separator"
    UNC_PATH = "unc_path"
    DRIVE_LETTER = "drive_letter"
    ABSOLUTE_PATH = "absolute_path"
    HOME_EXPANSION = "home_expansion"
    PARENT_TRAVERSAL = "parent_traversal"
    OUTSIDE_ROOT = "outside_root"


class SandboxViolationError(Exception):
    """Raised whenever a path fails validation or containment checks.

    The Action Gate converts every instance of this into an
    unconditional REFUSE. The message is intentionally generic and
    never includes a resolved outside-root path, so a violation
    report can't be used to probe the filesystem layout outside the
    workspace.
    """

    def __init__(self, reason: Reason, message: str) -> None:
        self.reason = reason
        super().__init__(message)


# Matches a leading Windows-style drive letter written with forward
# slashes, e.g. "C:/Windows" or "c:/windows" — the case a naive
# "starts with /" check would miss entirely.
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")


class Sandbox:
    """An immutable workspace boundary.

    Construct once via `Sandbox.create(root)`, as early as possible in
    the process's life (before any tool, prompt, or session logic
    runs). Every tool call that touches the filesystem must call
    `resolve()` on its path argument(s) before doing anything else —
    tools themselves must never re-implement or bypass this check.
    """

    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        # Not part of the public API on purpose — use Sandbox.create()
        # so construction always goes through validation.
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    @classmethod
    def create(cls, root: Path | str) -> "Sandbox":
        """Capture and validate the workspace root.

        Resolves symlinks in the root itself so every later comparison
        is resolved-path-to-resolved-path. Raises `NotADirectoryError`
        (a configuration error, distinct from `SandboxViolationError`,
        which is reserved for per-call path violations) if the root
        does not exist or is not a directory.
        """
        resolved_root = Path(root).resolve(strict=False)
        if not resolved_root.is_dir():
            raise NotADirectoryError(
                f"Workspace root does not exist or is not a directory: {root!r}"
            )
        return cls(resolved_root)

    def resolve(self, raw_path: str) -> Path:
        """Validate `raw_path` and return its fully resolved, contained form.

        Raises `SandboxViolationError` on any violation. Never returns
        a path outside `self.root`.
        """
        self._reject_malformed(raw_path)
        self._reject_unsafe_syntax(raw_path)

        candidate = (self._root / raw_path).resolve(strict=False)

        if not self._is_contained(candidate):
            raise SandboxViolationError(
                Reason.OUTSIDE_ROOT,
                "Path resolves outside the workspace boundary.",
            )

        return candidate

    # -- internal validation steps, kept separate for readability and
    #    so each rejection reason is easy to unit-test in isolation --

    def _reject_malformed(self, raw_path: str) -> None:
        if raw_path is None or not raw_path.strip():
            raise SandboxViolationError(Reason.EMPTY, "Path must not be empty.")
        if "\x00" in raw_path:
            raise SandboxViolationError(
                Reason.NULL_BYTE, "Path must not contain null bytes."
            )
        if "\\" in raw_path:
            raise SandboxViolationError(
                Reason.BACKSLASH, "Path must use forward slashes only."
            )

    def _reject_unsafe_syntax(self, raw_path: str) -> None:
        # Order matters for the specificity of the reported reason,
        # not for security: all of these are hard rejections.
        if raw_path.startswith("//"):
            raise SandboxViolationError(
                Reason.UNC_PATH, "UNC-style paths are not allowed."
            )
        if _DRIVE_LETTER_RE.match(raw_path):
            raise SandboxViolationError(
                Reason.DRIVE_LETTER, "Drive-letter paths are not allowed."
            )
        if raw_path.startswith("/"):
            raise SandboxViolationError(
                Reason.ABSOLUTE_PATH,
                "Absolute paths are not allowed; all paths are workspace-relative.",
            )

        for segment in raw_path.split("/"):
            if segment == "..":
                raise SandboxViolationError(
                    Reason.PARENT_TRAVERSAL,
                    "Parent-directory traversal ('..') is not allowed.",
                )
            if segment.startswith("~"):
                # Catches bare "~" AND "~root"-style segments (tilde +
                # username, no separator) — a shell would expand
                # either into a home directory just as readily.
                raise SandboxViolationError(
                    Reason.HOME_EXPANSION,
                    "Home-directory references are not allowed.",
                )

    def _is_contained(self, candidate: Path) -> bool:
        try:
            candidate.relative_to(self._root)
        except ValueError:
            return False
        return True
