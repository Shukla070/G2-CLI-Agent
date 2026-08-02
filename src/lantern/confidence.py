"""Confidence engine for Lantern.

This module is the code-enforced safety net for the confidence system.
It implements three things:

1. ``ConfidenceLevel`` — a 5-value enum (NONE, LOW, MEDIUM, HIGH, REFUSE)
   that represents how strongly human input is needed before acting.

2. ``ActionType`` — an enum classifying what kind of operation is being
   attempted, used to look up the code-enforced floor.

3. ``resolve_confidence()`` — the merge function that computes
   ``effective = max(model_declared, code_floor)``.  The model can
   *raise* the bar (e.g. declare MEDIUM on something that has a NONE
   floor because the request is genuinely ambiguous) but can never
   *lower* a hard-coded floor like "delete = HIGH" or "overwrite = HIGH".

The floor table is the actual safety net — it's the most important
thing to test in this module, since it's what prevents the model from
silently downgrading a destructive action to NONE.

Design notes:
    * REFUSE is structurally distinct from NONE–HIGH. It never reaches
      the approval UI at all — it's the result of a sandbox violation
      or a hard policy prohibition.  The Action Gate produces REFUSE
      directly; the model should never declare it (if it does, it's
      treated as REFUSE, which is the safe direction).
    * The ordering NONE < LOW < MEDIUM < HIGH < REFUSE is deliberate
      and used by ``resolve_confidence`` for the ``max()`` merge.
    * This module is pure logic — zero I/O, zero LLM dependency,
      zero filesystem access.  It's tested with table-driven unit
      tests, not mocks.
"""

from __future__ import annotations

from enum import IntEnum, auto
from typing import NamedTuple


# ------------------------------------------------------------------
# Confidence levels
# ------------------------------------------------------------------

class ConfidenceLevel(IntEnum):
    """How strongly human input is needed before acting.

    Ordered from least to most restrictive.  The ``IntEnum`` base
    makes ``max()`` work naturally: ``max(NONE, HIGH) == HIGH``.
    """

    NONE = 0      # Clear, safe, execute silently
    LOW = 1       # Minor ambiguity — short confirmation or stated assumption
    MEDIUM = 2    # Multiple interpretations — present 2–3 options
    HIGH = 3      # Destructive/restricted — explain consequence, require decision
    REFUSE = 4    # Sandbox violation or hard policy block — no approval path


# ------------------------------------------------------------------
# Action types (for floor table lookup)
# ------------------------------------------------------------------

class ActionType(IntEnum):
    """Classification of what operation is being attempted.

    Used to look up the code-enforced confidence floor.  The Action
    Gate maps each tool call to one of these based on filesystem
    state (e.g. "does the target already exist?" determines
    WRITE_NEW vs WRITE_OVERWRITE).
    """

    # Read operations
    READ = auto()               # list_directory, search_documents, read_document

    # Write operations (graduated severity)
    CREATE_DIR = auto()         # create_directory
    WRITE_NEW = auto()          # write_document to a new (non-existent) path
    WRITE_OVERWRITE = auto()    # write_document to an existing path
    MOVE_RENAME = auto()        # move_or_rename_file (no collision)
    MOVE_COLLISION = auto()     # move_or_rename_file where destination exists
    DELETE = auto()             # delete_file

    # Policy-file targeting (any write/delete targeting a policy file)
    POLICY_TARGET = auto()      # any mutating operation on a policy file

    # Terminal message
    FINALIZE = auto()           # finalize_response (model's answer to user)
    FINALIZE_RESTRICTED = auto()  # finalize_response exposing embargoed content


# ------------------------------------------------------------------
# Floor table
# ------------------------------------------------------------------

# The code-enforced minimum confidence for each action type.
# The model can raise these but never lower them.
# This table is the actual safety net — test it exhaustively.

CONFIDENCE_FLOOR: dict[ActionType, ConfidenceLevel] = {
    # Read operations: no gate needed
    ActionType.READ: ConfidenceLevel.NONE,

    # Write operations: graduated severity
    ActionType.CREATE_DIR: ConfidenceLevel.NONE,
    ActionType.WRITE_NEW: ConfidenceLevel.NONE,       # Model-assessed, no hard floor
    ActionType.WRITE_OVERWRITE: ConfidenceLevel.HIGH,  # Destructive
    ActionType.MOVE_RENAME: ConfidenceLevel.MEDIUM,    # Could lose track of files
    ActionType.MOVE_COLLISION: ConfidenceLevel.HIGH,    # Destination exists — data loss
    ActionType.DELETE: ConfidenceLevel.HIGH,             # Always destructive

    # Policy-file targeting: always HIGH regardless of operation type
    ActionType.POLICY_TARGET: ConfidenceLevel.HIGH,

    # Terminal message
    ActionType.FINALIZE: ConfidenceLevel.NONE,          # Normal answers flow freely
    ActionType.FINALIZE_RESTRICTED: ConfidenceLevel.HIGH,  # Exposing embargoed content
}


