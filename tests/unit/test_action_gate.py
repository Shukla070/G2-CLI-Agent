"""Tests for the Action Gate — sandbox → classification → confidence.

The ordering tests are the most important: a sandbox failure must
produce REFUSE *before* confidence is consulted, so approval can
never override the boundary.
"""

from __future__ import annotations

import pytest

from lantern.confidence import ActionType, ConfidenceLevel
from lantern.security.action_gate import (
    ActionGate,
    GateOutcome,
    GateRequest,
    classify_action_types,
    resolve_workspace_path,
)
from lantern.security.sandbox import Reason, Sandbox, SandboxViolationError


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "draft.txt").write_text("hello")
    (tmp_path / "output").mkdir()
    (tmp_path / "policies").mkdir()
    (tmp_path / "policies" / "POLICY_embargo.md").write_text("embargo")
    return tmp_path


@pytest.fixture
def sandbox(workspace):
    return Sandbox.create(workspace)


@pytest.fixture
def gate(sandbox):
    return ActionGate(sandbox)


# ------------------------------------------------------------------
# resolve_workspace_path
# ------------------------------------------------------------------

class TestResolveWorkspacePath:
    def test_empty_string_is_root(self, sandbox):
        assert resolve_workspace_path(sandbox, "") == sandbox.root

    def test_dot_is_root(self, sandbox):
        assert resolve_workspace_path(sandbox, ".") == sandbox.root

    def test_relative_path_resolves(self, sandbox):
        p = resolve_workspace_path(sandbox, "notes/draft.txt")
        assert p.name == "draft.txt"


# ------------------------------------------------------------------
# classify_action_types
# ------------------------------------------------------------------

class TestClassifyActionTypes:
    def test_read_tool(self, sandbox):
        types = classify_action_types(
            "read_document",
            sandbox,
            {"path": sandbox.root / "notes" / "draft.txt"},
        )
        assert types == (ActionType.READ,)

    def test_write_new(self, sandbox, workspace):
        target = workspace / "output" / "new.txt"
        types = classify_action_types(
            "write_document",
            sandbox,
            {"path": target},
        )
        assert ActionType.WRITE_NEW in types
        assert ActionType.WRITE_OVERWRITE not in types

    def test_write_overwrite(self, sandbox, workspace):
        existing = workspace / "notes" / "draft.txt"
        types = classify_action_types(
            "write_document",
            sandbox,
            {"path": existing},
        )
        assert ActionType.WRITE_OVERWRITE in types

    def test_write_to_policy_file_adds_policy_target(self, sandbox, workspace):
        policy = workspace / "policies" / "POLICY_embargo.md"
        types = classify_action_types(
            "write_document",
            sandbox,
            {"path": policy},
        )
        assert ActionType.POLICY_TARGET in types
        assert ActionType.WRITE_OVERWRITE in types

    def test_delete_is_high_floor(self, sandbox, workspace):
        target = workspace / "notes" / "draft.txt"
        types = classify_action_types(
            "delete_file",
            sandbox,
            {"path": target},
        )
        assert types == (ActionType.DELETE,)

    def test_delete_policy_file(self, sandbox, workspace):
        policy = workspace / "policies" / "POLICY_embargo.md"
        types = classify_action_types(
            "delete_file",
            sandbox,
            {"path": policy},
        )
        assert ActionType.DELETE in types
        assert ActionType.POLICY_TARGET in types

    def test_move_no_collision(self, sandbox, workspace):
        src = workspace / "notes" / "draft.txt"
        dest = workspace / "output" / "moved.txt"
        types = classify_action_types(
            "move_or_rename_file",
            sandbox,
            {"path": src, "destination": dest},
        )
        assert ActionType.MOVE_RENAME in types
        assert ActionType.MOVE_COLLISION not in types

    def test_move_with_collision(self, sandbox, workspace):
        src = workspace / "notes" / "draft.txt"
        dest = workspace / "output"
        (dest / "existing.txt").write_text("x")
        types = classify_action_types(
            "move_or_rename_file",
            sandbox,
            {"path": src, "destination": dest / "existing.txt"},
        )
        assert ActionType.MOVE_COLLISION in types


# ------------------------------------------------------------------
# Sandbox short-circuits before confidence
# ------------------------------------------------------------------

class TestSandboxOrdering:
    def test_escape_attempt_refuses_before_confidence(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="read_document",
            model_confidence=ConfidenceLevel.NONE,
            paths={"path": "../outside.txt"},
        ))
        assert result.outcome is GateOutcome.REFUSE
        assert result.effective is ConfidenceLevel.REFUSE
        assert result.confidence is None
        assert result.sandbox_reason is Reason.PARENT_TRAVERSAL

    def test_delete_outside_root_refuses(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="delete_file",
            model_confidence=ConfidenceLevel.HIGH,
            paths={"path": "/etc/passwd"},
        ))
        assert result.outcome is GateOutcome.REFUSE
        assert result.confidence is None
        assert result.sandbox_reason is Reason.ABSOLUTE_PATH

    def test_valid_read_executes_with_none(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="read_document",
            paths={"path": "notes/draft.txt"},
        ))
        assert result.outcome is GateOutcome.EXECUTE
        assert result.effective is ConfidenceLevel.NONE
        assert result.action_types == (ActionType.READ,)
        assert "path" in result.resolved_paths


