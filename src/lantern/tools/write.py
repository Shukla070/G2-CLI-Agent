"""Write tool functions for Lantern.

Four mutating tools — ``create_directory``, ``write_document``,
``move_or_rename_file``, ``delete_file`` — each accepting
*already-resolved* ``Path`` objects produced by the Action Gate.

These functions deliberately do **not** re-run sandbox checks or
confidence logic.  Callers must route every mutating tool call through
``ActionGate.evaluate()`` first and only invoke these when the gate
returns ``GateOutcome.EXECUTE``, or after explicit human approval for
a paused LOW/MEDIUM/HIGH decision.

Overwrite detection is likewise **not** done here — the gate checks
``path.exists()`` before confidence is computed; these tools simply
perform the filesystem operation they are asked to perform.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Plain-text formats Lantern can create or overwrite.  .docx / .pdf are
# read-only in v1 — writing them would need separate binary backends.
_WRITABLE_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md"})


def create_directory(path: Path, *, path_label: str | None = None) -> str:
    """Create a directory at *path* (and any missing parents).

    Parameters
    ----------
    path : Path
        Fully resolved, sandbox-contained directory path.
    path_label : str, optional
        Workspace-relative label for messages (defaults to *path*).

    Returns
    -------
    str
        Success or error message for the tool_result.
    """
    label = _display_label(path, path_label)

    if path.exists():
        if path.is_dir():
            return f"Directory '{label}' already exists."
        return f"Error: '{label}' exists and is not a directory."

    try:
        path.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return f"Error creating directory '{label}': {exc}"

    return f"Created directory '{label}'."


def write_document(
    path: Path,
    content: str,
    *,
    path_label: str | None = None,
) -> str:
    """Write *content* to a ``.txt`` or ``.md`` file at *path*.

    Creates parent directories if they do not exist.  Overwrites an
    existing file when *path* already exists (the gate should already
    have required appropriate confidence before this is called).
    """
    label = _display_label(path, path_label)

    if path.is_dir():
        return f"Error: '{label}' is a directory, not a file."

    ext = path.suffix.lower()
    if ext not in _WRITABLE_EXTENSIONS:
        supported = ", ".join(sorted(_WRITABLE_EXTENSIONS))
        return (
            f"Error: cannot write to '{ext}' format. "
            f"Supported write formats: {supported}"
        )

    existed = path.exists()

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing '{label}': {exc}"

    word_count = len(content.split())
    verb = "Overwrote" if existed else "Created"
    return f"{verb} '{label}' ({word_count} words)."


def move_or_rename_file(
    source: Path,
    destination: Path,
    *,
    source_label: str | None = None,
    destination_label: str | None = None,
) -> str:
    """Move or rename *source* to *destination*.

    If *destination* already exists it is replaced (the gate should
    have classified this as a collision and required HIGH approval).
    """
    src_label = _display_label(source, source_label)
    dest_label = _display_label(destination, destination_label)

    if not source.exists():
        return f"Error: source '{src_label}' does not exist."

    if source.is_dir():
        return (
            f"Error: '{src_label}' is a directory. "
            "Only file moves are supported."
        )

    if destination.is_dir():
        return f"Error: destination '{dest_label}' is a directory."

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        shutil.move(str(source), str(destination))
    except OSError as exc:
        return f"Error moving '{src_label}' to '{dest_label}': {exc}"

    return f"Moved '{src_label}' → '{dest_label}'."


def delete_file(path: Path, *, path_label: str | None = None) -> str:
    """Delete the file at *path*."""
    label = _display_label(path, path_label)

    if not path.exists():
        return f"Error: '{label}' does not exist."

    if path.is_dir():
        return (
            f"Error: '{label}' is a directory. "
            "Only files can be deleted with delete_file."
        )

    try:
        path.unlink()
    except OSError as exc:
        return f"Error deleting '{label}': {exc}"

    return f"Deleted '{label}'."


def _display_label(path: Path, path_label: str | None) -> str:
    return path_label if path_label is not None else path.as_posix()
