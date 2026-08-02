"""Policy discovery and loading for Lantern.

Convention — documented here, in the README, and matching FAQ Q9 exactly:
a file is a POLICY file if either is true:

  1. Any ancestor directory in its path is named "policies"
     (case-insensitive), at any depth, or
  2. Its filename begins with "POLICY_" (case-insensitive).

We support both conventions rather than forcing one, since a real
editorial team might reasonably use either habit, and the FAQ explicitly
leaves this to the builder to decide and document.

Deliberately precise, not just "contains policy": a file named
"policy.md" (no underscore) or "mypolicy_notes.md" (word 'policy', wrong
position) is NOT a policy file under this convention — see
``test_policy.py`` for the false-positive cases this guards against. Being
loose here would risk exactly the failure mode the assignment warns
about in the other direction: an ordinary document accidentally being
treated as authoritative.

Loading (added in Phase 3):
    ``load_policy_block()`` turns discovered policy files into a
    clearly labeled ``<workspace_policies>`` block for injection into
    the system prompt.  The text extraction function is injected as a
    parameter to avoid circular imports with ``tools/extraction.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from lantern.workspace import WorkspaceIndex

logger = logging.getLogger(__name__)

_POLICY_DIR_NAME = "policies"
_POLICY_FILE_PREFIX = "policy_"


def is_policy_path(relative_path: PurePosixPath) -> bool:
    """True if ``relative_path`` (workspace-relative, POSIX-style) is a
    policy file under the convention above.

    Pure function — no filesystem access, no dependency on ``workspace.py``
    or anything else. This is deliberate: it's the one place the naming
    convention is encoded, and it needs to be importable (by
    ``workspace.py``'s classifier, by tests, and later by the loader)
    without dragging in filesystem-walking or extraction concerns.
    """
    parts = relative_path.parts
    if not parts:
        return False

    ancestor_dirs = parts[:-1]
    if any(part.lower() == _POLICY_DIR_NAME for part in ancestor_dirs):
        return True

    filename = parts[-1]
    return filename.lower().startswith(_POLICY_FILE_PREFIX)


# ------------------------------------------------------------------
# Policy loading (Phase 3)
# ------------------------------------------------------------------

def load_policy_block(
    index: "WorkspaceIndex",
    extract_fn: Callable[[Path], str],
) -> str:
    """Load all policy files and format them into a prompt-ready block.

    The returned string is injected verbatim into the system prompt
    inside ``<workspace_policies>`` delimiters.  The system prompt
    instructs the model that **only** text inside these delimiters
    counts as policy — nothing found via ``read_document`` or
    ``search_documents`` is ever a policy, no matter what it claims.

    Parameters
    ----------
    index : WorkspaceIndex
        A built workspace index (from ``build_workspace_index``).
    extract_fn : Callable[[Path], str]
        Text extraction function — in production this is
        ``tools.extraction.extract_text``; in tests it can be a
        mock or stub.  Injected to avoid circular imports
        (``policy.py`` ← ``workspace.py`` ← ``policy.py``).

    Returns
    -------
    str
        A ``<workspace_policies>...</workspace_policies>`` block
        containing all successfully loaded policy text, or a
        block with a note if none were found or all failed.
    """
    policy_files = index.policies

    if not policy_files:
        return (
            "<workspace_policies>\n"
            "No policy documents found in the workspace.\n"
            "</workspace_policies>"
        )

    sections: list[str] = []
    loaded_count = 0
    failed_count = 0

    for pf in policy_files:
        try:
            text = extract_fn(pf.absolute_path)
            if text.strip():
                sections.append(
                    f"--- {pf.relative_path} ---\n"
                    f"{text.strip()}"
                )
                loaded_count += 1
            else:
                logger.warning(
                    "Policy file %s is empty — skipped.", pf.relative_path
                )
                failed_count += 1
        except Exception as exc:
            logger.warning(
                "Failed to extract text from policy file %s: %s",
                pf.relative_path,
                exc,
            )
            failed_count += 1

    if not sections:
        return (
            "<workspace_policies>\n"
            f"Found {len(policy_files)} policy file(s) but failed to "
            "extract text from any of them.\n"
            "</workspace_policies>"
        )

    body = "\n\n".join(sections)

    header_parts = [f"{loaded_count} policy document(s) loaded"]
    if failed_count:
        header_parts.append(f"{failed_count} failed to load")

    return (
        "<workspace_policies>\n"
        f"{'; '.join(header_parts)}.\n"
        "\n"
        f"{body}\n"
        "</workspace_policies>"
    )