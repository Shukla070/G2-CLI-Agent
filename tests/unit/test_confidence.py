"""Tests for the confidence engine — the code-enforced safety net.

This is the second most exhaustive test file after test_sandbox.py.
The floor table is the actual safety net that prevents the model from
silently downgrading destructive actions, so every entry in the table
gets explicit tests, plus adversarial cases where the model tries to
declare lower confidence than the floor allows.

Test organization:
    * TestConfidenceLevelOrdering — the IntEnum ordering is correct
    * TestFloorTable — every entry in the table has the right value
    * TestResolveConfidence — the merge function works correctly
    * TestFloorCannotBeUndercut — adversarial: model declares lower
    * TestModelCanRaise — model can declare higher than floor
    * TestEdgeCases — REFUSE, missing action types, etc.
"""

from __future__ import annotations

import pytest

from lantern.confidence import (
    ActionType,
    ConfidenceLevel,
    ConfidenceResult,
    CONFIDENCE_FLOOR,
    get_floor,
    is_mutating,
    parse_confidence_level,
    resolve_confidence,
    resolve_confidence_from_candidates,
)


# ------------------------------------------------------------------
# ConfidenceLevel ordering
# ------------------------------------------------------------------

class TestConfidenceLevelOrdering:
    """The IntEnum ordering must be NONE < LOW < MEDIUM < HIGH < REFUSE
    for max() to work correctly as the merge function."""

    def test_none_is_lowest(self) -> None:
        assert ConfidenceLevel.NONE < ConfidenceLevel.LOW

    def test_low_less_than_medium(self) -> None:
        assert ConfidenceLevel.LOW < ConfidenceLevel.MEDIUM

    def test_medium_less_than_high(self) -> None:
        assert ConfidenceLevel.MEDIUM < ConfidenceLevel.HIGH

    def test_high_less_than_refuse(self) -> None:
        assert ConfidenceLevel.HIGH < ConfidenceLevel.REFUSE

    def test_full_ordering(self) -> None:
        levels = [
            ConfidenceLevel.NONE,
            ConfidenceLevel.LOW,
            ConfidenceLevel.MEDIUM,
            ConfidenceLevel.HIGH,
            ConfidenceLevel.REFUSE,
        ]
        assert levels == sorted(levels)

    def test_max_of_none_and_high_is_high(self) -> None:
        assert max(ConfidenceLevel.NONE, ConfidenceLevel.HIGH) == ConfidenceLevel.HIGH

    def test_max_of_refuse_and_anything_is_refuse(self) -> None:
        for level in ConfidenceLevel:
            assert max(level, ConfidenceLevel.REFUSE) == ConfidenceLevel.REFUSE


# ------------------------------------------------------------------
# Floor table completeness and correctness
# ------------------------------------------------------------------

class TestFloorTable:
    """Every ActionType must have an entry in the floor table with the
    correct value per the architecture document."""

    def test_every_action_type_has_a_floor(self) -> None:
        """No ActionType should be missing from the floor table."""
        for action_type in ActionType:
            assert action_type in CONFIDENCE_FLOOR, (
                f"{action_type.name} is missing from CONFIDENCE_FLOOR"
            )

    # -- Read operations: all NONE --

    def test_read_floor_is_none(self) -> None:
        assert CONFIDENCE_FLOOR[ActionType.READ] == ConfidenceLevel.NONE

    # -- Write operations: graduated --

    def test_create_dir_floor_is_none(self) -> None:
        assert CONFIDENCE_FLOOR[ActionType.CREATE_DIR] == ConfidenceLevel.NONE

    def test_write_new_floor_is_none(self) -> None:
        """Writing a new file has no hard floor — model-assessed."""
        assert CONFIDENCE_FLOOR[ActionType.WRITE_NEW] == ConfidenceLevel.NONE

    def test_write_overwrite_floor_is_high(self) -> None:
        """Overwriting an existing file is destructive — always HIGH."""
        assert CONFIDENCE_FLOOR[ActionType.WRITE_OVERWRITE] == ConfidenceLevel.HIGH

    def test_move_rename_floor_is_medium(self) -> None:
        assert CONFIDENCE_FLOOR[ActionType.MOVE_RENAME] == ConfidenceLevel.MEDIUM

    def test_move_collision_floor_is_high(self) -> None:
        """Move where destination exists — data loss risk."""
        assert CONFIDENCE_FLOOR[ActionType.MOVE_COLLISION] == ConfidenceLevel.HIGH

    def test_delete_floor_is_high(self) -> None:
        """Delete is always destructive — always HIGH."""
        assert CONFIDENCE_FLOOR[ActionType.DELETE] == ConfidenceLevel.HIGH

    # -- Policy targeting --

    def test_policy_target_floor_is_high(self) -> None:
        """Any mutation on a policy file — always HIGH."""
        assert CONFIDENCE_FLOOR[ActionType.POLICY_TARGET] == ConfidenceLevel.HIGH

    # -- Finalize --

    def test_finalize_floor_is_none(self) -> None:
        """Normal answers flow freely."""
        assert CONFIDENCE_FLOOR[ActionType.FINALIZE] == ConfidenceLevel.NONE

    def test_finalize_restricted_floor_is_high(self) -> None:
        """Exposing embargoed content requires HIGH."""
        assert CONFIDENCE_FLOOR[ActionType.FINALIZE_RESTRICTED] == ConfidenceLevel.HIGH