# ------------------------------------------------------------------
# Confidence merge through the gate
# ------------------------------------------------------------------

class TestConfidenceThroughGate:
    def test_delete_with_model_none_elevated_to_high(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="delete_file",
            model_confidence=ConfidenceLevel.NONE,
            paths={"path": "notes/draft.txt"},
        ))
        assert result.outcome is GateOutcome.AWAIT_INPUT
        assert result.effective is ConfidenceLevel.HIGH
        assert result.confidence is not None
        assert result.confidence.was_elevated is True

    def test_delete_with_model_high_not_elevated(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="delete_file",
            model_confidence=ConfidenceLevel.HIGH,
            paths={"path": "notes/draft.txt"},
        ))
        assert result.outcome is GateOutcome.AWAIT_INPUT
        assert result.effective is ConfidenceLevel.HIGH
        assert result.confidence.was_elevated is False

    def test_write_new_executes_on_none(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="write_document",
            model_confidence=ConfidenceLevel.NONE,
            paths={"path": "output/brand_new.txt"},
        ))
        assert result.outcome is GateOutcome.EXECUTE
        assert result.effective is ConfidenceLevel.NONE

    def test_overwrite_requires_high(self, gate, workspace):
        existing = workspace / "notes" / "draft.txt"
        result = gate.evaluate(GateRequest(
            tool_name="write_document",
            model_confidence=ConfidenceLevel.NONE,
            paths={"path": str(existing.relative_to(workspace)).replace("\\", "/")},
        ))
        assert result.effective is ConfidenceLevel.HIGH
        assert result.outcome is GateOutcome.AWAIT_INPUT

    def test_move_rename_medium(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="move_or_rename_file",
            model_confidence=ConfidenceLevel.NONE,
            paths={
                "path": "notes/draft.txt",
                "destination": "output/moved.txt",
            },
        ))
        assert result.effective is ConfidenceLevel.MEDIUM
        assert result.outcome is GateOutcome.AWAIT_INPUT

    def test_model_refuse_on_write(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="write_document",
            model_confidence=ConfidenceLevel.REFUSE,
            paths={"path": "output/new.txt"},
        ))
        assert result.outcome is GateOutcome.REFUSE
        assert result.effective is ConfidenceLevel.REFUSE

    def test_invalid_confidence_string_refuses(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="read_document",
            model_confidence="maybe",
            paths={"path": "notes/draft.txt"},
        ))
        assert result.outcome is GateOutcome.REFUSE
        assert result.confidence is None

    def test_confidence_from_string(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="delete_file",
            model_confidence="high",
            paths={"path": "notes/draft.txt"},
        ))
        assert result.effective is ConfidenceLevel.HIGH


# ------------------------------------------------------------------
# finalize_response (terminal tool)
# ------------------------------------------------------------------

class TestFinalizeResponse:
    def test_normal_finalize_executes(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="finalize_response",
            model_confidence=ConfidenceLevel.NONE,
        ))
        assert result.outcome is GateOutcome.EXECUTE
        assert result.action_types == (ActionType.FINALIZE,)

    def test_restricted_finalize_requires_high(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="finalize_response",
            model_confidence=ConfidenceLevel.NONE,
            exposes_restricted_content=True,
        ))
        assert result.effective is ConfidenceLevel.HIGH
        assert result.outcome is GateOutcome.AWAIT_INPUT

    def test_finalize_medium_presents_options(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="finalize_response",
            model_confidence=ConfidenceLevel.MEDIUM,
        ))
        assert result.outcome is GateOutcome.AWAIT_INPUT
        assert result.effective is ConfidenceLevel.MEDIUM


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

class TestEdgeCases:
    def test_unknown_tool_refuses(self, gate):
        result = gate.evaluate(GateRequest(tool_name="launch_missiles"))
        assert result.outcome is GateOutcome.REFUSE

    def test_list_directory_root(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="list_directory",
            paths={"path": ""},
        ))
        assert result.outcome is GateOutcome.EXECUTE
        assert result.resolved_paths["path"] == gate.sandbox.root

    def test_create_directory_none(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="create_directory",
            model_confidence=ConfidenceLevel.NONE,
            paths={"path": "output/new_folder"},
        ))
        assert result.outcome is GateOutcome.EXECUTE

    def test_policy_write_elevated_even_when_new(self, gate):
        result = gate.evaluate(GateRequest(
            tool_name="write_document",
            model_confidence=ConfidenceLevel.NONE,
            paths={"path": "policies/POLICY_new_rule.md"},
        ))
        assert result.effective is ConfidenceLevel.HIGH
        assert ActionType.POLICY_TARGET in result.action_types

    def test_sandbox_violation_does_not_leak_outside_path(self, gate):
        outside = gate.sandbox.root.parent / "secret_outside.txt"
        outside.write_text("secret")
        try:
            link = gate.sandbox.root / "escape_link.txt"
            link.symlink_to(outside)
        except OSError:
            pytest.skip("Symlinks not supported on this platform")

        result = gate.evaluate(GateRequest(
            tool_name="read_document",
            paths={"path": "escape_link.txt"},
        ))
        assert result.outcome is GateOutcome.REFUSE
        assert str(outside) not in result.message
