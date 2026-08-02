"""Workspace enumeration and classification for Lantern.

Walks the sandboxed workspace root once and classifies every file into
exactly one of three categories — SOURCE, POLICY, or OUTPUT — using the
conventions documented here and in `policy.py`. This module answers
"what files exist and what kind are they"; it does NOT extract document
text (`tools/extraction.py`, later) and does NOT enforce the sandbox
boundary itself (it walks *through* an already-constructed `Sandbox`,
trusting it for containment, and reuses its resolved root).

Classification (POLICY is checked first — it always wins on overlap,
e.g. `output/policies/POLICY_x.md` is POLICY, not OUTPUT, because a
safety-relevant classification should never be shadowed by an
organizational one):

  - POLICY: see `lantern.policy.is_policy_path`.
  - OUTPUT: any ancestor directory literally named "output"
    (case-insensitive), at any depth.
  - SOURCE: everything else.

Deliberately excluded from enumeration entirely — not even classified
as SOURCE:

  - Any path with a hidden (dot-prefixed) component, e.g. `.lantern/`
    (session storage, added in Phase 7), `.git/`, `.env`. This is
    tooling/config, not editorial content, and must never appear in
    document search results or count toward the assignment's
    source-document minimum.
  - Symlinks — both symlinked files and symlinked directories — are
    skipped during this *bulk* walk and never recursed into. This is a
    deliberate difference from `Sandbox.resolve()`, which correctly
    follows an individual symlink when a tool asks for one specific
    path by name. Bulk-walking through a symlinked directory is a
    distinct risk from resolving a single path: it would silently
    enumerate (and thereby disclose the existence of) content outside
    the workspace to the model, even though a later `resolve()` call on
    any one of those paths would still correctly refuse to open it.
    Simplest safe answer: don't walk through them at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from lantern.policy import is_policy_path
from lantern.security.sandbox import Sandbox

_OUTPUT_DIR_NAME = "output"

# Per the assignment: only these formats are in scope for text
# processing. Scanned documents, images, and OCR are explicitly out of
# scope, so anything else is enumerated (for `list_directory` honesty)
# but flagged as not extractable.
_EXTRACTABLE_EXTENSIONS = frozenset({".txt", ".md", ".docx", ".pdf"})


class FileCategory(str, Enum):
    """The three buckets every enumerated workspace file falls into."""

    SOURCE = "source"
    POLICY = "policy"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class WorkspaceFile:
    """One enumerated file, already classified."""

    relative_path: PurePosixPath  # workspace-relative, "/"-separated, stable across host OS
    absolute_path: Path  # fully resolved; safe to hand to tools directly
    category: FileCategory
    size_bytes: int

    @property
    def name(self) -> str:
        return self.relative_path.name

    @property
    def extension(self) -> str:
        return self.relative_path.suffix.lower()

    @property
    def is_extractable(self) -> bool:
        """True if a text-extraction backend exists (or will exist, as
        of Phase 3) for this file type."""
        return self.extension in _EXTRACTABLE_EXTENSIONS


@dataclass(frozen=True, slots=True)
class WorkspaceIndex:
    """A point-in-time snapshot of every classified file under the workspace root."""

    root: Path
    files: tuple[WorkspaceFile, ...]

    @property
    def sources(self) -> tuple[WorkspaceFile, ...]:
        return tuple(f for f in self.files if f.category is FileCategory.SOURCE)

    @property
    def policies(self) -> tuple[WorkspaceFile, ...]:
        return tuple(f for f in self.files if f.category is FileCategory.POLICY)

    @property
    def outputs(self) -> tuple[WorkspaceFile, ...]:
        return tuple(f for f in self.files if f.category is FileCategory.OUTPUT)

    def find_by_name(self, fragment: str) -> tuple[WorkspaceFile, ...]:
        """Case-insensitive substring match against filenames.

        This is the mechanism behind the assignment's "several files
        with similar names" scenario: a later `search_documents` tool
        calls this to surface every plausible candidate rather than
        silently picking one — which is what turns that scenario into a
        genuine MEDIUM decision instead of a guess.
        """
        needle = fragment.lower()
        return tuple(f for f in self.files if needle in f.name.lower())


def _classify(relative_path: PurePosixPath) -> FileCategory:
    if is_policy_path(relative_path):
        return FileCategory.POLICY
    ancestor_dirs = relative_path.parts[:-1]
    if any(part.lower() == _OUTPUT_DIR_NAME for part in ancestor_dirs):
        return FileCategory.OUTPUT
    return FileCategory.SOURCE


def build_workspace_index(sandbox: Sandbox) -> WorkspaceIndex:
    """Walk `sandbox.root` once and classify every non-hidden, non-symlink file.

    Entries are sorted by relative path for deterministic, diff-friendly
    output — useful both for tests and for recorded transcripts.
    """
    root = sandbox.root
    entries: list[WorkspaceFile] = []

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current_dir = Path(dirpath)

        # Prune hidden and symlinked directories in place so os.walk
        # never lists their contents or descends into them at all.
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".") and not (current_dir / d).is_symlink()
        ]

        for filename in filenames:
            if filename.startswith("."):
                continue

            absolute_path = current_dir / filename
            if absolute_path.is_symlink():
                continue

            try:
                size = absolute_path.stat().st_size
            except OSError:
                # Vanished between listing and stat (e.g. a concurrent
                # edit elsewhere) — skip rather than fail a whole index
                # build over one transient file.
                continue

            relative = PurePosixPath(absolute_path.relative_to(root).as_posix())
            entries.append(
                WorkspaceFile(
                    relative_path=relative,
                    absolute_path=absolute_path.resolve(),
                    category=_classify(relative),
                    size_bytes=size,
                )
            )

    entries.sort(key=lambda f: f.relative_path.as_posix())
    return WorkspaceIndex(root=root, files=tuple(entries))