# ------------------------------------------------------------------
# resolve_confidence — the merge function
# ------------------------------------------------------------------

class TestResolveConfidence:
    """The core merge: effective = max(model_declared, code_floor)."""

    def test_returns_confidence_result(self) -> None:
        result = resolve_confidence(ConfidenceLevel.NONE, ActionType.READ)
        assert isinstance(result, ConfidenceResult)

    def test_result_fields(self) -> None:
        result = resolve_confidence(ConfidenceLevel.LOW, ActionType.WRITE_NEW)
        assert result.effective == ConfidenceLevel.LOW
        assert result.model_declared == ConfidenceLevel.LOW
        assert result.code_floor == ConfidenceLevel.NONE
        assert result.was_elevated is False

    def test_model_and_floor_agree(self) -> None:
        """When model declares the same as the floor, no elevation."""
        result = resolve_confidence(ConfidenceLevel.HIGH, ActionType.DELETE)
        assert result.effective == ConfidenceLevel.HIGH
        assert result.was_elevated is False

    def test_model_above_floor(self) -> None:
        """Model can declare higher than the floor — not elevated."""
        result = resolve_confidence(ConfidenceLevel.HIGH, ActionType.WRITE_NEW)
        assert result.effective == ConfidenceLevel.HIGH
        assert result.code_floor == ConfidenceLevel.NONE
        assert result.was_elevated is False

    def test_model_below_floor(self) -> None:
        """Model declares lower — floor takes over, was_elevated=True."""
        result = resolve_confidence(ConfidenceLevel.NONE, ActionType.DELETE)
        assert result.effective == ConfidenceLevel.HIGH
        assert result.model_declared == ConfidenceLevel.NONE
        assert result.code_floor == ConfidenceLevel.HIGH
        assert result.was_elevated is True


# ------------------------------------------------------------------
# Floor cannot be undercut — adversarial cases
# ------------------------------------------------------------------