# ------------------------------------------------------------------
# Confidence result
# ------------------------------------------------------------------

class ConfidenceResult(NamedTuple):
    """The outcome of resolving confidence for a single action.

    Attributes
    ----------
    effective : ConfidenceLevel
        The confidence level that actually applies — always
        ``max(model_declared, code_floor)``.
    model_declared : ConfidenceLevel
        What the model said (may be lower than effective).
    code_floor : ConfidenceLevel
        The code-enforced minimum for this action type.
    was_elevated : bool
        True if the code floor raised the model's declared level.
        Useful for logging/transcripts — makes it visible when
        the safety net intervened.
    """

    effective: ConfidenceLevel
    model_declared: ConfidenceLevel
    code_floor: ConfidenceLevel
    was_elevated: bool


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def parse_confidence_level(value: str | ConfidenceLevel) -> ConfidenceLevel:
    """Parse a model-supplied confidence value into ``ConfidenceLevel``.

    Accepts either an already-parsed enum member or a string matching
    one of the enum names (case-insensitive).  Used by the Action Gate
    and tool-schema validation when confidence arrives from the API as
    a string field.
    """
    if isinstance(value, ConfidenceLevel):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid confidence level: {value!r}")
    try:
        return ConfidenceLevel[value.strip().upper()]
    except KeyError as exc:
        raise ValueError(f"Invalid confidence level: {value!r}") from exc


def resolve_confidence(
    model_declared: ConfidenceLevel,
    action_type: ActionType,
) -> ConfidenceResult:
    """Merge the model's declared confidence with the code-enforced floor.

    Parameters
    ----------
    model_declared : ConfidenceLevel
        The confidence level the model declared in its tool call
        (via the ``confidence`` field on the tool input schema).
    action_type : ActionType
        The classified action type, determined by the Action Gate
        based on filesystem state (not trusted from the model).

    Returns
    -------
    ConfidenceResult
        Contains the effective level, both inputs, and whether
        the floor elevated the model's declaration.

    Examples
    --------
    >>> resolve_confidence(ConfidenceLevel.NONE, ActionType.DELETE)
    ConfidenceResult(effective=HIGH, model_declared=NONE, code_floor=HIGH, was_elevated=True)

    >>> resolve_confidence(ConfidenceLevel.HIGH, ActionType.WRITE_NEW)
    ConfidenceResult(effective=HIGH, model_declared=HIGH, code_floor=NONE, was_elevated=False)
    """
    return resolve_confidence_from_candidates(model_declared, (action_type,))


def resolve_confidence_from_candidates(
    model_declared: ConfidenceLevel,
    action_types: tuple[ActionType, ...] | list[ActionType],
) -> ConfidenceResult:
    """Merge model confidence with the highest floor among *action_types*.

    When several conditions apply at once (e.g. deleting a policy file
    triggers both ``DELETE`` and ``POLICY_TARGET``), the Action Gate
    passes every applicable type here and the strictest floor wins.
    """
    if not action_types:
        code_floor = ConfidenceLevel.NONE
    else:
        code_floor = max(get_floor(t) for t in action_types)

    effective = max(model_declared, code_floor)
    was_elevated = effective > model_declared

    return ConfidenceResult(
        effective=effective,
        model_declared=model_declared,
        code_floor=code_floor,
        was_elevated=was_elevated,
    )


def get_floor(action_type: ActionType) -> ConfidenceLevel:
    """Look up the code-enforced floor for an action type.

    Convenience function for callers that need the floor without
    doing a full resolve (e.g. for display or logging).
    """
    return CONFIDENCE_FLOOR.get(action_type, ConfidenceLevel.NONE)


def is_mutating(action_type: ActionType) -> bool:
    """Return True if the action type represents a mutating operation.

    Mutating operations are everything except READ and FINALIZE/
    FINALIZE_RESTRICTED.
    """
    return action_type not in {
        ActionType.READ,
        ActionType.FINALIZE,
        ActionType.FINALIZE_RESTRICTED,
    }