class TestFloorCannotBeUndercut:
    """The most important safety tests: the model declares NONE on
    every destructive action type, and the floor table overrides it
    every time."""

    def test_model_declares_none_on_delete(self) -> None:
        result = resolve_confidence(ConfidenceLevel.NONE, ActionType.DELETE)
        assert result.effective == ConfidenceLevel.HIGH

    def test_model_declares_low_on_delete(self) -> None:
        result = resolve_confidence(ConfidenceLevel.LOW, ActionType.DELETE)
        assert result.effective == ConfidenceLevel.HIGH

    def test_model_declares_medium_on_delete(self) -> None:
        result = resolve_confidence(ConfidenceLevel.MEDIUM, ActionType.DELETE)
        assert result.effective == ConfidenceLevel.HIGH

    def test_model_declares_none_on_overwrite(self) -> None:
        result = resolve_confidence(ConfidenceLevel.NONE, ActionType.WRITE_OVERWRITE)
        assert result.effective == ConfidenceLevel.HIGH

    def test_model_declares_low_on_overwrite(self) -> None:
        result = resolve_confidence(ConfidenceLevel.LOW, ActionType.WRITE_OVERWRITE)
        assert result.effective == ConfidenceLevel.HIGH

    def test_model_declares_none_on_move_collision(self) -> None:
        result = resolve_confidence(ConfidenceLevel.NONE, ActionType.MOVE_COLLISION)
        assert result.effective == ConfidenceLevel.HIGH

    def test_model_declares_none_on_policy_target(self) -> None:
        result = resolve_confidence(ConfidenceLevel.NONE, ActionType.POLICY_TARGET)
        assert result.effective == ConfidenceLevel.HIGH

    def test_model_declares_none_on_finalize_restricted(self) -> None:
        result = resolve_confidence(ConfidenceLevel.NONE, ActionType.FINALIZE_RESTRICTED)
        assert result.effective == ConfidenceLevel.HIGH

    def test_model_declares_none_on_move_rename(self) -> None:
        result = resolve_confidence(ConfidenceLevel.NONE, ActionType.MOVE_RENAME)
        assert result.effective == ConfidenceLevel.MEDIUM

    def test_model_declares_low_on_move_rename(self) -> None:
        """LOW < MEDIUM floor, so floor wins."""
        result = resolve_confidence(ConfidenceLevel.LOW, ActionType.MOVE_RENAME)
        assert result.effective == ConfidenceLevel.MEDIUM

    def test_elevation_flag_set_on_all_undercuts(self) -> None:
        """Every undercut case must have was_elevated=True."""
        undercut_cases = [
            (ConfidenceLevel.NONE, ActionType.DELETE),
            (ConfidenceLevel.LOW, ActionType.DELETE),
            (ConfidenceLevel.NONE, ActionType.WRITE_OVERWRITE),
            (ConfidenceLevel.NONE, ActionType.POLICY_TARGET),
            (ConfidenceLevel.NONE, ActionType.MOVE_RENAME),
        ]
        for model_declared, action_type in undercut_cases:
            result = resolve_confidence(model_declared, action_type)
            assert result.was_elevated, (
                f"Expected was_elevated=True for "
                f"model={model_declared.name}, action={action_type.name}"
            )


# ------------------------------------------------------------------
# Model can raise — the model is allowed to be MORE cautious
# ------------------------------------------------------------------

class TestModelCanRaise:
    """The model should be able to raise confidence above the floor
    for genuinely ambiguous situations."""

    def test_model_raises_read_to_low(self) -> None:
        """A read with ambiguous intent — model says LOW."""
        result = resolve_confidence(ConfidenceLevel.LOW, ActionType.READ)
        assert result.effective == ConfidenceLevel.LOW
        assert result.was_elevated is False

    def test_model_raises_write_new_to_medium(self) -> None:
        """Model detects ambiguity in a new-file write."""
        result = resolve_confidence(ConfidenceLevel.MEDIUM, ActionType.WRITE_NEW)
        assert result.effective == ConfidenceLevel.MEDIUM
        assert result.was_elevated is False

    def test_model_raises_write_new_to_high(self) -> None:
        """Model detects serious risk even in a new file."""
        result = resolve_confidence(ConfidenceLevel.HIGH, ActionType.WRITE_NEW)
        assert result.effective == ConfidenceLevel.HIGH
        assert result.was_elevated is False

    def test_model_raises_create_dir_to_medium(self) -> None:
        result = resolve_confidence(ConfidenceLevel.MEDIUM, ActionType.CREATE_DIR)
        assert result.effective == ConfidenceLevel.MEDIUM

    def test_model_raises_finalize_to_medium(self) -> None:
        """Model wants to present options in a normal answer."""
        result = resolve_confidence(ConfidenceLevel.MEDIUM, ActionType.FINALIZE)
        assert result.effective == ConfidenceLevel.MEDIUM


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

class TestEdgeCases:
    def test_refuse_from_model_stays_refuse(self) -> None:
        """If the model declares REFUSE, it stays REFUSE regardless of floor."""
        result = resolve_confidence(ConfidenceLevel.REFUSE, ActionType.WRITE_NEW)
        assert result.effective == ConfidenceLevel.REFUSE

    def test_refuse_plus_high_floor_is_refuse(self) -> None:
        """REFUSE > HIGH, so REFUSE wins even against HIGH floor."""
        result = resolve_confidence(ConfidenceLevel.REFUSE, ActionType.DELETE)
        assert result.effective == ConfidenceLevel.REFUSE

    def test_same_level_not_elevated(self) -> None:
        """When model and floor match exactly, was_elevated is False."""
        result = resolve_confidence(ConfidenceLevel.HIGH, ActionType.DELETE)
        assert result.was_elevated is False

    def test_none_on_none_floor(self) -> None:
        """Both NONE — straightforward pass-through."""
        result = resolve_confidence(ConfidenceLevel.NONE, ActionType.READ)
        assert result.effective == ConfidenceLevel.NONE
        assert result.was_elevated is False


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------

class TestGetFloor:
    def test_returns_correct_floor(self) -> None:
        assert get_floor(ActionType.DELETE) == ConfidenceLevel.HIGH

    def test_read_floor(self) -> None:
        assert get_floor(ActionType.READ) == ConfidenceLevel.NONE


class TestIsMutating:
    def test_read_is_not_mutating(self) -> None:
        assert is_mutating(ActionType.READ) is False

    def test_finalize_is_not_mutating(self) -> None:
        assert is_mutating(ActionType.FINALIZE) is False

    def test_finalize_restricted_is_not_mutating(self) -> None:
        assert is_mutating(ActionType.FINALIZE_RESTRICTED) is False

    @pytest.mark.parametrize("action_type", [
        ActionType.CREATE_DIR,
        ActionType.WRITE_NEW,
        ActionType.WRITE_OVERWRITE,
        ActionType.MOVE_RENAME,
        ActionType.MOVE_COLLISION,
        ActionType.DELETE,
        ActionType.POLICY_TARGET,
    ])
    def test_write_operations_are_mutating(self, action_type: ActionType) -> None:
        assert is_mutating(action_type) is True


# ------------------------------------------------------------------
# parse_confidence_level
# ------------------------------------------------------------------

class TestParseConfidenceLevel:
    def test_accepts_enum_member(self) -> None:
        assert parse_confidence_level(ConfidenceLevel.HIGH) is ConfidenceLevel.HIGH

    def test_parses_uppercase_string(self) -> None:
        assert parse_confidence_level("HIGH") is ConfidenceLevel.HIGH

    def test_parses_lowercase_string(self) -> None:
        assert parse_confidence_level("medium") is ConfidenceLevel.MEDIUM

    def test_strips_whitespace(self) -> None:
        assert parse_confidence_level("  none  ") is ConfidenceLevel.NONE

    def test_rejects_invalid_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid confidence level"):
            parse_confidence_level("maybe")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError):
            parse_confidence_level("")


# ------------------------------------------------------------------
# resolve_confidence_from_candidates
# ------------------------------------------------------------------

class TestResolveConfidenceFromCandidates:
    def test_empty_candidates_uses_none_floor(self) -> None:
        result = resolve_confidence_from_candidates(ConfidenceLevel.LOW, ())
        assert result.effective == ConfidenceLevel.LOW
        assert result.code_floor == ConfidenceLevel.NONE

    def test_single_candidate_matches_resolve_confidence(self) -> None:
        single = resolve_confidence(ConfidenceLevel.NONE, ActionType.DELETE)
        multi = resolve_confidence_from_candidates(
            ConfidenceLevel.NONE, (ActionType.DELETE,)
        )
        assert single == multi

    def test_delete_on_policy_file_uses_high_floor(self) -> None:
        result = resolve_confidence_from_candidates(
            ConfidenceLevel.NONE,
            (ActionType.DELETE, ActionType.POLICY_TARGET),
        )
        assert result.effective == ConfidenceLevel.HIGH
        assert result.was_elevated is True

    def test_move_rename_on_policy_file_elevates_to_high(self) -> None:
        """MOVE_RENAME floor is MEDIUM, POLICY_TARGET is HIGH — HIGH wins."""
        result = resolve_confidence_from_candidates(
            ConfidenceLevel.NONE,
            (ActionType.MOVE_RENAME, ActionType.POLICY_TARGET),
        )
        assert result.effective == ConfidenceLevel.HIGH
